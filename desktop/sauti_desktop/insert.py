"""Put text into the application the user was dictating into.

Default method: place the text on the clipboard (marked so Windows clipboard
history and cloud clipboard skip it), send the paste shortcut, then restore the
previous clipboard. Alternatives: "type" (synthesised Unicode keystrokes) and
"clipboard" (copy only). When direct insertion is not possible - focus moved to
another window, the target runs elevated, macOS Accessibility not granted - the
text is left on the clipboard and the user is told to paste it.
"""
from __future__ import annotations

import ctypes
import logging
import sys
import time
from dataclasses import dataclass

from PySide6.QtCore import QByteArray, QMimeData, QTimer
from PySide6.QtGui import QGuiApplication

log = logging.getLogger("sauti.insert")

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


@dataclass
class Outcome:
    inserted: bool
    reason: str = ""  # focus_changed | elevated | no_accessibility | clipboard_only | no_target | error

    @property
    def message(self) -> str:
        paste = "⌘V" if IS_MAC else "Ctrl+V"
        return {
            "": "Inserted",
            "focus_changed": f"Window changed — text copied, press {paste}",
            "elevated": f"Admin window — text copied, press {paste}",
            "no_accessibility": f"Text copied — allow Accessibility to insert directly ({paste} to paste)",
            "clipboard_only": f"Copied to clipboard — press {paste}",
            "no_target": f"No text field — copied, press {paste}",
            "error": f"Could not insert — copied, press {paste}",
        }.get(self.reason, "Copied")


