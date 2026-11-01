"""Emulate the target VM on a bigger workstation: pin to N logical CPUs and raise priority.

On the Proxmox host the equivalent is a VM with a fixed vCPU count (CPU type
"host"), CPU affinity, and a higher CPU weight (cpuunits) than the other guests.
"""
from __future__ import annotations

import os

import psutil


LEVELS = {"high": psutil.HIGH_PRIORITY_CLASS, "above_normal": psutil.ABOVE_NORMAL_PRIORITY_CLASS,
          "normal": psutil.NORMAL_PRIORITY_CLASS} if os.name == "nt" else {"high": -10, "above_normal": -5, "normal": 0}


def pin(cpus: str | None, priority: str = "high", pid: int | None = None) -> str:
    p = psutil.Process(pid or os.getpid())
    note = []
    if cpus:
        ids = []
        for part in cpus.split(","):
            a, _, b = part.partition("-")
            ids.extend(range(int(a), int(b or a) + 1))
        p.cpu_affinity(ids)
        note.append(f"affinity={ids[0]}-{ids[-1]}")
    if priority:
        p.nice(LEVELS[priority])
        note.append(f"priority={priority}")
    return ",".join(note)


if __name__ == "__main__":  # pin an already running process: python pinning.py <pid> 0-11 high
    import sys
    print(pin(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "high", int(sys.argv[1])))
