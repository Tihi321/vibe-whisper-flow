"""Insert text into the focused application: clipboard + Ctrl+V, or simulated typing."""
from __future__ import annotations

import ctypes
import logging
import time
from ctypes import wintypes

from .config import OutputConfig

log = logging.getLogger(__name__)

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x2
INPUT_KEYBOARD = 1
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT included so sizeof(INPUT) is correct (40 bytes on x64).
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


def _key(vk: int, up: bool) -> INPUT:
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0, time=0,
                        dwExtraInfo=0)
    return inp


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %d/%d events", sent, len(inputs))


def send_ctrl_v() -> None:
    _send(_key(VK_CONTROL, False), _key(VK_V, False))
    time.sleep(0.01)
    _send(_key(VK_V, True), _key(VK_CONTROL, True))


def _open_clipboard(win32clipboard) -> bool:
    for _ in range(5):
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.02)
    return False


def get_clipboard_text() -> str | None:
    import win32clipboard
    import win32con

    if not _open_clipboard(win32clipboard):
        log.debug("clipboard busy (get)")
        return None
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        return None
    except Exception:
        log.debug("get clipboard failed", exc_info=True)
        return None
    finally:
        win32clipboard.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    import win32clipboard
    import win32con

    if not _open_clipboard(win32clipboard):
        raise OSError("Clipboard is busy")
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    finally:
        win32clipboard.CloseClipboard()


def type_text(text: str) -> None:
    from pynput.keyboard import Controller

    kb = Controller()
    for i in range(0, len(text), 64):
        kb.type(text[i:i + 64])
        time.sleep(0.005)


def insert_text(text: str, cfg: OutputConfig, chord=None) -> None:
    if not text:
        return
    if chord is not None:
        try:
            from .hotkeys import wait_for_chord_release
        except Exception:
            log.debug("hotkeys unavailable; skipping chord release wait", exc_info=True)
        else:
            wait_for_chord_release(chord, 1.0)
    log.debug("inserting %d chars via %s", len(text), cfg.mode)
    if cfg.mode == "type":
        type_text(text)
        return
    prev = get_clipboard_text() if cfg.restore_clipboard else None
    set_clipboard_text(text)
    time.sleep(cfg.paste_delay_ms / 1000.0)
    send_ctrl_v()
    if cfg.restore_clipboard and prev is not None:
        time.sleep(0.3)
        set_clipboard_text(prev)
