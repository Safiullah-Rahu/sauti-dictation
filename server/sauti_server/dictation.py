"""The /v1/dictate WebSocket: audio up, text down, with resume and exactly-once delivery.

See docs/protocol.md section 3 for the message contract.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from . import __version__
from .backends.base import BackendUnavailable
from .config import LANGUAGES, MODES
from .pipeline import Request, Result, pcm16_to_float
from .scheduler import JobCancelled, JobDropped, JobTimeout, Priority, QueueFull
from .state import AppState, Session
from .text import dictionary

log = logging.getLogger("sauti.dictate")

SAMPLE_RATE = 16000
_UTT_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


@dataclass(eq=False)
class InFlight:
    user_id: int
    utterance_id: str
    listeners: set[Session] = field(default_factory=set)
    cancel: threading.Event = field(default_factory=threading.Event)
    task: asyncio.Task | None = None
    final: dict[str, Any] | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None


@dataclass(eq=False)
class Receiving:
    utterance_id: str
    mode: str
    lang: str | None
    target_lang: str
    style: str
    entries: list[dict]
    pcm: bytearray = field(default_factory=bytearray)
    started: float = field(default_factory=time.monotonic)
    last_partial_bytes: int = 0
    partial_seq: int = 0
    partial_task: asyncio.Task | None = None
    preview_lang: str | None = None
    truncated: bool = False


def _final_message(utt: str, r: Result) -> dict[str, Any]:
    msg = {"type": "final", "utterance_id": utt, "text": r.text, "raw": r.raw, "lang": r.lang,
           "lang_prob": round(r.lang_prob, 3), "confidence": round(r.confidence, 3), "mode": r.mode, "target_lang": r.target_lang, "replayed": False,
           "timings_ms": r.timings_ms, "notices": r.notices}
    if r.source_text is not None:
        msg["source_text"] = r.source_text
    return msg


def _error(code: str, message: str, utt: str | None = None, retryable: bool = False,
           retry_after_ms: int | None = None) -> dict[str, Any]:
    msg: dict[str, Any] = {"type": "error", "code": code, "message": message, "retryable": retryable}
    if utt:
        msg["utterance_id"] = utt
    if retry_after_ms is not None:
        msg["retry_after_ms"] = retry_after_ms
    return msg


class DictationSocket:
    def __init__(self, state: AppState, ws: WebSocket):
        self.st = state
        self.ws = ws
        self.session: Session | None = None
        self.rx: Receiving | None = None
        self.ignore_audio_for: str | None = None

    # -- connection --------------------------------------------------------------
    async def serve(self) -> None:
        await self.ws.accept()
        try:
            hello = json.loads(await asyncio.wait_for(self.ws.receive_text(), timeout=10))
        except (asyncio.TimeoutError, json.JSONDecodeError, WebSocketDisconnect, RuntimeError, KeyError):
            await self._reject("hello expected")
            return
        principal = self.st.store.resolve_token(str(hello.get("token", ""))) if hello.get("type") == "hello" else None
        if principal is None:
            await self._reject("invalid or revoked token")
            return
        remote = self.ws.client.host if self.ws.client else "?"
        if self.st.cfg.server.trust_proxy:
            remote = self.ws.headers.get("x-forwarded-for", remote).split(",")[0].strip()
        self.session = Session(secrets.token_hex(8), self.ws, principal, remote, str(hello.get("client", ""))[:60])
        self.st.sessions.add(self.session)
        user = self.st.store.user(principal.user_id)
        await self.session.send({
            "type": "ready", "session_id": self.session.id, "user": principal.public_user,
            "profile_version": int(user["profile_version"]) + self.st.store.org_version(),
            "server_version": __version__,
            "limits": {"max_utterance_seconds": self.st.cfg.limits.max_utterance_s, "sample_rate": SAMPLE_RATE},
        })
        try:
            await self._loop()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            self._cleanup()

    async def _reject(self, reason: str) -> None:
        try:
            await self.ws.send_text(json.dumps(_error("unauthorized", reason)))
            await self.ws.close(code=4401, reason=reason)
        except Exception:
            pass

    def _cleanup(self) -> None:
        if self.rx and self.rx.partial_task:
            self.rx.partial_task.cancel()
        self.rx = None
        if self.session:
            self.session.closed = True
            self.st.sessions.remove(self.session)
            for entry in self.st.inflight.values():
                entry.listeners.discard(self.session)

    async def _loop(self) -> None:
        assert self.session
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                return
            self.session.last_activity = time.time()
            if msg.get("bytes") is not None:
                await self._audio(msg["bytes"])
                continue
            try:
                data = json.loads(msg.get("text") or "")
                kind = data.get("type")
            except (json.JSONDecodeError, AttributeError):
                await self.session.send(_error("bad_request", "messages must be JSON objects"))
                continue
            handler = getattr(self, f"_on_{kind}", None) if isinstance(kind, str) else None
            if handler is None:
                await self.session.send(_error("bad_request", f"unknown message type {kind!r}"))
                continue
            await handler(data)

    # -- helpers -------------------------------------------------------------------
    def _key(self, utt: str) -> tuple[int, str]:
        assert self.session
        return (self.session.principal.user_id, utt)

    def _profile(self) -> tuple[Any, list[dict]]:
        assert self.session
        p = self.session.principal
        user = self.st.store.user(p.user_id)
        entries = dictionary.merge(self.st.store.dictionary(p.user_id, p.dek), self.st.store.org_dictionary())
        return user, entries

    def _quota_s(self, user) -> float:
        minutes = user["daily_limit_minutes"]
        return 60.0 * (minutes if minutes is not None else self.st.cfg.limits.daily_audio_minutes)

    async def _replay(self, entry: InFlight) -> None:
        assert self.session and entry.final
        await self.session.send({**entry.final, "replayed": True})

    # -- messages ------------------------------------------------------------------
    async def _on_ping(self, data: dict) -> None:
        await self.session.send({"type": "pong", "t": data.get("t")})

    async def _on_start(self, data: dict) -> None:
        s = self.session
        utt = str(data.get("utterance_id", ""))
        if not _UTT_ID.match(utt):
            await s.send(_error("bad_request", "utterance_id must be a UUID"))
            return
        if data.get("format", "pcm_s16le") != "pcm_s16le" or int(data.get("sample_rate", SAMPLE_RATE)) != SAMPLE_RATE:
            await s.send(_error("bad_request", "only pcm_s16le mono 16000 Hz is supported", utt))
            return
        if self.rx is not None and self.rx.utterance_id != utt:
            await self._finish_receiving()
        existing = self.st.inflight.get(self._key(utt))
        if existing is not None:
            self.ignore_audio_for = utt
            if existing.final is not None:
                await self._replay(existing)
            else:
                existing.listeners.add(s)
                await s.send({"type": "status", "utterance_id": utt, "state": "processing"})
            return
        if self.rx is not None and self.rx.utterance_id == utt:
            return  # duplicate start for the utterance being recorded
        user, entries = self._profile()
        if self.st.store.usage_today_s(s.principal.user_id) >= self._quota_s(user):
            await s.send(_error("quota_exceeded", "daily dictation limit reached", utt))
            return
        mode = data.get("mode") or user["mode"]
        lang = data.get("lang") or user["input_lang"]
        target = data.get("target_lang") or user["output_lang"]
        if mode not in MODES or (lang != "auto" and lang not in LANGUAGES) or target not in LANGUAGES:
            await s.send(_error("bad_request", "unsupported mode or language", utt))
            return
        self.ignore_audio_for = None
        self.rx = Receiving(utt, mode, None if lang == "auto" else lang, target, user["style"], entries)
        s.state = "recording"

    async def _audio(self, chunk: bytes) -> None:
        rx = self.rx
        if rx is None:
            return
        limit = int(self.st.cfg.limits.max_utterance_s * SAMPLE_RATE * 2)
        room = limit - len(rx.pcm)
        if room <= 0:
            return
        rx.pcm.extend(chunk[:room] if len(chunk) > room else chunk)
        if len(chunk) >= room:
            rx.truncated = True
            await self._finish_receiving()
            return
        sc = self.st.cfg.scheduler
        if sc.partials and rx.partial_task is None and len(rx.pcm) >= SAMPLE_RATE * 2 * 0.8 and \
                len(rx.pcm) - rx.last_partial_bytes >= sc.partial_interval_s * SAMPLE_RATE * 2:
            rx.last_partial_bytes = len(rx.pcm)
            rx.partial_task = asyncio.create_task(self._partial(rx))

    async def _partial(self, rx: Receiving) -> None:
        s = self.session
        window = int(self.st.cfg.scheduler.partial_window_s * SAMPLE_RATE * 2)
        req = Request(s.principal.user_id, rx.utterance_id, pcm16_to_float(rx.pcm[-window:]), rx.mode,
                      rx.lang or rx.preview_lang, rx.target_lang, rx.style, rx.entries)
        try:
            res = await self.st.pipeline.recognise(req, partial=True, timeout_s=5.0, key=f"{s.id}:{rx.utterance_id}")
            if self.rx is rx and res.text:
                rx.preview_lang = rx.preview_lang or res.lang
                rx.partial_seq += 1
                await s.send({"type": "partial", "utterance_id": rx.utterance_id,
                              "text": dictionary.apply_aliases(res.text, rx.entries), "seq": rx.partial_seq})
        except (QueueFull, JobDropped, JobTimeout, JobCancelled, BackendUnavailable, asyncio.CancelledError):
            pass
        except Exception:
            log.exception("partial failed")
        finally:
            rx.partial_task = None

    async def _on_end(self, data: dict) -> None:
        utt = str(data.get("utterance_id", ""))
        if self.rx is not None and self.rx.utterance_id == utt:
            await self._finish_receiving()
        elif self._key(utt) not in self.st.inflight and self.ignore_audio_for != utt:
            await self.session.send(_error("bad_request", "end for an utterance that was not started", utt))

    async def _finish_receiving(self) -> None:
        rx, self.rx = self.rx, None
        if rx is None:
            return
        if rx.partial_task:
            rx.partial_task.cancel()
        s = self.session
        entry = InFlight(s.principal.user_id, rx.utterance_id, {s})
        self.st.inflight[self._key(rx.utterance_id)] = entry
        s.state = "processing"
        s.utterances += 1
        entry.task = asyncio.create_task(self._process(entry, rx))

    async def _process(self, entry: InFlight, rx: Receiving) -> None:
        st = self.st
        user_id = entry.user_id
        audio = pcm16_to_float(rx.pcm)
        duration = len(audio) / SAMPLE_RATE
        rx.pcm = bytearray()  # audio is not kept once handed to the pipeline
        priority = Priority.BATCH if duration > st.cfg.scheduler.interactive_max_s else Priority.FINAL
        req = Request(user_id, rx.utterance_id, audio, rx.mode, rx.lang, rx.target_lang, rx.style, rx.entries,
                      priority)

        async def status(state: str, position: int | None) -> None:
            msg = {"type": "status", "utterance_id": rx.utterance_id, "state": state}
            if position:
                msg["position"] = position
            for listener in list(entry.listeners):
                await listener.send(msg)

        err: dict[str, Any] | None = None
        stage = None
        try:
            deadline = st.cfg.scheduler.final_timeout_s * (4 if priority == Priority.BATCH else 1)
            result = await st.pipeline.process(req, status, deadline_s=deadline)
            if rx.truncated:
                result.notices.append("truncated")
            entry.final = _final_message(rx.utterance_id, result)
            await asyncio.to_thread(self._record, user_id, duration, result)
            if result.guard_reasons:
                await asyncio.to_thread(st.store.add_event, "guard", "cleanup_fallback" if "cleanup_fallback" in
                                        result.notices else "translation_unverified", "cleanup", user_id,
                                        ",".join(result.guard_reasons))
        except QueueFull as e:
            stage, err = e.stage, _error("busy", "server busy, retry shortly", rx.utterance_id, True, e.retry_after_ms)
        except JobTimeout as e:
            stage, err = e.stage, _error("timeout", "processing took too long", rx.utterance_id, True, 1000)
        except (JobCancelled, asyncio.CancelledError):
            err = _error("cancelled", "cancelled", rx.utterance_id)
        except BackendUnavailable as e:
            stage, err = "backend", _error("unavailable", "a model service is unavailable", rx.utterance_id, True, 3000)
            log.warning("backend unavailable: %s", e)
        except Exception:
            log.exception("utterance failed")
            stage, err = "pipeline", _error("internal", "unexpected server error", rx.utterance_id, True, 1000)
        finally:
            entry.finished = time.time()
            for listener in list(entry.listeners):
                if listener.state == "processing":
                    listener.state = "idle"

        if err is not None:
            # Failed utterances are forgotten so a retry with the same id is processed fresh.
            st.inflight.pop((user_id, rx.utterance_id), None)
            if err["code"] != "cancelled":
                await asyncio.to_thread(st.store.add_event, "dictation", err["code"], stage, user_id, "")
                await asyncio.to_thread(st.store.add_usage, user_id, 0.0, 0, 1)
            for listener in list(entry.listeners):
                await listener.send(err)
            return
        for listener in list(entry.listeners):
            await listener.send(entry.final)

    def _record(self, user_id: int, duration: float, result: Result) -> None:
        st = self.st
        st.store.add_usage(user_id, duration)
        user = st.store.user(user_id)
        if user and user["history_enabled"] and result.text:
            st.store.add_history(user_id, self.session.principal.dek, result.mode, result.lang, result.target_lang,
                                 result.text, result.raw, int(user["retention_days"]))

    async def _on_cancel(self, data: dict) -> None:
        utt = str(data.get("utterance_id", ""))
        if self.rx is not None and self.rx.utterance_id == utt:
            if self.rx.partial_task:
                self.rx.partial_task.cancel()
            self.rx = None
            self.session.state = "idle"
            await self.session.send(_error("cancelled", "cancelled", utt))
            return
        entry = self.st.inflight.get(self._key(utt))
        if entry is not None and entry.final is None and entry.task is not None:
            entry.cancel.set()
            entry.task.cancel()
        elif entry is None:
            await self.session.send(_error("cancelled", "cancelled", utt))

    async def _on_resume(self, data: dict) -> None:
        utt = str(data.get("utterance_id", ""))
        entry = self.st.inflight.get(self._key(utt))
        if entry is None:
            await self.session.send({"type": "resume_result", "utterance_id": utt, "state": "unknown"})
        elif entry.final is not None:
            await self._replay(entry)
        else:
            entry.listeners.add(self.session)
            await self.session.send({"type": "resume_result", "utterance_id": utt, "state": "processing"})

    async def _on_ack(self, data: dict) -> None:
        key = self._key(str(data.get("utterance_id", "")))
        entry = self.st.inflight.get(key)
        if entry is not None and entry.final is not None:
            del self.st.inflight[key]


def purge_results(state: AppState) -> None:
    """Drop finished results that were never acknowledged (default after 10 minutes)."""
    cutoff = time.time() - state.cfg.privacy.result_cache_s
    for key, entry in list(state.inflight.items()):
        if entry.finished is not None and entry.finished < cutoff:
            del state.inflight[key]
