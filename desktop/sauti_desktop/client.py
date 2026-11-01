"""WebSocket dictation client: streaming, reconnect/resume, retries and exactly-once delivery.

Runs its own asyncio loop on a background thread. Public methods are thread-safe
and may be called from the UI thread or the audio callback. Results are reported
through the ``Events`` callbacks, which are invoked on the client thread.

Implements docs/protocol.md section 3.4:
* audio for an utterance is held in memory until its final arrives;
* after a dropped connection every pending utterance is resumed or resent with
  the same id, so the server can de-duplicate;
* the insertion ledger guarantees a final is delivered for insertion only once;
* finals are delivered in the order the utterances were started.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import websockets

from . import __version__
from .api import ssl_context
from .ledger import Ledger

log = logging.getLogger("sauti.client")
BACKOFF = [0.5, 1, 2, 4, 8]
GIVE_UP_S = 120.0


class Events(Protocol):
    def connection(self, state: str, detail: str) -> None: ...  # connecting | ready | offline | unauthorized
    def partial(self, utterance_id: str, text: str) -> None: ...
    def status(self, utterance_id: str, state: str, position: int | None) -> None: ...
    def final(self, utterance_id: str, message: dict) -> None: ...  # deliver for insertion
    def failed(self, utterance_id: str, code: str, message: str) -> None: ...
    def profile_changed(self) -> None: ...


@dataclass(eq=False)
class Utterance:
    id: str
    options: dict[str, Any]
    chunks: list[bytes] = field(default_factory=list)
    ended: bool = False
    created: float = field(default_factory=time.monotonic)
    started_on_server: bool = False  # start sent on the current connection
    sent_chunks: int = 0
    ended_on_server: bool = False
    result: dict | None = None
    done: bool = False  # final delivered or failed
    retry_at: float | None = None


class DictationClient:
    def __init__(self, ws_url: str, token: str, events: Events, ledger: Ledger, ca_file: str = ""):
        self.ws_url = ws_url
        self.token = token
        self.events = events
        self.ledger = ledger
        self.ssl = ssl_context(ca_file) if ws_url.startswith("wss") else None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="sauti-net", daemon=True)
        self.ws: Any = None
        self.ready = False
        self.pending: list[Utterance] = []  # in start order
        self.recording: Utterance | None = None
        self._stop = False

    # -- thread-safe API ----------------------------------------------------------
    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop = True
        self.loop.call_soon_threadsafe(self._shutdown)
        self.thread.join(timeout=3)

    def begin(self, **options: Any) -> str:
        uid = str(uuid.uuid4())
        self.loop.call_soon_threadsafe(self._begin, uid, options)
        return uid

    def push_audio(self, pcm: bytes) -> None:
        self.loop.call_soon_threadsafe(self._push, pcm)

    def finish(self, utterance_id: str) -> None:
        self.loop.call_soon_threadsafe(self._finish, utterance_id)

    def cancel(self, utterance_id: str) -> None:
        self.loop.call_soon_threadsafe(self._cancel, utterance_id)

    def has_pending(self) -> bool:
        return any(not u.done for u in self.pending)

    # -- loop internals ---------------------------------------------------------------
    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.create_task(self._connect_forever())
        self.loop.create_task(self._watchdog())
        self.loop.run_forever()

    def _shutdown(self) -> None:
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.loop.call_later(0.2, self.loop.stop)

    async def _send(self, msg: dict | bytes) -> bool:
        if self.ws is None or not self.ready:
            return False
        try:
            await self.ws.send(msg if isinstance(msg, bytes) else json.dumps(msg))
            return True
        except Exception:
            return False

    def _spawn(self, coro) -> None:
        self.loop.create_task(coro)

    def _begin(self, uid: str, options: dict) -> None:
        u = Utterance(uid, options)
        self.pending.append(u)
        self.recording = u
        self._spawn(self._send_start(u))

    async def _send_start(self, u: Utterance) -> None:
        if await self._send({"type": "start", "utterance_id": u.id, "sample_rate": 16000, "format": "pcm_s16le",
                             **u.options}):
            u.started_on_server = True
            for chunk in u.chunks[u.sent_chunks:]:
                if not await self._send(chunk):
                    return
                u.sent_chunks += 1
            if u.ended and not u.ended_on_server:
                if await self._send({"type": "end", "utterance_id": u.id}):
                    u.ended_on_server = True

    def _push(self, pcm: bytes) -> None:
        u = self.recording
        if u is None:
            return
        u.chunks.append(pcm)
        if u.started_on_server and u.sent_chunks == len(u.chunks) - 1:
            u.sent_chunks += 1
            self._spawn(self._send(pcm))

    def _finish(self, uid: str) -> None:
        u = self._find(uid)
        if u is None:
            return
        u.ended = True
        if self.recording is u:
            self.recording = None
        if u.started_on_server and u.sent_chunks == len(u.chunks):
            u.ended_on_server = True
            self._spawn(self._send({"type": "end", "utterance_id": uid}))
        elif self.ready:
            self._spawn(self._send_start(u))

    def _cancel(self, uid: str) -> None:
        u = self._find(uid)
        if u is None:
            return
        if self.recording is u:
            self.recording = None
        u.done = True
        u.chunks = []
        self.pending.remove(u)
        self._spawn(self._send({"type": "cancel", "utterance_id": uid}))

    def _find(self, uid: str) -> Utterance | None:
        return next((u for u in self.pending if u.id == uid), None)

    # -- connection management ------------------------------------------------------------
    async def _connect_forever(self) -> None:
        attempt = 0
        while not self._stop:
            self.events.connection("connecting", "")
            try:
                async with websockets.connect(self.ws_url, ssl=self.ssl, max_size=2**20, ping_interval=20,
                                              ping_timeout=20, open_timeout=10) as ws:
                    await ws.send(json.dumps({"type": "hello", "token": self.token,
                                              "client": f"desktop/{__version__}", "protocol": 1}))
                    hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if hello.get("type") != "ready":
                        if hello.get("code") == "unauthorized":
                            self.events.connection("unauthorized", hello.get("message", ""))
                            return  # do not hammer the server with a revoked token
                        raise ConnectionError(hello.get("message", "handshake failed"))
                    self.ws, self.ready, attempt = ws, True, 0
                    self.events.connection("ready", hello.get("user", {}).get("display_name", ""))
                    await self._on_connected()
                    async for raw in ws:
                        if isinstance(raw, str):
                            await self._on_message(json.loads(raw))
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.info("connection lost: %s", type(e).__name__)
            finally:
                self.ws, self.ready = None, False
                for u in self.pending:
                    u.started_on_server = u.ended_on_server = False
                    u.sent_chunks = 0
            if self._stop:
                return
            delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
            attempt += 1
            self.events.connection("offline", f"retrying in {delay:g} s")
            await asyncio.sleep(delay)

    async def _on_connected(self) -> None:
        for u in list(self.pending):
            if u.done:
                continue
            if u.ended:
                await self._send({"type": "resume", "utterance_id": u.id})
            else:
                await self._send_start(u)  # still recording: replay what we have, keep streaming

    async def _watchdog(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            for u in list(self.pending):
                if u.done:
                    continue
                if u.ended and now - u.created > GIVE_UP_S:
                    self._fail(u, "gave_up", "Dictation could not be processed - the server is unavailable.")
                elif u.retry_at and now >= u.retry_at and self.ready:
                    u.retry_at = None
                    u.started_on_server = u.ended_on_server = False
                    u.sent_chunks = 0
                    await self._send_start(u)

    # -- server messages ---------------------------------------------------------------------
    async def _on_message(self, m: dict) -> None:
        kind = m.get("type")
        uid = m.get("utterance_id", "")
        if kind == "partial":
            self.events.partial(uid, m.get("text", ""))
        elif kind == "status":
            self.events.status(uid, m.get("state", ""), m.get("position"))
        elif kind == "final":
            await self._send({"type": "ack", "utterance_id": uid})
            u = self._find(uid)
            if u is None or uid in self.ledger:
                return  # replay of something already handled
            u.result = m
            self._flush()
        elif kind == "resume_result":
            u = self._find(uid)
            if u and m.get("state") == "unknown":
                u.started_on_server = u.ended_on_server = False
                u.sent_chunks = 0
                await self._send_start(u)
        elif kind == "error":
            u = self._find(uid) if uid else None
            if u is None:
                return
            if m.get("retryable"):
                u.retry_at = time.monotonic() + max(0.3, m.get("retry_after_ms", 1000) / 1000)
                self.events.status(uid, "retrying", None)
            elif m.get("code") != "cancelled":
                self._fail(u, m.get("code", "error"), m.get("message", "Dictation failed."))
        elif kind == "profile_changed":
            self.events.profile_changed()

    def _fail(self, u: Utterance, code: str, message: str) -> None:
        u.done = True
        u.chunks = []
        u.result = {"type": "failed", "code": code, "message": message}
        self._flush()

    def _flush(self) -> None:
        """Deliver results in start order; a slow utterance holds back later ones."""
        while self.pending and self.pending[0].result is not None:
            u = self.pending.pop(0)
            u.done = True
            u.chunks = []  # audio is discarded as soon as the outcome is known
            if u.result.get("type") == "failed":
                self.events.failed(u.id, u.result["code"], u.result["message"])
            elif self.ledger.claim(u.id):
                self.events.final(u.id, u.result)


CallbackEvents = Callable[..., None]
