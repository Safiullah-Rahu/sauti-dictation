"""Deterministic stand-ins used by the test suite and CI (no model downloads).

FakeAsr reads a transcript embedded in the audio itself: tests generate PCM with
``encode_text`` so a given waveform always "says" a known sentence.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

import numpy as np

from .base import AsrResult, BackendUnavailable, Cancelled

_MAGIC = 12345  # first sample marker


def encode_text(text: str, lang: str = "en", seconds: float = 1.5) -> bytes:
    """PCM s16le whose first samples carry `lang|text` as UTF-8 bytes, followed by a tone."""
    payload = f"{lang}|{text}".encode()
    n = max(int(16000 * seconds), len(payload) + 2)
    pcm = np.round(0.2 * 32767 * np.sin(np.arange(n) * 2 * np.pi * 220 / 16000)).astype("<i2")
    pcm[: len(payload) + 2] = [_MAGIC, len(payload), *payload]
    return pcm.tobytes()


def decode_text(audio: np.ndarray) -> tuple[str, str] | None:
    ints = np.round(audio[:4096] * 32768).astype(np.int64)
    if len(ints) < 2 or ints[0] != _MAGIC:
        return None
    n = int(ints[1])
    raw = bytes(int(x) & 0xFF for x in ints[2:2 + n])
    lang, _, text = raw.decode(errors="replace").partition("|")
    return lang, text


class FakeAsr:
    name = "fake"

    def __init__(self, delay_s: float = 0.05, per_second: float = 0.0):
        self.delay_s = delay_s
        self.per_second = per_second
        self.calls = 0
        self.fail_next: Exception | None = None

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name}

    def transcribe(self, audio: np.ndarray, *, lang: str | None, allowed: list[str], hints: str | None,
                   cancel: threading.Event, partial: bool = False) -> AsrResult:
        self.calls += 1
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        end = time.monotonic() + self.delay_s + self.per_second * len(audio) / 16000
        while time.monotonic() < end:
            if cancel.is_set():
                raise Cancelled()
            time.sleep(0.005)
        decoded = decode_text(audio)
        if decoded is None:
            return AsrResult("", lang, 0.0, len(audio) / 16000, no_speech=True)
        spoken_lang, text = decoded
        if text.startswith("[low]"):  # tests: simulate a poorly recognised utterance
            return AsrResult(text[5:].strip(), lang or spoken_lang, 0.9, len(audio) / 16000, confidence=0.2)
        if partial:
            text = " ".join(text.split()[: max(1, len(text.split()) // 2)])
        return AsrResult(text, lang or spoken_lang, 0.99, len(audio) / 16000)


class FakeLlm:
    """Echoes the transcript with fillers removed; can be told to misbehave."""

    name = "fake"

    def __init__(self, delay_s: float = 0.02):
        self.delay_s = delay_s
        self.override: str | None = None
        self.down = False
        self.calls = 0

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name}

    async def chat(self, messages: list[dict[str, str]], *, max_tokens: int, timeout: float) -> str:
        self.calls += 1
        if self.down:
            raise BackendUnavailable("fake llm down")
        await asyncio.sleep(self.delay_s)
        if self.override is not None:
            return self.override
        content = messages[-1]["content"]
        for tag in ("transcript", "text"):
            if f"<{tag}>" in content:
                body = content.split(f"<{tag}>", 1)[1].rsplit(f"</{tag}>", 1)[0]
                if tag == "text":
                    return "[translated] " + body
                words = [w for w in body.split() if w.lower().strip(",.") not in ("um", "uh", "er")]
                out = " ".join(words)
                return out[:1].upper() + out[1:]
        return content

    async def healthy(self) -> bool:
        return not self.down
