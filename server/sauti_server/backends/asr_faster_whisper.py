"""Whisper via faster-whisper (CTranslate2): int8 on CPU, CUDA when a GPU is added."""
from __future__ import annotations

import logging
import math
import re
import threading
import time
from typing import Any

import numpy as np

from ..config import AsrSection
from ..devices import resolve_compute_type, resolve_device
from .base import AsrResult, Cancelled

log = logging.getLogger("sauti.asr")

# Phrases Whisper is known to produce from silence or noise (learned from subtitle data).
_HALLUCINATIONS = [
    "thank you for watching", "thanks for watching", "subtitles by", "please subscribe", "thank you.",
    "اشتركوا في القناة", "ترجمة نانسي قنقر", "شكرا للمشاهدة", "asante kwa kutazama",
]


def _is_hallucination(text: str) -> bool:
    t = re.sub(r"[\s.!,]+", " ", text.strip().casefold()).strip()
    return any(t == re.sub(r"[\s.!,]+", " ", h).strip() for h in _HALLUCINATIONS)


class FasterWhisperBackend:
    name = "faster_whisper"

    def __init__(self, cfg: AsrSection):
        from faster_whisper import WhisperModel

        self.cfg = cfg
        self.device = resolve_device(cfg.device)
        self.compute_type = resolve_compute_type(cfg.compute_type, self.device)
        t0 = time.time()

        def load(path: str, threads: int, workers: int):
            return WhisperModel(path, device=self.device, device_index=cfg.device_index,
                                compute_type=self.compute_type, cpu_threads=threads, num_workers=workers)

        self.model = load(cfg.model, cfg.cpu_threads, cfg.workers)
        self.partial_model = load(cfg.partial_model, cfg.partial_cpu_threads, 1) if cfg.partial_model else None
        self.lang_models = {lang: load(path, cfg.cpu_threads, cfg.workers) for lang, path in cfg.per_language.items()}
        self.load_s = time.time() - t0
        log.info("ASR loaded %s on %s (%s, %d workers) in %.1fs", cfg.model, self.device, self.compute_type,
                 cfg.workers, self.load_s)

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name, "model": self.cfg.model, "compute_type": self.compute_type,
                "partial_model": self.cfg.partial_model or None, "per_language": self.cfg.per_language,
                "workers": self.cfg.workers, "cpu_threads": self.cfg.cpu_threads if self.device == "cpu" else None,
                "device": self.device if self.device == "cpu" else f"cuda:{self.cfg.device_index}"}

    def _options(self, partial: bool, hints: str | None) -> dict[str, Any]:
        return dict(
            beam_size=1 if partial else self.cfg.beam_size,
            vad_filter=self.cfg.vad and not partial,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            without_timestamps=True,
            hotwords=hints,
            temperature=0.0 if partial else [0.0, 0.2, 0.4],
        )

    def transcribe(self, audio: np.ndarray, *, lang: str | None, allowed: list[str], hints: str | None,
                   cancel: threading.Event, partial: bool = False) -> AsrResult:
        duration = len(audio) / 16000
        model = self.partial_model if (partial and self.partial_model) else self.model
        if lang and lang in self.lang_models and not partial:
            model = self.lang_models[lang]
        opts = self._options(partial, hints)

        segments, info = model.transcribe(audio, language=lang, **opts)
        detected, prob, restricted = info.language, float(info.language_probability), False
        if lang is None and allowed and detected not in allowed:
            # Constrain language identification to the configured languages.
            probs = dict(info.all_language_probs or [])
            best = max(allowed, key=lambda code: probs.get(code, 0.0))
            restricted, detected, prob = True, best, float(probs.get(best, 0.0))
            segments, info = model.transcribe(audio, language=best, **opts)
        if lang is None and not partial and detected in self.lang_models:
            segments, info = self.lang_models[detected].transcribe(audio, language=detected, **opts)

        parts: list[str] = []
        logprob_sum = weight = 0.0
        for seg in segments:  # lazy: decoding happens while iterating
            if cancel.is_set():
                raise Cancelled()
            parts.append(seg.text)
            w = max(0.1, seg.end - seg.start)
            logprob_sum += seg.avg_logprob * w
            weight += w
        text = "".join(parts).strip()
        if _is_hallucination(text):
            text = ""
        confidence = math.exp(logprob_sum / weight) if weight else 0.0
        return AsrResult(text=text, lang=detected, lang_prob=prob, duration_s=duration, restricted=restricted,
                         no_speech=not text, confidence=confidence)
