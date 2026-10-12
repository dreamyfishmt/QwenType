"""Hotkey logic, the settings dialog, the tray menu and the controller's state machine
(with a fake ASR backend and no microphone)."""

import asyncio
import importlib
import json
import os
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never touch the real settings: Controller._save() writes to settings_dir().
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="qwentype-test-")
os.environ["XDG_CONFIG_HOME"] = os.environ["APPDATA"]

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from qwentype import editlog
from qwentype.aio import AsyncRunner
from qwentype.asr.base import AsrBackend, AsrSession
from qwentype.dialogs import SettingsDialog, parse_app_list
from qwentype.hotkey import (
    ALTGR_FAKE_LCTRL_SCAN,
    LLKHF_EXTENDED,
    VK_ESCAPE,
    VK_LCONTROL,
    VK_LSHIFT,
    VK_RETURN,
    VK_RSHIFT,
    VK_Z,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_XBUTTONDOWN,
    WM_XBUTTONUP,
    HotkeyHook,
    get_hotkey,
)
from qwentype.injector import InjectionError
from qwentype.settings import Settings
from qwentype.tray import Tray

qmain = importlib.import_module("qwentype.main")  # `qwentype.main` the attribute is the entry function


def setUpModule():
    global app
    app = QApplication.instance() or QApplication([])


class Recorder:
    def __init__(self, hook: HotkeyHook):
        self.events: list[str] = []
        for name in ("pressed", "released", "other_key", "escape", "shift", "enter", "undo"):
            getattr(hook, name).connect(lambda n=name: self.events.append(n))


