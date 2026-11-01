"""Backend interfaces. The pipeline only sees these, so models are swappable by config."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np


class BackendUnavailable(RuntimeError):
    """A model server or model is not reachable / not loaded."""


class Cancelled(RuntimeError):
    """Raised inside a backend call when the job's cancel event was set."""


@dataclass
class AsrResult:
    text: str
    lang: str | None
    lang_prob: float
    duration_s: float
    restricted: bool = False  # detected language was outside the allowed set
    no_speech: bool = False


class AsrBackend(Protocol):
    name: str

    def transcribe(self, audio: np.ndarray, *, lang: str | None, allowed: list[str], hints: str | None,
                   cancel: threading.Event, partial: bool = False) -> AsrResult: ...

    def describe(self) -> dict[str, Any]: ...


class LlmBackend(Protocol):
    name: str

    async def chat(self, messages: list[dict[str, str]], *, max_tokens: int, timeout: float) -> str: ...

    async def healthy(self) -> bool: ...

    def describe(self) -> dict[str, Any]: ...


class TranslateBackend(Protocol):
    name: str

    async def translate(self, text: str, src: str, tgt: str, *, timeout: float) -> str: ...

    def describe(self) -> dict[str, Any]: ...


@dataclass
class Backends:
    asr: AsrBackend
    llm: LlmBackend | None
    translate: TranslateBackend | None
