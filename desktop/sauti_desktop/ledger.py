"""Insertion ledger: the ids of utterances whose text was already inserted.

A final that arrives twice (after a reconnect, a retry or a server replay) is
recognised here and never typed a second time. Only ids are stored - no text.
"""
from __future__ import annotations

import json
import threading
from collections import OrderedDict
from pathlib import Path


class Ledger:
    def __init__(self, path: Path | None, size: int = 500):
        self.path = path
        self.size = size
        self._ids: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()
        if path and path.exists():
            try:
                for uid in json.loads(path.read_text(encoding="utf-8"))[-size:]:
                    self._ids[uid] = None
            except (OSError, ValueError):
                pass

    def claim(self, utterance_id: str) -> bool:
        """Record the id; True if this is the first time (caller may insert)."""
        with self._lock:
            if utterance_id in self._ids:
                return False
            self._ids[utterance_id] = None
            while len(self._ids) > self.size:
                self._ids.popitem(last=False)
            self._save()
            return True

    def __contains__(self, utterance_id: str) -> bool:
        with self._lock:
            return utterance_id in self._ids

    def _save(self) -> None:
        if self.path:
            try:
                self.path.write_text(json.dumps(list(self._ids)), encoding="utf-8")
            except OSError:
                pass