class HotkeyTest(unittest.TestCase):
    def test_right_ctrl_passes_through_and_detects_shortcuts(self):
        hook = HotkeyHook(get_hotkey("right_ctrl"))
        rec = Recorder(hook)
        self.assertFalse(hook.handle_key(WM_KEYDOWN, 0xA3, flags=LLKHF_EXTENDED))
        self.assertFalse(hook.handle_key(WM_KEYDOWN, 0xA3, flags=LLKHF_EXTENDED))  # auto-repeat
        self.assertFalse(hook.handle_key(WM_KEYDOWN, ord("C")))
        self.assertFalse(hook.handle_key(WM_KEYDOWN, ord("V")))  # reported once per press
        self.assertFalse(hook.handle_key(WM_KEYUP, 0xA3, flags=LLKHF_EXTENDED))
        self.assertEqual(rec.events, ["pressed", "other_key", "released"])

    def test_left_ctrl_is_not_right_ctrl(self):
        hook = HotkeyHook(get_hotkey("right_ctrl"))
        rec = Recorder(hook)
        hook.handle_key(WM_KEYDOWN, 0x11, flags=0)
        hook.handle_key(WM_KEYDOWN, VK_LCONTROL, flags=0)
        self.assertEqual(rec.events, [])

    def test_caps_lock_is_swallowed_without_shortcuts(self):
        hook = HotkeyHook(get_hotkey("caps_lock"))
        rec = Recorder(hook)
        self.assertTrue(hook.handle_key(WM_KEYDOWN, 0x14))
        self.assertFalse(hook.handle_key(WM_KEYDOWN, ord("A")))  # not a modifier: no other_key
        self.assertTrue(hook.handle_key(WM_KEYUP, 0x14))
        self.assertEqual(rec.events, ["pressed", "released"])
        self.assertEqual(get_hotkey("caps_lock").poll_vk, 0)  # swallowed keys can't be polled

    def test_altgr_fake_left_ctrl_is_ignored(self):
        hook = HotkeyHook(get_hotkey("right_alt"))
        rec = Recorder(hook)
        hook.handle_key(WM_KEYDOWN, 0xA5, flags=LLKHF_EXTENDED)
        hook.handle_key(WM_KEYDOWN, VK_LCONTROL, scan=ALTGR_FAKE_LCTRL_SCAN)  # auto-repeat of AltGr
        hook.handle_key(WM_KEYUP, 0xA5, flags=LLKHF_EXTENDED)
        self.assertEqual(rec.events, ["pressed", "released"])

    def test_mouse_button(self):
        hook = HotkeyHook(get_hotkey("mouse_back"))
        rec = Recorder(hook)
        self.assertFalse(hook.handle_mouse(WM_XBUTTONDOWN, 2))  # forward button: not ours
        self.assertTrue(hook.handle_mouse(WM_XBUTTONDOWN, 1))
        self.assertTrue(hook.handle_mouse(WM_XBUTTONUP, 1))
        self.assertEqual(rec.events, ["pressed", "released"])

    def test_unknown_id_falls_back(self):
        self.assertEqual(HotkeyHook(get_hotkey("no_such_key")).hotkey.id, "right_ctrl")

    def test_escape_only_captured_while_recording(self):
        hook = HotkeyHook(get_hotkey("right_ctrl"))
        rec = Recorder(hook)
        self.assertFalse(hook.handle_key(WM_KEYDOWN, VK_ESCAPE))  # idle: Esc belongs to the app
        self.assertFalse(hook.handle_key(WM_KEYUP, VK_ESCAPE))
        self.assertEqual(rec.events, [])
        hook.capture_escape = True
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_ESCAPE))
        hook.capture_escape = False  # the recording was cancelled
        self.assertTrue(hook.handle_key(WM_KEYUP, VK_ESCAPE))  # its key-up is swallowed too
        self.assertFalse(hook.handle_key(WM_KEYUP, VK_ESCAPE))
        self.assertEqual(rec.events, ["escape"])

    def test_shift_marks_review_while_recording(self):
        hook = HotkeyHook(get_hotkey("right_ctrl"))
        rec = Recorder(hook)
        self.assertFalse(hook.handle_key(WM_KEYDOWN, VK_LSHIFT))  # not recording: Shift belongs to the app
        self.assertFalse(hook.handle_key(WM_KEYUP, VK_LSHIFT))
        hook.handle_key(WM_KEYDOWN, 0xA3, flags=LLKHF_EXTENDED)
        hook.capture_shift = True
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_LSHIFT))  # swallowed: no Ctrl+Shift layout switch
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_LSHIFT))  # auto-repeat
        hook.capture_shift = False
        self.assertTrue(hook.handle_key(WM_KEYUP, VK_LSHIFT))  # its key-up too
        self.assertEqual(rec.events, ["pressed", "shift"])  # not a shortcut: the recording goes on

    def test_right_shift_hotkey_leaves_left_shift(self):
        hook = HotkeyHook(get_hotkey("right_shift"))
        rec = Recorder(hook)
        hook.capture_shift = True
        hook.handle_key(WM_KEYDOWN, VK_RSHIFT)
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_LSHIFT))
        self.assertEqual(rec.events, ["pressed", "shift"])

    def test_review_keys(self):
        hook = HotkeyHook(get_hotkey("right_ctrl"))
        rec = Recorder(hook)
        self.assertFalse(hook.handle_key(WM_KEYDOWN, VK_RETURN))  # box closed: Enter belongs to the app
        self.assertFalse(hook.handle_key(WM_KEYUP, VK_RETURN))
        hook.capture_review = True
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_RETURN))
        hook.capture_review = False  # the box closed on Enter
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_RETURN))  # auto-repeat while still held
        self.assertTrue(hook.handle_key(WM_KEYUP, VK_RETURN))
        hook.capture_review = True
        self.assertFalse(hook.handle_key(WM_KEYDOWN, VK_Z))  # plain Z
        self.assertFalse(hook.handle_key(WM_KEYUP, VK_Z))
        self.assertFalse(hook.handle_key(WM_KEYDOWN, VK_LCONTROL))
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_Z))  # Ctrl+Z
        self.assertTrue(hook.handle_key(WM_KEYUP, VK_Z))
        self.assertFalse(hook.handle_key(WM_KEYUP, VK_LCONTROL))
        # Right Ctrl is the hotkey: Right Ctrl+Z is still an undo, not a shortcut for the app.
        hook.handle_key(WM_KEYDOWN, 0xA3, flags=LLKHF_EXTENDED)
        self.assertTrue(hook.handle_key(WM_KEYDOWN, VK_Z))
        hook.handle_key(WM_KEYUP, VK_Z)
        hook.handle_key(WM_KEYUP, 0xA3, flags=LLKHF_EXTENDED)
        # AltGr+Z types a character on some layouts: not an undo.
        hook.handle_key(WM_KEYDOWN, VK_LCONTROL, scan=ALTGR_FAKE_LCTRL_SCAN)
        self.assertFalse(hook.handle_key(WM_KEYDOWN, VK_Z))
        self.assertEqual(rec.events, ["enter", "undo", "pressed", "undo", "released"])

    def test_missed_key_up_is_recovered(self):
        hook = HotkeyHook(get_hotkey("caps_lock"))
        rec = Recorder(hook)
        hook.handle_key(WM_KEYDOWN, 0x14, time_ms=1000)
        hook.handle_key(WM_KEYDOWN, 0x14, time_ms=1500)  # auto-repeat
        hook.handle_key(WM_KEYDOWN, 0x14, time_ms=2000)
        self.assertEqual(rec.events, ["pressed"])
        # The key-up was missed; the next press comes much later.
        hook.handle_key(WM_KEYDOWN, 0x14, time_ms=9000)
        self.assertEqual(rec.events, ["pressed", "released", "pressed"])
        # The 32-bit tick count wraps around.
        hook.handle_key(WM_KEYDOWN, 0x14, time_ms=0xFFFFFF00)
        hook.handle_key(WM_KEYDOWN, 0x14, time_ms=0x10)
        self.assertEqual(rec.events[-2:], ["released", "pressed"])
        self.assertEqual(rec.events.count("pressed"), 3)

    def test_mouse_button_never_repeats(self):
        hook = HotkeyHook(get_hotkey("mouse_forward"))
        rec = Recorder(hook)
        hook.handle_mouse(WM_XBUTTONDOWN, 2, time_ms=100)
        hook.handle_mouse(WM_XBUTTONDOWN, 2, time_ms=200)  # the button-up was missed
        self.assertEqual(rec.events, ["pressed", "released", "pressed"])

    def test_reset_after_polled_release(self):
        hook = HotkeyHook(get_hotkey("right_ctrl"))
        rec = Recorder(hook)
        hook.handle_key(WM_KEYDOWN, 0xA3, flags=LLKHF_EXTENDED)
        hook.reset()  # the controller saw the release by polling
        hook.handle_key(WM_KEYDOWN, 0xA3, flags=LLKHF_EXTENDED)
        self.assertEqual(rec.events, ["pressed", "pressed"])


