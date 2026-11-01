"""Global hotkeys: hold-to-talk and press-to-toggle, via pynput.

Combos use pynput's notation: "<ctrl_r>", "<ctrl>+<alt>+<space>", "<f9>".
Generic modifiers (<ctrl>, <alt>, <shift>, <cmd>) match either side; side-specific
ones (<ctrl_r>) match only that key, so Right Ctrl can be a dedicated talk key
without interfering with ordinary Ctrl shortcuts or the app's own Ctrl+V.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

from pynput import keyboard
from pynput.keyboard import Key, KeyCode

log = logging.getLogger("sauti.hotkeys")

_GENERIC = {
    Key.ctrl: {Key.ctrl, Key.ctrl_l, Key.ctrl_r},
    Key.alt: {Key.alt, Key.alt_l, Key.alt_r, getattr(Key, "alt_gr", Key.alt_r)},
    Key.shift: {Key.shift, Key.shift_l, Key.shift_r},
    Key.cmd: {Key.cmd, Key.cmd_l, Key.cmd_r},
}


def parse(combo: str) -> list:
    keys = []
    for part in combo.split("+"):
        part = part.strip()
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            name = part[1:-1]
            if not hasattr(Key, name):
                raise ValueError(f"unknown key {part}")
            keys.append(getattr(Key, name))
        else:
            keys.append(KeyCode.from_char(part.lower()))
    if not keys:
        raise ValueError("empty hotkey")
    return keys


def describe(key) -> str:
    if isinstance(key, Key):
        return f"<{key.name}>"
    if isinstance(key, KeyCode) and key.char:
        return key.char.lower()
    return f"<{getattr(key, 'vk', '?')}>"


def _same(want, got) -> bool:
    if want in _GENERIC:
        return got in _GENERIC[want]
    if isinstance(want, KeyCode) and isinstance(got, KeyCode):
        if want.char and got.char and want.char == got.char.lower():
            return True
        # With Ctrl held, Windows reports control characters; fall back to the virtual key.
        return bool(want.char and got.vk and got.vk == ord(want.char.upper()))
    return want == got


class Hotkeys:
    def __init__(self, push_to_talk: str, toggle: str, on_down: Callable[[str], None], on_up: Callable[[str], None]):
        self.on_down, self.on_up = on_down, on_up
        self.combos = {"ptt": parse(push_to_talk) if push_to_talk else [],
                       "toggle": parse(toggle) if toggle else []}
        self.pressed: set = set()
        self.active: set[str] = set()
        self.capture: Callable[[str], None] | None = None
        self._lock = threading.Lock()
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def start(self) -> None:
        self.listener.start()

    def stop(self) -> None:
        self.listener.stop()

    def update(self, push_to_talk: str, toggle: str) -> None:
        with self._lock:
            self.combos = {"ptt": parse(push_to_talk) if push_to_talk else [],
                           "toggle": parse(toggle) if toggle else []}
            self.active.clear()

    def record_next(self, callback: Callable[[str], None]) -> None:
        """Capture the next combo the user presses (for the settings dialog)."""
        self.capture = callback
        self.pressed.clear()

    def _matches(self, combo: list) -> bool:
        return bool(combo) and len(self.pressed) == len(combo) and \
            all(any(_same(w, g) for g in self.pressed) for w in combo)

    def _press(self, key) -> None:
        with self._lock:
            if key in self.pressed:
                return  # auto-repeat
            self.pressed.add(key)
            if self.capture is not None:
                return
            for name, combo in self.combos.items():
                if name not in self.active and self._matches(combo):
                    self.active.add(name)
                    self.on_down(name)

    def _release(self, key) -> None:
        with self._lock:
            if self.capture is not None and self.pressed:
                combo = "+".join(describe(k) for k in sorted(self.pressed, key=lambda k: 0 if isinstance(k, Key) else 1))
                cb, self.capture = self.capture, None
                self.pressed.clear()
                cb(combo)
                return
            self.pressed.discard(key)
            for name in list(self.active):
                combo = self.combos.get(name, [])
                if any(_same(w, key) for w in combo):
                    self.active.discard(name)
                    self.on_up(name)
