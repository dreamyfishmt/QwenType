"""Hotkey logic, the settings dialog, the tray menu and the controller's state machine
(with a fake ASR backend and no microphone)."""

import importlib
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

from qwentype.aio import AsyncRunner
from qwentype.asr.base import AsrBackend, AsrSession
from qwentype.dialogs import SettingsDialog, parse_app_list
from qwentype.hotkey import (
    ALTGR_FAKE_LCTRL_SCAN,
    LLKHF_EXTENDED,
    VK_ESCAPE,
    VK_LCONTROL,
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
        for name in ("pressed", "released", "other_key", "escape"):
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

    def test_escape_and_unknown_id(self):
        hook = HotkeyHook(get_hotkey("no_such_key"))
        self.assertEqual(hook.hotkey.id, "right_ctrl")
        rec = Recorder(hook)
        hook.handle_key(WM_KEYDOWN, VK_ESCAPE)
        self.assertEqual(rec.events, ["escape"])


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
            d.llm.timeout.setValue(12)
            d._save()
            self.assertEqual(d.result(), SettingsDialog.DialogCode.Accepted)
        finally:
            runner.stop()
        self.assertEqual((s.max_record_seconds, s.final_timeout_seconds), (120.0, 45.0))
        self.assertEqual(s.clipboard_apps, ["mstsc.exe", "vmconnect.exe"])
        self.assertEqual((s.history_size, s.copy_on_failure, s.capsule_blur), (0, False, True))
        self.assertEqual(s.llm_timeout_seconds, 12.0)

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
        session = self._record()
        self.c._on_released()
        self.c._on_escape()
        self.assertIs(self.c.state, qmain.State.IDLE)
        self.assertEqual(session.calls, ["start", "cancel"])

    def test_toggle_mode_error_goes_idle(self):
        self.settings.hotkey_mode = "toggle"
        session = self._record()
        session.events.error.emit("ASR server offline")
        self.assertIs(self.c.state, qmain.State.IDLE)

    def test_failed_injection_copies_text(self):
        QGuiApplication.clipboard().setText("")
        session = self._record()
        self.c._on_released()
        with mock.patch.object(qmain, "inject_text", side_effect=InjectionError("Input blocked")):
            session.events.final.emit("keep me", "English")
            self._wait_idle()
        self.assertEqual(QGuiApplication.clipboard().text(), "keep me")
        self.assertIn("text copied", self.c.capsule._text)


if __name__ == "__main__":
    unittest.main()