class SettingsDialogTest(unittest.TestCase):
    def test_parse_app_list(self):
        self.assertEqual(
            parse_app_list(" mstsc.exe, vmconnect.exe;mstsc.exe  x.exe "), ["mstsc.exe", "vmconnect.exe", "x.exe"]
        )
        self.assertEqual(parse_app_list(""), [])

    def test_advanced_tab_applies_values(self):
        s = Settings()
        runner = AsyncRunner()
        try:
            d = SettingsDialog(s, runner, "advanced")
            self.assertIs(d.tabs.currentWidget(), d.advanced)
            d.advanced.max_record.setValue(120)
            d.advanced.final_timeout.setValue(45)
            d.advanced.clipboard_apps.setText("mstsc.exe, vmconnect.exe")
            d.advanced.history_size.setValue(0)
            d.advanced.copy_on_failure.setChecked(False)
            d.advanced.capsule_blur.setChecked(True)
            d.advanced.review_log.setChecked(True)
            d.llm.timeout.setValue(12)
            d.llm.min_length.setValue(20)
            d.llm.max_growth.setValue(150)
            d._save()
            self.assertEqual(d.result(), SettingsDialog.DialogCode.Accepted)
        finally:
            runner.stop()
        self.assertEqual((s.max_record_seconds, s.final_timeout_seconds), (120.0, 45.0))
        self.assertEqual(s.clipboard_apps, ["mstsc.exe", "vmconnect.exe"])
        self.assertEqual((s.history_size, s.copy_on_failure, s.capsule_blur), (0, False, True))
        self.assertTrue(s.review_log)
        self.assertEqual(s.llm_timeout_seconds, 12.0)
        self.assertEqual((s.llm_length_guard, s.llm_min_length_percent, s.llm_max_growth_percent), (True, 20, 150))

    def test_length_guard_can_be_turned_off(self):
        s = Settings()
        runner = AsyncRunner()
        try:
            d = SettingsDialog(s, runner, "llm")
            self.assertTrue(d.llm.min_length.isEnabled())
            d.llm.length_guard.setChecked(False)
            self.assertFalse(d.llm.min_length.isEnabled())
            self.assertFalse(d.llm.max_growth.isEnabled())
            d._save()
        finally:
            runner.stop()
        self.assertEqual(s.llm_length_limits, (None, None))
        self.assertEqual((s.llm_min_length_percent, s.llm_max_growth_percent), (40, 30))  # kept for later

    def test_custom_system_prompt(self):
        from qwentype import llm

        s = Settings()
        runner = AsyncRunner()
        try:
            d = SettingsDialog(s, runner, "llm")
            self.assertEqual(d.llm.prompt.toPlainText(), llm.SYSTEM_PROMPT)  # built-in shown to edit
            d.llm.prompt.setPlainText("  Fix typos only.\n")
            d._save()
            self.assertEqual(s.llm_system_prompt, "Fix typos only.")

            d = SettingsDialog(s, runner, "llm")
            self.assertEqual(d.llm.prompt.toPlainText(), "Fix typos only.")
            d.llm.prompt.setPlainText(llm.SYSTEM_PROMPT)  # the Default button
            d._save()
            self.assertEqual(s.llm_system_prompt, "")  # stored as "use the built-in prompt"

            d = SettingsDialog(s, runner, "llm")
            self.assertEqual(d.llm.edit_prompt.toPlainText(), llm.EDIT_SYSTEM_PROMPT)
            d.llm.edit_prompt.setPlainText("Edit as told.")
            d._save()
            self.assertEqual((s.llm_system_prompt, s.llm_edit_prompt), ("", "Edit as told."))
            d = SettingsDialog(s, runner, "llm")
            d.llm.prompts.setCurrentWidget(d.llm.edit_prompt)
            d.llm._reset_prompt()  # Default resets the selected tab only
            self.assertEqual(d.llm.edit_prompt.toPlainText(), llm.EDIT_SYSTEM_PROMPT)
            d._save()
            self.assertEqual(s.llm_edit_prompt, "")

            s.llm_system_prompt = "x"
            d = SettingsDialog(s, runner, "llm")
            d.llm.prompt.setPlainText("   ")
            d._save()
            self.assertEqual(s.llm_system_prompt, "")
        finally:
            runner.stop()

    def test_vocabulary_tab(self):
        s = Settings(asr_context="Vocabulary: QwenType")
        runner = AsyncRunner()
        try:
            d = SettingsDialog(s, runner, "vocabulary")
            self.assertIs(d.tabs.currentWidget(), d.vocabulary)
            self.assertEqual(d.vocabulary.context.toPlainText(), "Vocabulary: QwenType")
            d.vocabulary.context.setPlainText("Kubernetes\nQwenType\n张三\n")
            d.vocabulary.corrections.setPlainText("库伯内提斯 -> Kubernetes\n配森")
            d.tabs.setCurrentWidget(d.asr)
            d._save()  # line 2 has no arrow
            self.assertIs(d.tabs.currentWidget(), d.vocabulary)
            self.assertIn("line 2", d.error.text())
            self.assertEqual(s.asr_corrections, "")
            d.vocabulary.corrections.setPlainText("# fixes\n库伯内提斯 -> Kubernetes\n配森 -> Python\n")
            d._save()
            self.assertEqual(d.result(), SettingsDialog.DialogCode.Accepted)
        finally:
            runner.stop()
        self.assertEqual(s.asr_context, "Kubernetes\nQwenType\n张三")
        self.assertEqual(s.asr_corrections, "# fixes\n库伯内提斯 -> Kubernetes\n配森 -> Python")

    def test_invalid_url_switches_to_its_tab(self):
        s = Settings()
        runner = AsyncRunner()
        try:
            d = SettingsDialog(s, runner, "advanced")
            d.asr.url.setText("http://nope")
            d._save()
            self.assertIs(d.tabs.currentWidget(), d.asr)
            self.assertIn("ws://", d.error.text())
        finally:
            runner.stop()
        self.assertEqual(s.ws_url, Settings().ws_url)


