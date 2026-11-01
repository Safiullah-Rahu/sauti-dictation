"""CPU / GPU selection for the CTranslate2 models (Whisper, MADLAD) and GPU monitoring.

``device = "auto"`` uses the first NVIDIA GPU when CTranslate2 can see one and falls
back to the CPU otherwise, so the same config works before and after a GPU is added.
``compute_type = "auto"`` picks float16 on a GPU and int8 on the CPU.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Any

from .backends.base import BackendUnavailable

log = logging.getLogger("sauti.devices")


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception:
        return 0


def resolve_device(requested: str, count: int | None = None) -> str:
    """Map "auto" | "cpu" | "cuda" to the device actually used."""
    requested = (requested or "auto").lower()
    count = cuda_device_count() if count is None else count
    if requested == "cpu":
        return "cpu"
    if requested == "cuda":
        if count == 0:
            raise BackendUnavailable("device = \"cuda\" but no CUDA GPU is visible (driver, passthrough or "
                                     "CUDA libraries missing); use \"auto\" to fall back to the CPU")
        return "cuda"
    if requested == "auto":
        return "cuda" if count > 0 else "cpu"
    raise ValueError(f"unknown device {requested!r} (use auto, cpu or cuda)")


def resolve_compute_type(requested: str, device: str) -> str:
    requested = (requested or "auto").lower()
    if requested != "auto":
        return requested
    return "float16" if device == "cuda" else "int8"


def parse_nvidia_smi(text: str) -> list[dict[str, Any]]:
    """Parse `nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu
    --format=csv,noheader,nounits`."""
    gpus = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue

        def num(v: str) -> float | None:
            try:
                return float(v)
            except ValueError:
                return None

        gpus.append({"index": int(num(parts[0]) or 0), "name": parts[1], "util_percent": num(parts[2]),
                     "memory_used_mb": num(parts[3]), "memory_total_mb": num(parts[4]), "temperature_c": num(parts[5])})
    return gpus


_cache: tuple[float, list[dict[str, Any]]] = (0.0, [])


def gpu_snapshot(max_age_s: float = 2.0) -> list[dict[str, Any]]:
    """Live GPU numbers for the admin dashboard; empty when there is no NVIDIA GPU."""
    global _cache
    if time.monotonic() - _cache[0] < max_age_s:
        return _cache[1]
    gpus: list[dict[str, Any]] = []
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run([exe, "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,"
                                  "temperature.gpu", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=3)
            gpus = parse_nvidia_smi(out.stdout) if out.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            gpus = []
    _cache = (time.monotonic(), gpus)
    return gpus


def describe() -> dict[str, Any]:
    info: dict[str, Any] = {"cuda_devices": cuda_device_count()}
    try:
        import ctranslate2

        info["ctranslate2"] = ctranslate2.__version__
        info["cpu_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cpu"))
        if info["cuda_devices"]:
            info["cuda_compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda"))
    except Exception as e:  # ctranslate2 missing (fake backends) or no CUDA build
        info["note"] = str(e)[:120]
    info["gpus"] = gpu_snapshot()
    return info
