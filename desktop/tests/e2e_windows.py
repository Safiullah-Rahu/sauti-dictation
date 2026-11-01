"""End-to-end check of the Windows tray app: real hotkey -> audio -> server -> text in another app.

Starts a small target editor, starts the Sauti tray app with test audio instead
of the microphone, clicks into the editor, holds Right Ctrl for each clip like a
user would, and checks the dictated text lands in the editor exactly once.

    python desktop/tests/e2e_windows.py --server http://127.0.0.1:8700 --user amina --password-file pw.txt \
        --fake "Please send the report to Amina by Friday" "The meeting moves to room 4"
    python desktop/tests/e2e_windows.py ... --wav a.wav b.wav        # real speech against real models

Requires an interactive Windows desktop. It moves the mouse and presses keys, so
run it when nobody is using the machine.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
user32 = ctypes.WinDLL("user32", use_last_error=True)

TARGET_APP = r'''
import sys, tkinter as tk
out = sys.argv[1]
root = tk.Tk(); root.title("Notes - Sauti test target"); root.geometry("900x420+80+80")
text = tk.Text(root, font=("Segoe UI", 15), wrap="word", padx=14, pady=12); text.pack(fill="both", expand=True)
def dump():
    open(out, "w", encoding="utf-8").write(text.get("1.0", "end-1c")); root.after(300, dump)
dump(); root.after(500, lambda: (root.lift(), text.focus_force())); root.mainloop()
'''


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _U(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def send(*inputs):
    arr = (INPUT * len(inputs))(*inputs)
    user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def key(vk, up=False, extended=False):
    return INPUT(1, _U(ki=KEYBDINPUT(vk, 0, (2 if up else 0) | (1 if extended else 0), 0, 0)))


def click(x, y):
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    ax, ay = int(x * 65535 / (sw - 1)), int(y * 65535 / (sh - 1))
    move = INPUT(0, _U(mi=MOUSEINPUT(ax, ay, 0, 0x8001, 0, 0)))  # MOVE | ABSOLUTE
    down = INPUT(0, _U(mi=MOUSEINPUT(ax, ay, 0, 0x8002, 0, 0)))
    upm = INPUT(0, _U(mi=MOUSEINPUT(ax, ay, 0, 0x8004, 0, 0)))
    send(move, down, upm)


def find_window(title_part: str, timeout=15.0) -> int:
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if title_part in buf.value and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    end = time.time() + timeout
    while time.time() < end:
        found.clear()
        user32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.2)
    raise RuntimeError(f"window {title_part!r} not found")


def register(server: str, user: str, password: str) -> dict:
    body = json.dumps({"username": user, "password": password, "device_name": "e2e test", "platform": "windows"}).encode()
    req = urllib.request.Request(server + "/v1/devices/register", data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8700")
    ap.add_argument("--user", default="amina")
    ap.add_argument("--password-file", type=Path, required=True)
    ap.add_argument("--fake", nargs="*", default=[], help="phrases encoded for the fake recogniser")
    ap.add_argument("--wav", nargs="*", default=[], help="real speech WAV files")
    ap.add_argument("--python", default=str(ROOT.parent / ".venv-desktop" / "Scripts" / "python.exe"))
    ap.add_argument("--exe", default="", help="test a packaged build (path to Sauti.exe) instead of the source")
    ap.add_argument("--keep", action="store_true", help="leave the windows open afterwards")
    ap.add_argument("--settle", type=float, default=60.0, help="max seconds to wait for each dictation")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="sauti-e2e-"))
    clips: list[tuple[str, float]] = []
    if args.fake:
        sys.path.insert(0, str(ROOT / "server"))
        from sauti_server.backends.fake import encode_text
        import struct
        for i, phrase in enumerate(args.fake):
            pcm = encode_text(phrase, "en", 2.0)  # exact samples: the fake recogniser reads them back
            header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
                "<IHHIIHH", 16, 1, 1, 16000, 32000, 2, 16) + b"data" + struct.pack("<I", len(pcm))
            path = work / f"fake{i}.wav"
            path.write_bytes(header + pcm)
            clips.append((str(path), 2.0))
    for w in args.wav:
        sys.path.insert(0, str(ROOT / "bench"))
        from audio import read_wav
        x, sr = read_wav(w)
        clips.append((w, len(x) / sr))

    reg = register(args.server, args.user, args.password_file.read_text(encoding="utf-8").strip())
    cfg_dir = work / "config"
    cfg_dir.mkdir()
    (cfg_dir / "settings.json").write_text(json.dumps({"server_url": args.server, "username": args.user,
                                                       "device_id": reg["device_id"], "device_name": "e2e test"}))
    notes = work / "notes.txt"
    target = subprocess.Popen([sys.executable, "-c", TARGET_APP, str(notes)])
    env = {**os.environ, "SAUTI_CONFIG_DIR": str(cfg_dir), "SAUTI_DEVICE_TOKEN": reg["device_token"]}
    launch = [args.exe] if args.exe else [args.python, "-m", "sauti_desktop"]
    tray = subprocess.Popen(launch + ["--test-audio", ",".join(c for c, _ in clips)], cwd=str(ROOT / "desktop"), env=env)
    try:
        hwnd = find_window("Sauti test target")
        time.sleep(4)  # tray app connects
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        click((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
        time.sleep(0.5)
        VK_RCONTROL = 0xA3
        for i, (path, dur) in enumerate(clips):
            before = notes.read_text(encoding="utf-8") if notes.exists() else ""
            print(f"[{i + 1}/{len(clips)}] holding Right Ctrl for {dur + 0.5:.1f} s ({Path(path).name})", flush=True)
            send(key(VK_RCONTROL, extended=True))
            time.sleep(dur + 0.5)
            send(key(VK_RCONTROL, up=True, extended=True))
            t0 = time.time()
            while time.time() - t0 < args.settle:
                now = notes.read_text(encoding="utf-8") if notes.exists() else ""
                if now != before:
                    time.sleep(1.0)  # let any (unexpected) duplicate arrive before we compare
                    break
                time.sleep(0.2)
            print(f"    text after {time.time() - t0:.1f} s: {notes.read_text(encoding='utf-8')!r}", flush=True)
            time.sleep(1.0)
        final = notes.read_text(encoding="utf-8")
        print("FINAL TEXT:", repr(final))
        ok = True
        for phrase in args.fake:
            n = final.lower().count(phrase.lower()[:20])
            print(f"  {phrase!r}: inserted {n}x")
            ok &= n == 1
        print("RESULT:", "PASS" if ok else "FAIL")
    finally:
        if not args.keep:
            tray.terminate()
            target.terminate()


if __name__ == "__main__":
    main()
