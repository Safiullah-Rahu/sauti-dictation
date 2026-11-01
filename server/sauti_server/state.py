"""Process-wide state: live sessions, pairing codes, rate limits."""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from . import security as sec
from .backends.base import Backends
from .config import Config
from .db import Principal, Store
from .pipeline import Pipeline
from .scheduler import Stage


@dataclass(eq=False)
class Session:
    id: str
    ws: WebSocket
    principal: Principal
    remote: str
    client: str
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    state: str = "idle"  # idle | recording | processing
    utterances: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False

    async def send(self, msg: dict[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            async with self._lock:
                await self.ws.send_text(json.dumps(msg, ensure_ascii=False))
            return True
        except Exception:
            self.closed = True
            return False

    def public(self) -> dict[str, Any]:
        p = self.principal
        return {"session_id": self.id, "user_id": p.user_id, "username": p.username, "device": p.device_name,
                "platform": p.platform, "kind": p.kind, "client": self.client, "remote": self.remote,
                "connected_at": self.connected_at, "last_activity": self.last_activity, "state": self.state,
                "utterances": self.utterances}


class SessionRegistry:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def add(self, s: Session) -> None:
        self.sessions[s.id] = s

    def remove(self, s: Session) -> None:
        self.sessions.pop(s.id, None)

    def for_user(self, user_id: int) -> list[Session]:
        return [s for s in self.sessions.values() if s.principal.user_id == user_id]

    async def notify_user(self, user_id: int, msg: dict[str, Any]) -> None:
        for s in self.for_user(user_id):
            await s.send(msg)

    async def close_where(self, pred, code: int = 4401, reason: str = "revoked") -> int:
        n = 0
        for s in list(self.sessions.values()):
            if pred(s):
                n += 1
                await s.send({"type": "error", "code": "unauthorized", "message": reason, "retryable": False})
                try:
                    await s.ws.close(code=code, reason=reason)
                except Exception:
                    pass
                s.closed = True
                self.remove(s)
        return n


@dataclass
class _PairingCode:
    user_id: int
    dek: bytes
    expires: float


class Pairing:
    """Pairing codes live only in memory: they carry an unwrapped data key for a few minutes."""

    TTL = 600

    def __init__(self):
        self.codes: dict[str, _PairingCode] = {}

    def create(self, user_id: int, dek: bytes) -> tuple[str, float]:
        self.purge()
        for code, entry in list(self.codes.items()):
            if entry.user_id == user_id:
                del self.codes[code]
        while True:
            code = f"{secrets.randbelow(10**6):06d}"
            if code not in self.codes:
                break
        exp = time.time() + self.TTL
        self.codes[code] = _PairingCode(user_id, dek, exp)
        return code, exp

    def claim(self, code: str) -> _PairingCode | None:
        self.purge()
        code = "".join(ch for ch in code if ch.isdigit())
        for known in list(self.codes):
            if sec.constant_time_eq(known, code):
                return self.codes.pop(known)
        return None

    def purge(self) -> None:
        now = time.time()
        for code in [c for c, e in self.codes.items() if e.expires < now]:
            del self.codes[code]


class RateLimiter:
    def __init__(self, limit: int, window_s: float):
        self.limit = limit
        self.window = window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


@dataclass
class AppState:
    cfg: Config
    store: Store
    backends: Backends
    asr_stage: Stage
    llm_stage: Stage | None
    mt_stage: Stage | None
    pipeline: Pipeline
    sessions: SessionRegistry = field(default_factory=SessionRegistry)
    pairing: Pairing = field(default_factory=Pairing)
    login_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(10, 60))
    pairing_limiter: RateLimiter = field(default_factory=lambda: RateLimiter(5, 60))
    started_at: float = field(default_factory=time.time)
    llm_healthy: bool | None = None
    inflight: dict = field(default_factory=dict)  # (user_id, utterance_id) -> InFlight

    def stages(self) -> list[Stage]:
        return [s for s in (self.asr_stage, self.llm_stage, self.mt_stage) if s is not None]