class TrayTest(unittest.TestCase):
    def test_recent_menu_and_hint(self):
        tray = Tray(Settings(hotkey="caps_lock", hotkey_mode="toggle"), False)
        self.assertIn("Tap Caps Lock", tray.icon.toolTip())
        tray.set_recent(["hello & bye", "x" * 100])
        labels = [a.text() for a in tray.recent_menu.actions() if not a.isSeparator()]
        self.assertEqual(labels[0], "hello && bye")
        self.assertTrue(labels[1].endswith("…"))
        self.assertEqual(labels[-1], "Clear")
        tray.set_recent([], visible=False)
        self.assertFalse(tray.recent_menu.menuAction().isVisible())

    def test_vocabulary_item_opens_its_tab(self):
        tray = Tray(Settings(), False)
        pages = []
        tray.settings_requested.connect(pages.append)
        next(a for a in tray.menu.actions() if a.text() == "Vocabulary…").trigger()
        self.assertEqual(pages, ["vocabulary"])


class FakeSession(AsrSession):
    def __init__(self):
        super().__init__()
        self.calls: list[str] = []

    def start(self):
        self.calls.append("start")

    def send_audio(self, pcm):
        pass

    def stop(self):
        self.calls.append("stop")

    def cancel(self):
        self.calls.append("cancel")


