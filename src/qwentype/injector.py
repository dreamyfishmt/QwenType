"""Types text into the focused window.

Primary path: SendInput with KEYEVENTF_UNICODE (UTF-16 code units, so
characters outside the BMP are sent as surrogate pairs). Works under CJK IMEs
without switching them. Fallback for long text or apps that drop Unicode input:
paste via the clipboard and restore the previous clipboard contents afterwards.
"""

from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from .hotkey import INJECTED_MARKER
from .win32 import IS_WINDOWS, foreground_process_name, is_key_down

log = logging.getLogger(__name__)

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_RETURN = 0x0D
VK_V = 0x56
# Unassigned virtual key: pressed while Alt is held so that releasing Alt doesn't open the menu bar.
VK_MASK = 0xE8

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
MAX_CLIPBOARD_BYTES = 64 * 1024 * 1024

# Formats whose handle is not an HGLOBAL (GDI objects, metafiles, owner display),
# or that Windows synthesizes from another format anyway.
_SKIP_FORMATS = {2, 3, 9, 14, 0x0080, 0x0082, 0x0083, 0x008E}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _user32.OpenClipboard.argtypes = [wintypes.HWND]
    _user32.OpenClipboard.restype = wintypes.BOOL
    _user32.CloseClipboard.restype = wintypes.BOOL
    _user32.EmptyClipboard.restype = wintypes.BOOL
    _user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    _user32.EnumClipboardFormats.restype = wintypes.UINT
    _user32.GetClipboardData.argtypes = [wintypes.UINT]
    _user32.GetClipboardData.restype = wintypes.HANDLE
    _user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _user32.SetClipboardData.restype = wintypes.HANDLE
    _user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    _user32.RegisterClipboardFormatW.restype = wintypes.UINT
    _kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    _kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    _kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalLock.restype = ctypes.c_void_p
    _kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    _kernel32.GlobalSize.restype = ctypes.c_size_t


class InjectionError(Exception):
    pass


def utf16_units(text: str) -> list[int]:
    """UTF-16 code units; non-BMP characters become surrogate pairs."""
    data = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(data[i : i + 2], "little") for i in range(0, len(data), 2)]


def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(vk, scan, flags, 0, INJECTED_MARKER)
    return inp


def _send(events: list[INPUT]) -> int:
    if not events:
        return 0
    arr = (INPUT * len(events))(*events)
    return _user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def send_mask_key() -> None:
    if IS_WINDOWS:
        _send([_key(VK_MASK), _key(VK_MASK, flags=KEYEVENTF_KEYUP)])


