"""Resource and health numbers for the admin dashboard. Nothing here carries content."""
from __future__ import annotations

import os
import time
from typing import Any

import psutil

_START = time.time()
_proc = psutil.Process(os.getpid())
psutil.cpu_percent(interval=None)


def _related_processes() -> list[psutil.Process]:
    out = [_proc]
    for p in psutil.process_iter(["name"]):
        name = (p.info.get("name") or "").lower()
        if name.startswith("llama-server"):
            out.append(p)
    return out


def system_snapshot(data_dir: str) -> dict[str, Any]:
    vm = psutil.virtual_memory()
    procs = []
    for p in _related_processes():
        try:
            with p.oneshot():
                procs.append({"pid": p.pid, "name": p.name(), "rss_mb": round(p.memory_info().rss / 2**20),
                              "cpu_percent": p.cpu_percent(interval=None), "threads": p.num_threads()})
        except psutil.Error:
            continue
    try:
        disk = psutil.disk_usage(data_dir)
        disk_info = {"free_gb": round(disk.free / 2**30, 1), "total_gb": round(disk.total / 2**30, 1)}
    except OSError:
        disk_info = {}
    load = None
    if hasattr(os, "getloadavg"):
        try:
            load = [round(x, 2) for x in os.getloadavg()]
        except OSError:
            load = None
    return {
        "uptime_s": int(time.time() - _START),
        "cpu_percent": psutil.cpu_percent(interval=None),
        "cpu_count": psutil.cpu_count(),
        "load_avg": load,
        "memory": {"used_gb": round((vm.total - vm.available) / 2**30, 1), "total_gb": round(vm.total / 2**30, 1),
                   "percent": vm.percent},
        "disk": disk_info,
        "processes": procs,
    }