class FakeBackend(AsrBackend):
    def __init__(self):
        self.sessions: list[FakeSession] = []
        self.status_value = "ready"

    def create_session(self, language):
        self.sessions.append(FakeSession())
        return self.sessions[-1]

    async def status(self):
        return self.status_value


class FakeAudio:
    def start(self, on_chunk):
        pass

    def stop(self, flush=True, then=None):
        if then:
            then()

    def shutdown(self):
        pass


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.settings = Settings()
        self.c = qmain.Controller(app, self.settings)
        self.c.backend = self.backend = FakeBackend()
        self.c.audio = FakeAudio()

    def tearDown(self):
        self.c.runner.stop()
        self.c.capsule.hide_now()

    def _wait_status(self, status, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.c.tray._status != status and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertEqual(self.c.tray._status, status)

    def test_status_retried_while_offline(self):
        self.backend.status_value = "offline"
        self.c.refresh_status()
        self._wait_status("offline")
        self.assertTrue(self.c._status_timer.isActive())
        self.assertEqual(self.c._status_timer.interval(), 60_000)
        self.assertEqual(self.c.tray.icon.icon().cacheKey(), self.c.tray._icon_offline.cacheKey())
        self.backend.status_value = "ready"
        self.c._status_timer.timeout.emit()  # the 60 s check
        self._wait_status("ready")
        self.assertFalse(self.c._status_timer.isActive())

    def _wait_idle(self, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.c.state is not qmain.State.IDLE and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertIs(self.c.state, qmain.State.IDLE)

    def _record(self):
        self.c._on_pressed()
        self.assertIs(self.c.state, qmain.State.RECORDING)
        self.c._press_time -= 1.0  # pretend the key was held long enough
        return self.backend.sessions[-1]

    def test_hold_mode_short_press_cancels(self):
        self.c._on_pressed()
        self.c._on_released()
        self.assertIs(self.c.state, qmain.State.IDLE)
        self.assertEqual(self.backend.sessions[-1].calls, ["start", "cancel"])

    def test_toggle_mode_and_recent(self):
        self.settings.hotkey_mode = "toggle"
        session = self._record()
        self.c._on_released()  # releasing the key doesn't stop in toggle mode
        self.assertIs(self.c.state, qmain.State.RECORDING)
        self.c._on_pressed()  # second tap stops
        self.assertIs(self.c.state, qmain.State.FINISHING)
        self.assertEqual(session.calls, ["start", "stop"])
        session.events.final.emit("hello world", "English")
        self._wait_idle()
        self.assertEqual(list(self.c._history), ["hello world"])

    def test_escape_cancels(self):
        self.settings.hotkey_mode = "toggle"
        self.assertFalse(self.c.hook.capture_escape)
        session = self._record()
        self.assertTrue(self.c.hook.capture_escape)
        self.c._on_released()
        self.c._on_escape()
        self.assertIs(self.c.state, qmain.State.IDLE)
        self.assertFalse(self.c.hook.capture_escape)
        self.assertEqual(session.calls, ["start", "cancel"])

    def test_toggle_mode_error_goes_idle(self):
        self.settings.hotkey_mode = "toggle"
        session = self._record()
        session.events.error.emit("ASR server offline")
        self.assertIs(self.c.state, qmain.State.IDLE)

    def test_corrections_apply_to_partial_and_final(self):
        self.settings.asr_corrections = "库伯内提斯 -> Kubernetes\n嗯 ->"
        session = self._record()
        self.c._on_show_timer()
        session.events.partial.emit("我用库伯内提斯")
        self.assertEqual(self.c.capsule._text, "我用Kubernetes")
        self.c._on_released()
        with mock.patch.object(qmain, "inject_text") as inject:
            session.events.final.emit("我用库伯内提斯部署", "Chinese")
            self._wait_idle()
        self.assertEqual(inject.call_args.args[0], "我用Kubernetes部署")
        self.assertEqual(self.c._asr_text, "我用库伯内提斯部署")  # the review log keeps what was recognized

        session = self._record()
        self.c._on_released()
        with mock.patch.object(qmain, "inject_text") as inject:
            session.events.final.emit("嗯", "Chinese")  # nothing left after the corrections
            self._wait_idle()
        inject.assert_not_called()

    def test_failed_injection_copies_text(self):
        QGuiApplication.clipboard().setText("")
        session = self._record()
        self.c._on_released()
        with mock.patch.object(qmain, "inject_text", side_effect=InjectionError("Input blocked")):
            session.events.final.emit("keep me", "English")
            self._wait_idle()
        self.assertEqual(QGuiApplication.clipboard().text(), "keep me")
        self.assertIn("text copied", self.c.capsule._text)

    # -- review box ------------------------------------------------------------------

    def _wait_state(self, state, timeout=3.0):
        deadline = time.monotonic() + timeout
        while self.c.state is not state and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        self.assertIs(self.c.state, state)

    def _open_review(self, text="hello world"):
        session = self._record()
        self.assertTrue(self.c.hook.capture_shift)
        self.c._on_shift()
        self.c._on_released()
        session.events.final.emit(text, "English")
        self.assertIs(self.c.state, qmain.State.REVIEW)
        self.assertTrue(self.c.review_box.isVisible())
        self.assertEqual(self.c.review_box.text, text)
        self.assertTrue(self.c.hook.capture_review)
        self.assertTrue(self.c.hook.capture_escape)
        self.assertFalse(self.c.hook.capture_shift)

    def _instruct(self, instruction):
        """Speak an edit instruction while the review box is open."""
        session = self._record()
        self.assertTrue(self.c._instruction)
        self.assertFalse(self.c.hook.capture_shift)
        self.c._on_released()
        session.events.final.emit(instruction, "English")

    def test_without_shift_the_text_is_typed(self):
        session = self._record()
        self.c._on_released()
        with mock.patch.object(qmain, "inject_text") as inject:
            session.events.final.emit("direct", "English")
            self._wait_idle()
        self.assertEqual(inject.call_args.args[0], "direct")
        self.assertFalse(self.c.review_box.isVisible())

    def test_review_enter_types_the_text(self):
        with mock.patch.object(qmain, "inject_text") as inject:
            self._open_review()
            inject.assert_not_called()
            self.c._on_enter()
            self._wait_idle()
        self.assertEqual(inject.call_args.args[0], "hello world")
        self.assertFalse(self.c.review_box.isVisible())
        self.assertFalse(self.c.hook.capture_review)
        self.assertEqual(list(self.c._history), ["hello world"])

    def test_review_escape_discards_to_recent(self):
        with mock.patch.object(qmain, "inject_text") as inject:
            self._open_review()
            self.c._on_escape()
            self.assertIs(self.c.state, qmain.State.IDLE)
            inject.assert_not_called()
        self.assertFalse(self.c.review_box.isVisible())
        self.assertEqual(list(self.c._history), ["hello world"])

    def test_voice_edit_needs_an_llm(self):
        self.settings.llm_model = ""
        self._open_review()
        sessions = len(self.backend.sessions)
        self.c._on_pressed()
        self.assertIs(self.c.state, qmain.State.REVIEW)
        self.assertEqual(len(self.backend.sessions), sessions)  # nothing recorded
        self.assertIn("LLM", self.c.review_box.status.text())

    def test_voice_edit_undo_and_enter(self):
        self.settings.llm_model = "m"
        calls = []

        async def fake_edit(text, instruction, **kwargs):
            calls.append((text, instruction, kwargs["system_prompt"]))
            return f"{text} (edited)"

        with mock.patch.object(qmain.llm, "edit", fake_edit), mock.patch.object(qmain, "inject_text") as inject:
            self._open_review("draft")
            self._instruct("make it better")
            self._wait_state(qmain.State.REVIEW)
            self.assertEqual(self.c.review_box.text, "draft (edited)")
            self._instruct("again")
            self._wait_state(qmain.State.REVIEW)
            self.assertEqual(self.c.review_box.text, "draft (edited) (edited)")
            self.c._on_undo()
            self.assertEqual(self.c.review_box.text, "draft (edited)")
            self.c._on_enter()
            self._wait_idle()
        self.assertEqual(calls, [("draft", "make it better", ""), ("draft (edited)", "again", "")])
        self.assertEqual(inject.call_args.args[0], "draft (edited)")

    def test_failed_edit_keeps_the_text(self):
        self.settings.llm_model = "m"

        async def fake_edit(text, instruction, **kwargs):
            raise TimeoutError("too slow")

        with mock.patch.object(qmain.llm, "edit", fake_edit):
            self._open_review("draft")
            self._instruct("rewrite")
            self._wait_state(qmain.State.REVIEW)
        self.assertEqual(self.c.review_box.text, "draft")
        self.assertIn("Edit failed", self.c.review_box.status.text())

    def test_escape_while_instructing_keeps_the_box(self):
        self.settings.llm_model = "m"
        self._open_review()
        session = self._record()
        self.c._on_escape()
        self.assertEqual(session.calls, ["start", "cancel"])
        self.assertIs(self.c.state, qmain.State.REVIEW)
        self.assertTrue(self.c.review_box.isVisible())
        self.c._on_enter()  # Enter is ignored only while an instruction is in progress
        self._wait_idle()

    def test_undo_cancels_an_instruction_in_progress(self):
        self.settings.llm_model = "m"
        self._open_review()
        session = self._record()
        self.c._on_undo()
        self.assertEqual(session.calls, ["start", "cancel"])
        self.assertIs(self.c.state, qmain.State.REVIEW)
        self.assertEqual(self.c.review_box.status.text(), "Nothing to undo")

    def test_enter_is_ignored_while_editing(self):
        self.settings.llm_model = "m"

        async def never(text, instruction, **kwargs):
            await asyncio.sleep(10)

        with mock.patch.object(qmain.llm, "edit", never), mock.patch.object(qmain, "inject_text") as inject:
            self._open_review()
            self._instruct("slowly")
            self.assertIs(self.c.state, qmain.State.EDITING)
            self.c._on_enter()
            self.assertIs(self.c.state, qmain.State.EDITING)
            inject.assert_not_called()
            self.c._on_escape()  # cancels the edit, keeps the box
            self.assertIs(self.c.state, qmain.State.REVIEW)
            self.assertEqual(self.c.review_box.text, "hello world")

    def test_review_mark_on_capsule(self):
        session = self._record()
        width = self.c.capsule._base_width()
        self.c._on_shift()
        self.assertGreater(self.c.capsule._base_width(), width)
        self.c._on_escape()
        self.assertEqual(session.calls, ["start", "cancel"])
        self._record()
        self.assertEqual(self.c.capsule._base_width(), width)  # cleared on the next press

    def test_review_log(self):
        path = editlog.log_path()
        path.unlink(missing_ok=True)
        with mock.patch.object(qmain, "inject_text"):
            self._open_review("not logged")
            self.c._on_enter()
            self._wait_idle()
            self.assertFalse(path.exists())
            self.settings.review_log = True
            self._open_review("logged")
            self.c._on_escape()
        lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual((record["asr"], record["final"], record["language"]), ("logged", None, "English"))


if __name__ == "__main__":
    unittest.main()
