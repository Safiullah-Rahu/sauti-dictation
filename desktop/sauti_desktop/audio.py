"""Microphone capture (16 kHz mono PCM16, 100 ms blocks) and a WAV test source.

The microphone stream is opened only while recording and closed immediately
after, so the operating system's microphone indicator reflects real use.
"""
from __future__ import annotations

import logging
import struct
import threading
import time
from pathlib import Path
from typing import Callable

import numpy as np

log = logging.getLogger("sauti.audio")
RATE = 16000
BLOCK = 1600  # 100 ms

OnChunk = Callable[[bytes, float], None]  # pcm16 bytes, peak level 0..1


def list_inputs() -> list[str]:
    import sounddevice as sd
    try:
        return [d["name"] for d in sd.query_devices() if d["max_input_channels"] > 0]
    except Exception:
        return []


class MicSource:
    def __init__(self, device: str = ""):
        self.device = device or None
        self.stream = None

    def start(self, on_chunk: OnChunk) -> None:
        import sounddevice as sd

        try:
            self.stream = sd.InputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK,
                                         device=self.device, callback=self._cb(on_chunk, RATE))
        except Exception:
            # Some devices refuse 16 kHz: capture at their native rate and resample.
            info = sd.query_devices(self.device, "input")
            native = int(info["default_samplerate"])
            self.stream = sd.InputStream(samplerate=native, channels=1, dtype="int16",
                                         blocksize=int(native / 10), device=self.device,
                                         callback=self._cb(on_chunk, native))
        self.stream.start()

    @staticmethod
    def _cb(on_chunk: OnChunk, rate: int):
        def callback(indata, frames, _time, status):  # audio thread
            x = indata[:, 0]
            if rate != RATE:
                f = x.astype(np.float32)
                n = int(round(len(f) * RATE / rate))
                x = np.interp(np.linspace(0, len(f) - 1, n), np.arange(len(f)), f).astype("<i2")
            peak = float(np.abs(x).max()) / 32768.0 if len(x) else 0.0
            on_chunk(x.astype("<i2").tobytes(), peak)
        return callback

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None


def read_wav_pcm16(path: str | Path) -> bytes:
    """Load a WAV (PCM16 or float32, any rate, mono/stereo) as 16 kHz mono PCM16."""
    data = Path(path).read_bytes()
    pos, fmt, rate, ch, bits, pcm = 12, 1, RATE, 1, 16, b""
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt, ch, rate = struct.unpack("<HHI", body[:8])
            bits = struct.unpack("<H", body[14:16])[0]
            if fmt == 0xFFFE:
                fmt = struct.unpack("<H", body[24:26])[0]
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    x = np.frombuffer(pcm, dtype="<f4") if (fmt == 3 and bits == 32) else \
        np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != RATE:
        n = int(round(len(x) * RATE / rate))
        x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x)
    return np.round(np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


class FileSource:
    """Test hook: plays WAV files in real time through the same path as the microphone.

    Used for automated tests and recorded demos (``--test-audio a.wav,b.wav``); each
    recording takes the next file. The overlay labels the source as test audio.
    """

    def __init__(self, paths: list[str]):
        self.clips = [read_wav_pcm16(p) for p in paths]
        self.index = 0
        self._stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self, on_chunk: OnChunk) -> None:
        clip = self.clips[self.index % len(self.clips)]
        self.index += 1
        self._stop.clear()

        def run():
            t0 = time.monotonic()
            step = BLOCK * 2
            silence = b"\x00\x00" * BLOCK
            for i in range(0, 10**9, step):
                if self._stop.is_set():
                    return
                chunk = clip[i:i + step] if i < len(clip) else silence
                if len(chunk) < step:
                    chunk += b"\x00" * (step - len(chunk))
                x = np.frombuffer(chunk, dtype="<i2")
                on_chunk(chunk, float(np.abs(x).max()) / 32768.0)
                time.sleep(max(0.0, t0 + (i // step + 1) * 0.1 - time.monotonic()))

        self.thread = threading.Thread(target=run, name="test-audio", daemon=True)
        self.thread.start()

    def duration_of_next(self) -> float:
        return len(self.clips[self.index % len(self.clips)]) / 2 / RATE

    def stop(self) -> None:
        self._stop.set()
