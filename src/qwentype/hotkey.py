"""Global Right Ctrl monitor using a low-level keyboard hook (WH_KEYBOARD_LL).

The hook runs on its own thread with a Win32 message loop. The callback only
updates two booleans and emits Qt signals, then immediately passes the event
on (CallNextHookEx) — Right Ctrl is never swallowed, so shortcuts keep working.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from .win32 import IS_WINDOWS, VK_CONTROL, VK_RCONTROL

log = logging.getLogger(__name__)

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012
LLKHF_EXTENDED = 0x01

# dwExtraInfo marker on events injected by QwenType itself (see injector.py).
INJECTED_MARKER = 0x5157_5459  # "QWTY"

LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
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


class RightCtrlHook(QObject):
    """Emits pressed / released for Right Ctrl and other_key for any other key
    pressed while Right Ctrl is held (i.e. a shortcut such as Ctrl+C)."""

    pressed = Signal()
    released = Signal()
    other_key = Signal()
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._proc = None  # keep the ctypes callback alive
        self._rctrl_down = False
        self._other_reported = False

    def start(self) -> None:
        if not IS_WINDOWS:
            log.warning("Global hotkey is only available on Windows")
            return
        if self._thread is not None:
            return
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

    # -- hook thread ----------------------------------------------------------

    def _run(self, started: threading.Event) -> None:
        self._thread_id = _kernel32.GetCurrentThreadId()
        self._proc = HOOKPROC(self._callback)
        self._hook = _user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, _kernel32.GetModuleHandleW(None), 0)
        started.set()
        if not self._hook:
            err = ctypes.get_last_error()
            log.error("SetWindowsHookExW failed: %s", err)
            self.failed.emit(f"Keyboard hook failed ({err})")
            return
        log.info("Keyboard hook installed")
        msg = wintypes.MSG()
        try:
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                pass  # LL hooks are dispatched from inside GetMessage
        finally:
            _user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            log.info("Keyboard hook removed")

    def _callback(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code == 0:  # HC_ACTION
            try:
                kb = KBDLLHOOKSTRUCT.from_address(l_param)
                if kb.dwExtraInfo != INJECTED_MARKER:
                    self._handle(w_param, kb.vkCode, kb.flags)
            except Exception:  # never let an exception escape into the hook chain
                log.exception("Keyboard hook callback failed")
        return _user32.CallNextHookEx(None, n_code, w_param, l_param)

    def _handle(self, msg: int, vk: int, flags: int) -> None:
        is_down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = msg in (WM_KEYUP, WM_SYSKEYUP)
        # Right Ctrl: VK_RCONTROL, extended key (left Ctrl has no extended flag).
        is_rctrl = vk in (VK_RCONTROL, VK_CONTROL) and bool(flags & LLKHF_EXTENDED)
        if is_rctrl:
            if is_down:
                if not self._rctrl_down:  # ignore auto-repeat
                    self._rctrl_down = True
                    self._other_reported = False
                    self.pressed.emit()
            elif is_up and self._rctrl_down:
                self._rctrl_down = False
                self.released.emit()
        elif is_down and self._rctrl_down and not self._other_reported:
            self._other_reported = True
            self.other_key.emit()
