"""Turn one utterance's audio into the text to insert.

verbatim:  ASR -> dictionary spellings
clean:     ASR -> dictionary -> rule cleanup -> LLM cleanup -> guard (fallback to rules)
translate: clean -> translation -> number/length check
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import numpy as np

from .backends.base import AsrResult, Backends, BackendUnavailable
from .config import Config
from .scheduler import Priority, Stage
from .text import dictionary, guard, rules
from .text.llm_tasks import cleanup_messages, max_tokens_for, translate_messages

log = logging.getLogger("sauti.pipeline")

StatusFn = Callable[[str, int | None], Awaitable[None]]
SILENCE_RMS = 0.0025


@dataclass
class Request:
    user_id: int
    utterance_id: str
    audio: np.ndarray  # float32, 16 kHz mono
    mode: str
    lang: str | None  # None = detect
    target_lang: str
    style: str
    entries: list[dict]  # merged personal + org dictionary
    priority: Priority = Priority.FINAL


@dataclass
class Result:
    text: str
    raw: str
    lang: str | None
    lang_prob: float
    mode: str
    target_lang: str | None
    source_text: str | None = None
    notices: list[str] = field(default_factory=list)
    timings_ms: dict[str, int] = field(default_factory=dict)
    guard_reasons: list[str] = field(default_factory=list)


def pcm16_to_float(pcm: bytes | bytearray | memoryview) -> np.ndarray:
    return np.frombuffer(bytes(pcm), dtype="<i2").astype(np.float32) / 32768.0


class Pipeline:
    def __init__(self, cfg: Config, backends: Backends, asr: Stage, llm: Stage | None, mt: Stage | None):
        self.cfg = cfg
        self.backends = backends
        self.asr_stage = asr
        self.llm_stage = llm
        self.mt_stage = mt

    # -- recognition ---------------------------------------------------------
    async def recognise(self, req: Request, *, partial: bool = False, timeout_s: float | None = None,
                        key: str | None = None) -> AsrResult:
        hints = dictionary.hint_text(req.entries)
        allowed = self.cfg.asr.languages
        audio = req.audio

        def work(cancel):
            return self.backends.asr.transcribe(audio, lang=req.lang, allowed=allowed, hints=hints,
                                                cancel=cancel, partial=partial)

        return await self.asr_stage.run(req.user_id, Priority.PARTIAL if partial else req.priority,
                                        timeout_s=timeout_s or self.cfg.scheduler.final_timeout_s, fn=work, key=key)

    # -- full pipeline ----------------------------------------------------------
    async def process(self, req: Request, on_status: StatusFn, deadline_s: float | None = None) -> Result:
        t0 = time.monotonic()
        deadline = t0 + (deadline_s or self.cfg.scheduler.final_timeout_s)
        timings: dict[str, int] = {"queue": 0, "asr": 0, "cleanup": 0, "translate": 0}
        notices: list[str] = []

        if len(req.audio) == 0 or float(np.sqrt(np.mean(req.audio ** 2))) < SILENCE_RMS:
            return Result("", "", req.lang, 0.0, req.mode, None, notices=["no_speech"],
                          timings_ms={**timings, "total": int((time.monotonic() - t0) * 1000)})

        asr_job = self.asr_stage.submit(
            req.user_id, req.priority, timeout_s=max(1.0, deadline - time.monotonic()),
            fn=lambda cancel: self.backends.asr.transcribe(
                req.audio, lang=req.lang, allowed=self.cfg.asr.languages, hints=dictionary.hint_text(req.entries),
                cancel=cancel, partial=False))
        pos = self.asr_stage.position(asr_job)
        if pos > 1:
            await on_status("queued", pos)
        else:
            await on_status("transcribing", None)
        asr = await self.asr_stage.result(asr_job)
        timings["queue"] = int(((asr_job.started or time.monotonic()) - asr_job.enqueued) * 1000)
        timings["asr"] = int((time.monotonic() - (asr_job.started or asr_job.enqueued)) * 1000)
        if asr.restricted:
            notices.append("lang_restricted")
        if asr.no_speech or not asr.text.strip():
            return Result("", "", asr.lang, asr.lang_prob, req.mode, None, notices=notices + ["no_speech"],
                          timings_ms={**timings, "total": int((time.monotonic() - t0) * 1000)})

        lang = asr.lang
        raw = dictionary.apply_aliases(asr.text.strip(), req.entries)
        result = Result(raw, raw, lang, asr.lang_prob, req.mode, None, notices=notices, timings_ms=timings)

        if req.mode in ("clean", "translate"):
            t = time.monotonic()
            await on_status("cleaning", None)
            result.text, reasons, extra = await self._cleanup(req, raw, lang, deadline)
            result.guard_reasons = reasons
            notices.extend(extra)
            timings["cleanup"] = int((time.monotonic() - t) * 1000)

        if req.mode == "translate" and lang and req.target_lang and req.target_lang != lang:
            t = time.monotonic()
            await on_status("translating", None)
            result.source_text = result.text
            result.target_lang = req.target_lang
            translated = await self._translate(req, result.text, lang, req.target_lang, deadline)
            check = guard.check_translation(result.text, translated)
            if not check.ok:
                notices.append("translation_unverified")
                result.guard_reasons += ["mt:" + r for r in check.reasons]
            result.text = translated
            timings["translate"] = int((time.monotonic() - t) * 1000)
        elif req.mode == "translate":
            result.target_lang = req.target_lang

        timings["total"] = int((time.monotonic() - t0) * 1000)
        return result

    async def _cleanup(self, req: Request, raw: str, lang: str | None, deadline: float) -> tuple[str, list[str], list[str]]:
        base = rules.clean(raw, lang)
        llm = self.backends.llm
        if llm is None or self.llm_stage is None or len(base.split()) < 3 or \
                (lang and lang not in self.cfg.llm.cleanup_languages):
            return base, [], []
        terms = dictionary.terms_in(base, req.entries)
        messages = cleanup_messages(base, lang, req.style, terms)
        budget = max(1.0, min(self.cfg.llm.timeout_s, deadline - time.monotonic()))
        try:
            candidate = await self.llm_stage.run(
                req.user_id, req.priority, timeout_s=budget,
                coro=lambda cancel: llm.chat(messages, max_tokens=max_tokens_for(base), timeout=budget))
        except BackendUnavailable:
            return base, [], ["cleanup_unavailable"]
        except Exception as exc:  # timeouts, queue full, HTTP errors: rules are always a safe answer
            log.warning("cleanup failed for user %s: %s", req.user_id, type(exc).__name__)
            return base, [], ["cleanup_unavailable"]
        candidate = candidate.strip().strip('"').strip()
        if candidate.startswith("<transcript>"):
            candidate = candidate.removeprefix("<transcript>").removesuffix("</transcript>").strip()
        verdict = guard.check_cleanup(base, candidate, lang, terms)
        if not verdict.ok:
            return base, verdict.reasons, ["cleanup_fallback"]
        return candidate, [], []

    async def _translate(self, req: Request, text: str, src: str, tgt: str, deadline: float) -> str:
        budget = max(1.0, deadline - time.monotonic())
        terms = dictionary.terms_in(text, req.entries)
        if self.backends.translate is not None and self.mt_stage is not None:
            mt = self.backends.translate
            return (await self.mt_stage.run(req.user_id, req.priority, timeout_s=budget,
                                            coro=lambda cancel: mt.translate(text, src, tgt, timeout=budget))).strip()
        if self.backends.llm is not None and self.llm_stage is not None:
            llm = self.backends.llm
            msgs = translate_messages(text, src, tgt, terms)
            return (await self.llm_stage.run(req.user_id, req.priority, timeout_s=budget,
                                             coro=lambda cancel: llm.chat(msgs, max_tokens=max_tokens_for(text) * 2,
                                                                          timeout=budget))).strip()
        raise BackendUnavailable("no translation backend configured")

    def describe(self) -> dict[str, Any]:
        b = self.backends
        return {"asr": b.asr.describe(), "llm": b.llm.describe() if b.llm else None,
                "translate": b.translate.describe() if b.translate else ("llm" if b.llm else None)}
