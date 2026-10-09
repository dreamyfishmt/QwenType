"""QwenType entry point and the recording state machine.

Threads: keyboard hook, PortAudio callback, asyncio loop (WebSocket/HTTP) and
injection worker. They only talk to the UI thread through Qt signals.
"""

from __future__ import annotations

import argparse
import enum
import logging
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QLockFile, QObject, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from . import __version__, llm, win32
from .aio import AsyncRunner
from .asr import AsrBackend, AsrSession, Qwen3StreamingBackend
from .audio import AudioCapture
from .capsule import CapsuleWindow
from .dialogs import AsrServerDialog, LlmSettingsDialog
from .hotkey import RightCtrlHook
from .injector import InjectionError, inject_text
from .qtasync import run_async
from .settings import Settings, settings_dir
from .tray import Tray, app_icon

log = logging.getLogger("qwentype")

SHOW_DELAY_MS = 150  # capsule appears only after the key is held this long
MIN_RECORD_S = 0.3  # shorter recordings are cancelled silently


class State(enum.Enum):
    IDLE = enum.auto()
    RECORDING = enum.auto()  # Right Ctrl held, audio streaming
    FAILED = enum.auto()  # error while the key is still held: wait for release
    FINISHING = enum.auto()  # key released, waiting for the final result
    REFINING = enum.auto()  # waiting for the LLM
    INJECTING = enum.auto()