# --------------------------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------------------------
if IS_WIN:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_CONTROL, VK_V, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN = 0x11, 0x56, 0x10, 0x12, 0x5B, 0x5C

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    def _key(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
        return INPUT(INPUT_KEYBOARD, _INPUTUNION(ki=KEYBDINPUT(vk, scan, flags, 0, 0)))

    def _send(inputs: list) -> None:
        arr = (INPUT * len(inputs))(*inputs)
        if user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)) != len(inputs):
            raise OSError(ctypes.get_last_error(), "SendInput blocked")

    def foreground() -> int:
        return int(user32.GetForegroundWindow() or 0)

    def _class_name(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _is_elevated_pid(pid: int) -> bool | None:
        PROCESS_QUERY_LIMITED_INFORMATION, TOKEN_QUERY, TokenElevation = 0x1000, 0x0008, 20
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None  # cannot even query it: treat as protected
        try:
            tok = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h, TOKEN_QUERY, ctypes.byref(tok)):
                return None
            try:
                elevated = wintypes.DWORD()
                size = wintypes.DWORD()
                ok = advapi32.GetTokenInformation(tok, TokenElevation, ctypes.byref(elevated), 4, ctypes.byref(size))
                return bool(elevated.value) if ok else None
            finally:
                kernel32.CloseHandle(tok)
        finally:
            kernel32.CloseHandle(h)

    def _target_problem(hwnd: int) -> str:
        if not hwnd or _class_name(hwnd) in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return "no_target"
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        import os
        if _is_elevated_pid(pid.value) and not _is_elevated_pid(os.getpid()):
            return "elevated"  # Windows blocks synthetic input into elevated windows (UIPI)
        return ""

    def _wait_modifiers_released(timeout: float = 1.0) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (VK_CONTROL, VK_SHIFT, VK_MENU, VK_LWIN, VK_RWIN)):
                return
            time.sleep(0.02)

    def _paste_keys() -> None:
        _wait_modifiers_released()
        _send([_key(VK_CONTROL), _key(VK_V), _key(VK_V, flags=KEYEVENTF_KEYUP), _key(VK_CONTROL, flags=KEYEVENTF_KEYUP)])

    def _type_unicode(text: str) -> None:
        _wait_modifiers_released()
        events = []
        for unit in text.encode("utf-16-le").hex(" ", 2).split():  # UTF-16 code units, surrogates included
            code = int.from_bytes(bytes.fromhex(unit), "little")
            if code == 0x0A:
                events += [_key(0x0D), _key(0x0D, flags=KEYEVENTF_KEYUP)]
                continue
            events += [_key(0, code, KEYEVENTF_UNICODE), _key(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
        for i in range(0, len(events), 200):
            _send(events[i:i + 200])

# --------------------------------------------------------------------------------------------
# macOS
# --------------------------------------------------------------------------------------------
elif IS_MAC:
    def foreground() -> int:
        return 0  # focus tracking is not needed for paste on macOS in this version

    def _accessibility_ok() -> bool:
        try:
            lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            lib.AXIsProcessTrusted.restype = ctypes.c_bool
            return bool(lib.AXIsProcessTrusted())
        except OSError:
            return False

    def _target_problem(hwnd: int) -> str:
        return "" if _accessibility_ok() else "no_accessibility"

    def _paste_keys() -> None:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        time.sleep(0.05)
        with kb.pressed(Key.cmd):
            kb.press("v")
            kb.release("v")

    def _type_unicode(text: str) -> None:
        from pynput.keyboard import Controller
        Controller().type(text)

else:
    def foreground() -> int:
        return 0

    def _target_problem(hwnd: int) -> str:
        return ""

    def _paste_keys() -> None:
        from pynput.keyboard import Controller, Key
        kb = Controller()
        with kb.pressed(Key.ctrl):
            kb.press("v")
            kb.release("v")

    def _type_unicode(text: str) -> None:
        from pynput.keyboard import Controller
        Controller().type(text)


# --------------------------------------------------------------------------------------------
def _private_mime(text: str) -> QMimeData:
    mime = QMimeData()
    mime.setText(text)
    if IS_WIN:
        zero = QByteArray(b"\x00\x00\x00\x00")
        for fmt in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory",
                    "CanUploadToCloudClipboard"):
            mime.setData(f'application/x-qt-windows-mime;value="{fmt}"', zero)
    elif IS_MAC:
        mime.setData("application/x-nspasteboard-concealed-type", QByteArray(b"1"))  # hint for clipboard managers
    return mime


def _snapshot_clipboard() -> QMimeData | None:
    src = QGuiApplication.clipboard().mimeData()
    if src is None:
        return None
    copy = QMimeData()
    for fmt in src.formats():
        copy.setData(fmt, src.data(fmt))
    return copy


class Inserter:
    def __init__(self, method: str = "paste", restore_clipboard: bool = True):
        self.method = method
        self.restore_clipboard = restore_clipboard
        self.last_target = 0
        self.last_time = 0.0

    def copy_only(self, text: str) -> None:
        QGuiApplication.clipboard().setMimeData(_private_mime(text))

    def insert(self, text: str, target: int) -> Outcome:
        if not text:
            return Outcome(True)
        now_fg = foreground()
        if IS_WIN and target and now_fg != target:
            log.info("focus changed: started in %s (%s), now %s (%s)", target, _class_name(target), now_fg,
                     _class_name(now_fg) if now_fg else "-")
            self.copy_only(text)
            return Outcome(False, "focus_changed")
        problem = _target_problem(now_fg)
        if problem or self.method == "clipboard":
            self.copy_only(text)
            return Outcome(False, problem or "clipboard_only")
        # Consecutive dictations into the same window are separated by a space.
        if now_fg == self.last_target and time.monotonic() - self.last_time < 90 and text[:1] not in ".,;:!?)]}،؟":
            text = " " + text
        try:
            if self.method == "type":
                _type_unicode(text)
            else:
                saved = _snapshot_clipboard() if self.restore_clipboard else None
                QGuiApplication.clipboard().setMimeData(_private_mime(text))
                QGuiApplication.processEvents()
                _paste_keys()
                if saved is not None:
                    # Give the target app time to read the clipboard before restoring it.
                    QTimer.singleShot(600, lambda: QGuiApplication.clipboard().setMimeData(saved))
        except Exception as e:
            log.warning("insertion failed: %s", e)
            self.copy_only(text.lstrip())
            return Outcome(False, "error")
        self.last_target, self.last_time = now_fg, time.monotonic()
        return Outcome(True)
