"""Minimal WAV reading/writing (PCM16 and float32) without extra dependencies."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


def read_wav(path: str | Path) -> tuple[np.ndarray, int]:
    """Return (mono float32 samples in [-1, 1], sample rate)."""
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a WAV file")
    pos, fmt, rate, channels, bits, pcm = 12, None, 0, 1, 16, None
    while pos + 8 <= len(data):
        cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body = data[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt, channels, rate = struct.unpack("<HHI", body[:8])
            bits = struct.unpack("<H", body[14:16])[0]
            if fmt == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: real format in the sub-format GUID
                fmt = struct.unpack("<H", body[24:26])[0]
        elif cid == b"data":
            pcm = body
        pos += 8 + size + (size & 1)
    if pcm is None or fmt is None:
        raise ValueError(f"{path}: missing fmt/data chunk")
    if fmt == 3 and bits == 32:
        x = np.frombuffer(pcm, dtype="<f4")
    elif fmt == 1 and bits == 16:
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    else:
        raise ValueError(f"{path}: unsupported WAV format {fmt}/{bits}-bit")
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    return x.astype(np.float32), rate


def resample(x: np.ndarray, src: int, dst: int = 16000) -> np.ndarray:
    if src == dst:
        return x
    n = int(round(len(x) * dst / src))
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def to_pcm16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def write_wav(path: str | Path, x: np.ndarray, rate: int = 16000) -> None:
    pcm = to_pcm16(x)
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    Path(path).write_bytes(header + pcm)