class Controller(QObject):
    _inject_finished = Signal(int, str)  # utterance id, error message ("" = ok)

    def __init__(self, app: QApplication, settings: Settings) -> None:
        super().__init__()
        self.app = app
        self.settings = settings
        self.runner = AsyncRunner()
        self.backend: AsrBackend = Qwen3StreamingBackend(self.runner, settings)

        self.state = State.IDLE
        self._utterance = 0
        self._session: AsrSession | None = None
        self._press_time = 0.0
        self._shown = False
        self._error = ""
        self._pending_partial = ""
        self._release_polls = 0
        self._status_busy = False

        self.capsule = CapsuleWindow(blur=settings.capsule_blur)
        self.audio = AudioCapture(self)
        self.audio.level.connect(self.capsule.set_level)
        self.audio.error.connect(self._on_audio_error)

        self.hook = RightCtrlHook(self)
        self.hook.pressed.connect(self._on_pressed)
        self.hook.released.connect(self._on_released)
        self.hook.other_key.connect(self._on_other_key)
        self.hook.failed.connect(lambda msg: self.tray.icon.showMessage("QwenType", msg))

        self.tray = Tray(settings, win32.is_autostart_enabled(), self)
        self.tray.language_changed.connect(self._set_language)
        self.tray.asr_server_requested.connect(self._edit_asr_server)
        self.tray.llm_toggled.connect(self._toggle_llm)
        self.tray.llm_settings_requested.connect(self._edit_llm_settings)
        self.tray.autostart_toggled.connect(self._toggle_autostart)
        self.tray.menu_opened.connect(self.refresh_status)
        self.tray.quit_requested.connect(self.quit)

        self._inject_finished.connect(self._on_inject_finished)

        self._show_timer = self._timer(SHOW_DELAY_MS, self._on_show_timer)
        self._max_timer = self._timer(0, self._on_max_duration)
        self._watchdog = self._timer(0, self._on_watchdog)
        self._poll_timer = self._timer(100, self._poll_key, single=False)

    def _timer(self, ms: int, slot, single: bool = True) -> QTimer:
        t = QTimer(self)
        t.setSingleShot(single)
        t.setInterval(ms)
        t.timeout.connect(slot)
        return t

    def start(self) -> None:
        self.tray.show()
        self.hook.start()
        self.refresh_status()
        log.info("QwenType %s started (language=%s, url=%s)", __version__,
                 self.settings.language or "auto", self.settings.ws_url)

    # -- hotkey ------------------------------------------------------------------

    @Slot()
    def _on_pressed(self) -> None:
        if self.state is not State.IDLE:
            log.debug("Key press ignored in state %s", self.state.name)
            return
        self.capsule.hide_now()  # e.g. a previous error message
        self._utterance += 1
        self._press_time = time.monotonic()
        self._shown = False
        self._error = ""
        self._pending_partial = ""
        self._release_polls = 0

        session = self.backend.create_session(self.settings.language)
        session.events.partial.connect(self._on_partial)
        session.events.final.connect(self._on_final)
        session.events.error.connect(self._on_asr_error)
        self._session = session
        session.start()  # connect now; audio is buffered until "ready"
        self.audio.start(session.send_audio)

        self.state = State.RECORDING
        self.tray.set_recording(True)
        self._show_timer.start()
        self._max_timer.start(int(max(1.0, self.settings.max_record_seconds) * 1000))
        if win32.IS_WINDOWS:
            self._poll_timer.start()

    @Slot()
    def _on_released(self) -> None:
        if self.state is State.FAILED:
            self._show_timer.stop()
            if time.monotonic() - self._press_time < MIN_RECORD_S:
                self.capsule.dismiss()
            elif not self._shown:
                self.capsule.show_error(self._error)
            self._set_idle()
            return
        if self.state is not State.RECORDING:
            return
        if time.monotonic() - self._press_time < MIN_RECORD_S:
            self._cancel("too short")
        else:
            self._finish()

    @Slot()
    def _on_other_key(self) -> None:
        if self.state is State.RECORDING:
            self._cancel("shortcut")
        elif self.state is State.FAILED:
            self._show_timer.stop()
            self.capsule.dismiss()
            self._set_idle()

    def _poll_key(self) -> None:
        # Safety net in case the hook misses the key-up (e.g. secure desktop).
        if self.state not in (State.RECORDING, State.FAILED):
            self._poll_timer.stop()
            return
        if win32.is_key_down(win32.VK_RCONTROL):
            self._release_polls = 0
            return
        self._release_polls += 1
        if self._release_polls >= 3:
            log.info("Right Ctrl release detected by polling")
            self._on_released()

    def _on_show_timer(self) -> None:
        if self.state is State.RECORDING:
            self._shown = True
            self.capsule.show_listening()
            if self._pending_partial:
                self.capsule.set_text(self._pending_partial)
        elif self.state is State.FAILED:
            self._shown = True
            self.capsule.show_error(self._error)

    def _on_max_duration(self) -> None:
        if self.state is State.RECORDING:
            log.info("Maximum recording duration reached")
            self._finish(notice=f"Max {self.settings.max_record_seconds:g} s reached")

    # -- recording lifecycle -------------------------------------------------------

    def _stop_timers(self) -> None:
        for t in (self._show_timer, self._max_timer, self._poll_timer):
            t.stop()

    def _cancel(self, why: str) -> None:
        log.info("Recording cancelled (%s)", why)
        self._stop_timers()
        self.audio.stop(flush=False)
        if self._session is not None:
            self._session.cancel()
            self._session = None
        self.capsule.dismiss()
        self._set_idle()

    def _finish(self, notice: str = "") -> None:
        self._stop_timers()
        session = self._session
        if not self._shown:
            self._on_show_timer()
        self.state = State.FINISHING
        self.tray.set_recording(False)
        if notice:
            self.capsule.show_notice(notice)
        # The last partial chunk is flushed before "stop" (same worker thread).
        self.audio.stop(flush=True, then=session.stop if session else None)
        s = self.settings
        self._watchdog.start(int((s.ready_timeout_seconds + s.final_timeout_seconds + 5) * 1000))

    def _set_idle(self) -> None:
        self._stop_timers()
        self._watchdog.stop()
        self.state = State.IDLE
        self.tray.set_recording(False)

    def _fail(self, message: str) -> None:
        """Abort the utterance with a short error in the capsule (~2 s)."""
        self._stop_timers()
        self._watchdog.stop()
        self.audio.stop(flush=False)
        if self._session is not None:
            self._session.cancel()
            self._session = None
        if self.state is State.RECORDING and not self._shown:
            # Key still held and the capsule isn't visible yet: show on the 150 ms mark,
            # or not at all if the key is released quickly.
            self.state = State.FAILED
            self._error = message
            self._show_timer.start(max(0, SHOW_DELAY_MS - int((time.monotonic() - self._press_time) * 1000)))
            if win32.IS_WINDOWS:
                self._poll_timer.start()
            self.tray.set_recording(False)
            return
        was_recording = self.state is State.RECORDING
        self.capsule.show_error(message)
        if was_recording:
            self.state = State.FAILED  # swallow the coming key release
            self._error = message
            self.tray.set_recording(False)
            if win32.IS_WINDOWS:
                self._poll_timer.start()
        else:
            self._set_idle()

    def _is_current(self) -> bool:
        session = self._session
        return session is not None and self.sender() is session.events

    # -- ASR events ------------------------------------------------------------------

    @Slot(str)
    def _on_partial(self, text: str) -> None:
        if not self._is_current() or self.state not in (State.RECORDING, State.FINISHING):
            return
        if self._shown:
            self.capsule.set_text(text)
        else:
            self._pending_partial = text

    @Slot(str, str)
    def _on_final(self, text: str, language: str) -> None:
        if not self._is_current():
            return
        self._session = None
        self._watchdog.stop()
        if self.state is not State.FINISHING:
            return
        text = text.strip()
        if not text:
            log.info("Empty transcript")
            self.capsule.dismiss()
            self._set_idle()
            return
        s = self.settings
        if s.llm_enabled and s.llm_configured:
            self.state = State.REFINING
            self.capsule.show_status("Refining…")
            utterance = self._utterance
            coro = llm.refine(text, base_url=s.llm_base_url, api_key=s.llm_api_key, model=s.llm_model,
                              timeout=s.llm_timeout_seconds, selected_language=s.language,
                              detected_language=language)
            run_async(self.runner, coro,
                      lambda result, error: self._on_refined(utterance, text, result, error))
        else:
            self._inject(text)

    @Slot(str)
    def _on_asr_error(self, message: str) -> None:
        if not self._is_current():
            return
        if self.state in (State.RECORDING, State.FINISHING):
            self._fail(message)
            self.refresh_status()

    @Slot(str)
    def _on_audio_error(self, message: str) -> None:
        if self.state in (State.RECORDING, State.FINISHING):
            self._fail(message)

    def _on_watchdog(self) -> None:
        if self.state is State.FINISHING:
            self._fail("No result from ASR server")

    def _on_refined(self, utterance: int, original: str, result, error) -> None:
        if utterance != self._utterance or self.state is not State.REFINING:
            return
        self._inject(result if error is None and isinstance(result, str) and result else original)

    # -- injection ---------------------------------------------------------------------

    def _inject(self, text: str) -> None:
        self.state = State.INJECTING
        self.capsule.dismiss()
        utterance = self._utterance
        s = self.settings

        def work() -> None:
            try:
                inject_text(text, s.unicode_max_chars, s.clipboard_apps)
                self._inject_finished.emit(utterance, "")
            except InjectionError as e:
                self._inject_finished.emit(utterance, str(e))
            except Exception as e:
                log.exception("Injection failed")
                self._inject_finished.emit(utterance, f"Typing failed: {type(e).__name__}")

        threading.Thread(target=work, name="QwenType-Inject", daemon=True).start()

    @Slot(int, str)
    def _on_inject_finished(self, utterance: int, error: str) -> None:
        if utterance != self._utterance:
            return
        self._set_idle()
        if error:
            self.capsule.show_error(error)

    # -- tray actions --------------------------------------------------------------------

    def refresh_status(self) -> None:
        if self._status_busy:
            return
        self._status_busy = True

        def done(result, error) -> None:
            self._status_busy = False
            self.tray.set_status(result if error is None and result else "offline")

        run_async(self.runner, self.backend.status(), done)

    def _save(self) -> None:
        try:
            self.settings.save()
        except OSError as e:
            log.error("Could not save settings: %s", e)
            self.tray.icon.showMessage("QwenType", f"Could not save settings: {e}",
                                       QSystemTrayIcon.MessageIcon.Warning)

    def _set_language(self, code: str) -> None:
        self.settings.language = code
        self._save()
        log.info("Language set to %s", code or "auto")

    def _edit_asr_server(self) -> None:
        dialog = AsrServerDialog(self.settings, self.runner)
        if dialog.exec_front():
            self._save()
            self.refresh_status()

    def _toggle_llm(self, enabled: bool) -> None:
        if enabled and not self.settings.llm_configured:
            self._edit_llm_settings()
            enabled = self.settings.llm_configured
        self.settings.llm_enabled = enabled
        self.tray.set_llm_checked(enabled)
        self._save()

    def _edit_llm_settings(self) -> None:
        dialog = LlmSettingsDialog(self.settings, self.runner)
        if dialog.exec_front():
            self._save()

    def _toggle_autostart(self, enabled: bool) -> None:
        try:
            win32.set_autostart(enabled)
        except OSError as e:
            log.error("Autostart change failed: %s", e)
            self.tray.icon.showMessage("QwenType", f"Could not change autostart: {e}",
                                       QSystemTrayIcon.MessageIcon.Warning)
        self.tray.set_autostart_checked(win32.is_autostart_enabled())

    def quit(self) -> None:
        log.info("Quitting")
        if self._session is not None:
            self._session.cancel()
            self._session = None
        self.hook.stop()
        self.audio.shutdown()
        self.capsule.hide_now()
        self.tray.hide()
        self.runner.stop()
        self.app.quit()


def _setup_logging(debug: bool) -> None:
    handlers: list[logging.Handler] = []
    try:
        settings_dir().mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(settings_dir() / "qwentype.log", maxBytes=1_000_000,
                                            backupCount=2, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:  # None in windowed (no console) builds
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qwentype", description="Hold Right Ctrl, speak, release.")
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    args = parser.parse_args(argv)
    _setup_logging(args.debug)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("QwenType")
    app.setApplicationDisplayName("QwenType")
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(app_icon())

    lock = None
    if win32.IS_WINDOWS:
        already_running = not win32.acquire_single_instance()
    else:
        lock = QLockFile(str(settings_dir() / "qwentype.lock"))
        settings_dir().mkdir(parents=True, exist_ok=True)
        already_running = not lock.tryLock(0)
    if already_running:
        QMessageBox.information(None, "QwenType", "QwenType is already running (see the system tray).")
        return 0

    if not QSystemTrayIcon.isSystemTrayAvailable():
        log.warning("No system tray available")

    controller = Controller(app, Settings.load())
    controller.start()
    return app.exec()
