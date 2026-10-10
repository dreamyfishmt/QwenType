"""Small ctypes wrappers around the Win32 API (single instance, autostart,
window styles, acrylic backdrop, key state, foreground window info).

Every function degrades to a harmless no-op on other platforms so the rest of
the package can be imported (and unit-tested) anywhere.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

APP_NAME = "QwenType"
MUTEX_NAME = r"Local\QwenType.SingleInstance"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

VK_CONTROL = 0x11
VK_RCONTROL = 0xA3

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetWindowRect.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
    user32.SetWindowRgn.restype = ctypes.c_int
    gdi32.CreateRoundRectRgn.argtypes = [ctypes.c_int] * 6
    gdi32.CreateRoundRectRgn.restype = wintypes.HANDLE
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL


# --- single instance ---------------------------------------------------------

_mutex_handle = None


def acquire_single_instance() -> bool:
    """Return False if another QwenType instance already holds the named mutex."""
    global _mutex_handle
    if not IS_WINDOWS:
        return True
    handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    err = ctypes.get_last_error()
    if not handle:
        log.warning("CreateMutexW failed (%s); continuing without single-instance check", err)
        return True
    if err == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return False
    _mutex_handle = handle  # keep alive for the process lifetime
    return True


# --- keyboard ----------------------------------------------------------------


def is_key_down(vk: int) -> bool:
    if not IS_WINDOWS:
        return False
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# --- foreground window -------------------------------------------------------


def foreground_window_center() -> tuple[int, int] | None:
    """Center of the foreground window in physical screen pixels."""
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def foreground_process_name() -> str:
    """Lower-case exe name of the foreground window's process ("" if unknown)."""
    if not IS_WINDOWS:
        return ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return ""
        return buf.value.replace("/", "\\").rsplit("\\", 1)[-1].lower()
    finally:
        kernel32.CloseHandle(handle)


# --- window styling ----------------------------------------------------------

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000


def make_overlay_window(hwnd: int) -> None:
    """Add WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW (never focused, not in Alt+Tab)."""
    if not IS_WINDOWS or not hwnd:
        return
    style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    new = (style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_TRANSPARENT) & ~WS_EX_APPWINDOW
    if new != style:
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020)


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),  # AABBGGRR
        ("AnimationId", ctypes.c_int),
    ]


class _WindowCompositionAttribData(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.c_void_p),
        ("SizeOfData", ctypes.c_size_t),
    ]


ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19


def enable_blur_behind(hwnd: int, tint_abgr: int = 0x99201C1A) -> bool:
    """Acrylic blur behind the window via SetWindowCompositionAttribute.

    Falls back to the plain (non-acrylic) blur. Returns False if neither is
    available, in which case the caller paints an opaque-ish background.
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        fn = user32.SetWindowCompositionAttribute
    except AttributeError:
        return False
    fn.argtypes = [wintypes.HWND, ctypes.POINTER(_WindowCompositionAttribData)]
    fn.restype = wintypes.BOOL
    for state in (ACCENT_ENABLE_ACRYLICBLURBEHIND, ACCENT_ENABLE_BLURBEHIND):
        accent = _AccentPolicy(state, 2, tint_abgr, 0)
        data = _WindowCompositionAttribData(
            WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p), ctypes.sizeof(accent)
        )
        if fn(hwnd, ctypes.byref(data)):
            return True
    return False


def set_round_region(hwnd: int, width: int, height: int, radius: int) -> None:
    """Clip the window (and its blur) to a rounded rectangle, in physical pixels."""
    if not IS_WINDOWS or not hwnd or width <= 0 or height <= 0:
        return
    rgn = gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius * 2, radius * 2)
    if rgn and not user32.SetWindowRgn(hwnd, rgn, True):
        gdi32.DeleteObject(rgn)  # on success the system owns the region


# --- autostart ---------------------------------------------------------------


def autostart_command() -> str:
    """Command line used for the Run key."""
    import os

    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    argv0 = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if argv0.lower().endswith(".exe") and os.path.exists(argv0):
        return f'"{argv0}"'  # uv-generated qwentype.exe launcher
    exe = sys.executable
    pythonw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pythonw):
        exe = pythonw
    return f'"{exe}" -m qwentype'


def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    if not IS_WINDOWS:
        return
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
