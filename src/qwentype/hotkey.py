"""Global push-to-talk key monitor using low-level hooks (WH_KEYBOARD_LL, and
WH_MOUSE_LL only when a mouse button is the hotkey).

The hooks run on their own thread with a Win32 message loop. The callbacks only
update a few booleans and emit Qt signals. Modifier hotkeys (Right Ctrl, Right
Alt, Right Shift) are always passed on (CallNextHookEx), so shortcuts keep
working. Dedicated keys (Caps Lock, Scroll Lock, Pause) and the mouse side
buttons are swallowed: they would otherwise toggle a lock state or navigate back.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from dataclasses import dataclass

from PySide6.QtCore import QObject, Signal

from .settings import DEFAULT_HOTKEY
from .win32 import IS_WINDOWS

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_XBUTTONDOWN = 0x020B
WM_XBUTTONUP = 0x020C
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01

VK_ESCAPE = 0x1B
VK_LCONTROL = 0xA2
# AltGr layouts send a fake Left Ctrl with this scan code together with Right Alt.
ALTGR_FAKE_LCTRL_SCAN = 0x21D

# dwExtraInfo marker on events injected by QwenType itself (see injector.py).
INJECTED_MARKER = 0x5157_5459  # "QWTY"

LRESULT = ctypes.c_ssize_t


@dataclass(frozen=True)
class Hotkey:
    id: str
    label: str
    vks: tuple[int, ...] = ()  # keyboard: any of these virtual-key codes
    extended: bool | None = None  # required LLKHF_EXTENDED state (None = either)
    xbutton: int = 0  # mouse: XBUTTON1 = 1 (back), XBUTTON2 = 2 (forward)
    # Modifiers pass through: another key pressed while held is a shortcut (cancels the recording),
    # and the key state can be polled / waited for. Other hotkeys are swallowed.
    modifier: bool = False
    # Alt pressed and released alone opens the menu bar of the focused app: send a masking key.
    needs_mask: bool = False

    @property
    def poll_vk(self) -> int:
        """Virtual key to poll with GetAsyncKeyState (0 = can't: swallowed keys never update it)."""
        return self.vks[0] if self.modifier else 0

    @property
    def short_name(self) -> str:
        return self.label.split(" (")[0]


HOTKEYS: dict[str, Hotkey] = {
    h.id: h
    for h in (
        Hotkey("right_ctrl", "Right Ctrl", vks=(0xA3, 0x11), extended=True, modifier=True),
        Hotkey("right_alt", "Right Alt / AltGr", vks=(0xA5, 0x12), extended=True, modifier=True, needs_mask=True),
        Hotkey("right_shift", "Right Shift", vks=(0xA1,), modifier=True),
        Hotkey("caps_lock", "Caps Lock", vks=(0x14,)),
        Hotkey("scroll_lock", "Scroll Lock", vks=(0x91,)),
        Hotkey("pause", "Pause", vks=(0x13,)),
        Hotkey("mouse_back", "Mouse Back (side button)", xbutton=1),
        Hotkey("mouse_forward", "Mouse Forward (side button)", xbutton=2),
    )
}


def get_hotkey(hotkey_id: str) -> Hotkey:
    return HOTKEYS.get(hotkey_id) or HOTKEYS[DEFAULT_HOTKEY]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wintypes.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


if IS_WINDOWS:
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    _user32.SetWindowsHookExW.restype = wintypes.HHOOK
    _user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    _user32.CallNextHookEx.restype = LRESULT
    _user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    _user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    _user32.GetMessageW.restype = wintypes.BOOL
    _user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE


class HotkeyHook(QObject):
    """Emits pressed / released for the hotkey, other_key for any other key
    pressed while a modifier hotkey is held (i.e. a shortcut such as Ctrl+C),
    and escape for every Esc key press."""

    pressed = Signal()
    released = Signal()
    other_key = Signal()
    escape = Signal()
    failed = Signal(str)

    def __init__(self, hotkey: Hotkey, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.hotkey = hotkey
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._procs: list = []  # keep the ctypes callbacks alive
        self._down = False
        self._other_reported = False

    def start(self) -> None:
        if not IS_WINDOWS:
            log.warning("Global hotkey is only available on Windows")
            return
        if self._thread is not None:
            return
        self._down = False
        self._other_reported = False
        started = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(started,), name="QwenType-Hotkey", daemon=True)
        self._thread.start()
        started.wait(2.0)

    def stop(self) -> None:
        if self._thread is None:
            return
        if self._thread_id:
            _user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(2.0)
        self._thread = None
        self._thread_id = 0

    def set_hotkey(self, hotkey: Hotkey) -> None:
        """Switch keys; the hook thread is restarted (the mouse hook is only installed when needed)."""
        running = self._thread is not None
        self.stop()
        self.hotkey = hotkey
        if running:
            self.start()

    # -- hook thread ----------------------------------------------------------

    def _install(self, kind: int, callback) -> int:
        proc = HOOKPROC(callback)
        self._procs.append(proc)
        hook = _user32.SetWindowsHookExW(kind, proc, _kernel32.GetModuleHandleW(None), 0)
        if not hook:
            err = ctypes.get_last_error()
            log.error("SetWindowsHookExW(%d) failed: %s", kind, err)
            self.failed.emit(f"{'Mouse' if kind == WH_MOUSE_LL else 'Keyboard'} hook failed ({err})")
        return hook

    def _run(self, started: threading.Event) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._procs = []
        # The keyboard hook is always needed (shortcuts, Esc); a mouse hook costs a Python call
        # per mouse move, so it is only installed for a mouse-button hotkey.
        hooks = [self._install(WH_KEYBOARD_LL, self._keyboard_callback)]
        if self.hotkey.xbutton:
            hooks.append(self._install(WH_MOUSE_LL, self._mouse_callback))
        started.set()
        if not all(hooks):
            for h in hooks:
                if h:
                    _user32.UnhookWindowsHookEx(h)
            return
        log.info("Hooks installed (hotkey: %s)", self.hotkey.label)
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass  # LL hooks are dispatched from inside GetMessage
        finally:
            for h in hooks:
                _user32.UnhookWindowsHookEx(h)
            log.info("Hooks removed")

    def _keyboard_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0:  # HC_ACTION
            try:
                kb = KBDLLHOOKSTRUCT.from_address(l_param)
                if kb.dwExtraInfo != INJECTED_MARKER and self.handle_key(w_param, kb.vkCode, kb.scanCode, kb.flags):
                    return 1  # swallowed
            except Exception:  # never let an exception escape into the hook chain
                log.exception("Keyboard hook callback failed")
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _mouse_callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0 and w_param in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            try:
                ms = MSLLHOOKSTRUCT.from_address(l_param)
                if ms.dwExtraInfo != INJECTED_MARKER and self.handle_mouse(w_param, (ms.mouseData >> 16) & 0xFFFF):
                    return 1
            except Exception:
                log.exception("Mouse hook callback failed")
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    # -- event logic (platform independent, unit-tested) -------------------------

    def _set_down(self, down: bool) -> None:
        if down:
            if not self._down:  # ignore auto-repeat
                self._down = True
                self._other_reported = False
                self.pressed.emit()
        elif self._down:
            self._down = False
            self.released.emit()

    def handle_key(self, msg: int, vk: int, scan: int = 0, flags: int = 0) -> bool:
        """Process one keyboard event; returns True if it must be swallowed."""
        is_down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = msg in (WM_KEYUP, WM_SYSKEYUP)
        hk = self.hotkey
        if (
            hk.vks
            and vk in hk.vks
            and (hk.extended is None or hk.extended == bool(flags & LLKHF_EXTENDED))
            and (is_down or is_up)
        ):
            self._set_down(is_down)
            return not hk.modifier
        if vk == VK_LCONTROL and scan == ALTGR_FAKE_LCTRL_SCAN:
            return False  # part of AltGr, not a shortcut
        if is_down and vk == VK_ESCAPE:
            self.escape.emit()
        if is_down and self._down and hk.modifier and not self._other_reported:
            self._other_reported = True
            self.other_key.emit()
        return False

    def handle_mouse(self, msg: int, xbutton: int) -> bool:
        if not self.hotkey.xbutton or xbutton != self.hotkey.xbutton:
            return False
        self._set_down(msg == WM_XBUTTONDOWN)
        return True