def wait_for_key_release(vk: int, timeout: float = 30.0) -> bool:
    """Typed characters must not combine with a held modifier (the hotkey) into shortcuts."""
    if not vk:
        return True
    deadline = time.monotonic() + timeout
    while is_key_down(vk):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def type_unicode(text: str, batch_chars: int = 32) -> bool:
    """Returns False if SendInput was blocked (e.g. UIPI)."""
    text = text.replace("\r\n", "\n")
    events: list[INPUT] = []
    for ch in text:
        if ch == "\n":
            events += [_key(VK_RETURN), _key(VK_RETURN, flags=KEYEVENTF_KEYUP)]
            continue
        for unit in utf16_units(ch):
            events += [
                _key(scan=unit, flags=KEYEVENTF_UNICODE),
                _key(scan=unit, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
            ]
    step = batch_chars * 2
    for i in range(0, len(events), step):
        batch = events[i : i + step]
        sent = _send(batch)
        if sent != len(batch):
            log.warning("SendInput inserted %d of %d events (error %d)", sent, len(batch), ctypes.get_last_error())
            return i > 0 or sent > 0  # partially typed: don't paste the text twice
        time.sleep(0.005)  # let the target's message queue keep up
    return True


# --- clipboard ---------------------------------------------------------------


def _open_clipboard(retries: int = 20) -> None:
    for _ in range(retries):
        if _user32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise InjectionError("Clipboard is busy")


def _alloc(data: bytes) -> int:
    h = _kernel32.GlobalAlloc(GMEM_MOVEABLE, max(1, len(data)))
    if not h:
        raise MemoryError("GlobalAlloc failed")
    p = _kernel32.GlobalLock(h)
    if not p:
        _kernel32.GlobalFree(h)
        raise MemoryError("GlobalLock failed")
    ctypes.memmove(p, data, len(data))
    _kernel32.GlobalUnlock(h)
    return h


def _set_data(fmt: int, data: bytes) -> None:
    h = _alloc(data)
    if not _user32.SetClipboardData(fmt, h):
        _kernel32.GlobalFree(h)  # ownership only transfers on success


def _save_clipboard() -> list[tuple[int, bytes]]:
    saved: list[tuple[int, bytes]] = []
    total = 0
    _open_clipboard()
    try:
        fmt = 0
        while True:
            fmt = _user32.EnumClipboardFormats(fmt)
            if not fmt:
                break
            if fmt in _SKIP_FORMATS or 0x0300 <= fmt <= 0x03FF:  # CF_GDIOBJFIRST..LAST
                continue
            h = _user32.GetClipboardData(fmt)
            if not h:
                continue
            size = _kernel32.GlobalSize(h)
            if not size or total + size > MAX_CLIPBOARD_BYTES:
                continue
            p = _kernel32.GlobalLock(h)
            if not p:
                continue
            try:
                saved.append((fmt, ctypes.string_at(p, size)))
                total += size
            finally:
                _kernel32.GlobalUnlock(h)
    finally:
        _user32.CloseClipboard()
    return saved


def _set_clipboard_text(text: str) -> None:
    _open_clipboard()
    try:
        _user32.EmptyClipboard()
        _set_data(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")
        # Keep the transient text out of clipboard history / cloud clipboard.
        for name, value in (
            ("ExcludeClipboardContentFromMonitorProcessing", b"\x00"),
            ("CanIncludeInClipboardHistory", b"\x00\x00\x00\x00"),
            ("CanUploadToCloudClipboard", b"\x00\x00\x00\x00"),
        ):
            fmt = _user32.RegisterClipboardFormatW(name)
            if fmt:
                _set_data(fmt, value)
    finally:
        _user32.CloseClipboard()


def _restore_clipboard(saved: list[tuple[int, bytes]]) -> None:
    _open_clipboard()
    try:
        _user32.EmptyClipboard()
        for fmt, data in saved:
            try:
                _set_data(fmt, data)
            except MemoryError:
                pass
    finally:
        _user32.CloseClipboard()


def paste_via_clipboard(text: str) -> None:
    saved: list[tuple[int, bytes]] | None
    try:
        saved = _save_clipboard()
    except Exception as e:
        log.warning("Could not save clipboard: %s", e)
        saved = None  # unknown contents: leave the pasted text there rather than wiping it
    _set_clipboard_text(text)
    time.sleep(0.03)
    sent = _send(
        [
            _key(VK_CONTROL),
            _key(VK_V),
            _key(VK_V, flags=KEYEVENTF_KEYUP),
            _key(VK_CONTROL, flags=KEYEVENTF_KEYUP),
        ]
    )
    # The target reads the clipboard asynchronously after Ctrl+V.
    time.sleep(0.35)
    if saved is not None:
        try:
            _restore_clipboard(saved)
        except Exception as e:
            log.warning("Could not restore clipboard: %s", e)
    if sent != 4:
        raise InjectionError("Input blocked (run as administrator?)")


def inject_text(
    text: str, unicode_max_chars: int = 200, clipboard_apps: list[str] | None = None, wait_vk: int = 0
) -> None:
    """Blocking; call from a worker thread. Raises InjectionError with a short message."""
    if not text:
        return
    if not IS_WINDOWS:
        log.info("Text injection is only available on Windows (%d chars)", len(text))
        return
    if not wait_for_key_release(wait_vk):
        raise InjectionError("Hotkey still held")
    proc = foreground_process_name()
    force_paste = proc and proc in {a.lower() for a in (clipboard_apps or [])}
    if len(text) > unicode_max_chars or force_paste:
        log.info("Injecting %d chars via clipboard (%s)", len(text), proc or "?")
        paste_via_clipboard(text)
        return
    log.info("Typing %d chars into %s", len(text), proc or "?")
    if not type_unicode(text):
        log.info("Unicode input was blocked, falling back to the clipboard")
        paste_via_clipboard(text)
