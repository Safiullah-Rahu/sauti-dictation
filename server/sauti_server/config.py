"""Server configuration, loaded from a TOML file plus SAUTI_* environment overrides.

Every model backend is named here, so swapping a recogniser or LLM is a config
change and a restart, never a client release.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

LANGUAGES = ("en", "so", "ar", "sw")
LANGUAGE_NAMES = {"en": "English", "so": "Somali", "ar": "Arabic", "sw": "Swahili"}
MODES = ("verbatim", "clean", "translate")
STYLES = ("neutral", "formal", "casual", "message")


@dataclass
class ServerSection:
    host: str = "127.0.0.1"
    port: int = 8700
    data_dir: str = "./data"
    public_url: str = "http://127.0.0.1:8700"
    # Behind a reverse proxy (Caddy) set this so client IPs come from X-Forwarded-For.
    trust_proxy: bool = False
    log_level: str = "info"


@dataclass
class AsrSection:
    backend: str = "faster_whisper"  # faster_whisper | fake
    model: str = "large-v3-turbo"  # model name or local path
    compute_type: str = "int8"
    cpu_threads: int = 4
    workers: int = 2  # concurrent transcriptions
    beam_size: int = 1
    vad: bool = True
    languages: list[str] = field(default_factory=lambda: list(LANGUAGES))
    # Optional faster model used only for the live preview while the user speaks.
    partial_model: str = ""
    partial_cpu_threads: int = 2
    # Route specific languages to another model (e.g. a Somali fine-tune): {"so": "/models/x"}
    per_language: dict[str, str] = field(default_factory=dict)
    # Below this recogniser confidence (0..1) the result is flagged, and translate mode returns the
    # transcript untranslated rather than risk translating misrecognised words into invented text.
    low_confidence: float = 0.5


@dataclass
class LlmSection:
    backend: str = "openai_compat"  # openai_compat | none | fake
    base_url: str = "http://127.0.0.1:8081/v1"
    model: str = "qwen3-4b-instruct"
    api_key: str = ""
    timeout_s: float = 20.0
    max_concurrency: int = 2  # should match llama-server --parallel
    # Languages for which the LLM pass is attempted; others get rule-based cleanup only.
    cleanup_languages: list[str] = field(default_factory=lambda: list(LANGUAGES))


@dataclass
class TranslateSection:
    backend: str = "llm"  # llm | ct2 | fake | none
    model_path: str = ""  # for ct2 (MADLAD-400 / NLLB)
    compute_type: str = "int8"
    cpu_threads: int = 4
    workers: int = 1
    beam_size: int = 2


@dataclass
class SchedulerSection:
    max_queued_finals: int = 32
    max_queued_per_user: int = 3
    max_queued_batch: int = 16
    final_timeout_s: float = 45.0
    partials: bool = True
    partial_interval_s: float = 1.2
    partial_max_age_s: float = 2.0
    partial_window_s: float = 20.0
    # Utterances longer than this are scheduled as background (batch) work.
    interactive_max_s: float = 45.0


@dataclass
class LimitsSection:
    max_utterance_s: float = 120.0
    daily_audio_minutes: float = 240.0
    max_devices_per_user: int = 10


@dataclass
class PrivacySection:
    default_history: bool = False
    default_retention_days: int = 30
    max_retention_days: int = 365
    result_cache_s: int = 600


@dataclass
class Config:
    server: ServerSection = field(default_factory=ServerSection)
    asr: AsrSection = field(default_factory=AsrSection)
    llm: LlmSection = field(default_factory=LlmSection)
    translate: TranslateSection = field(default_factory=TranslateSection)
    scheduler: SchedulerSection = field(default_factory=SchedulerSection)
    limits: LimitsSection = field(default_factory=LimitsSection)
    privacy: PrivacySection = field(default_factory=PrivacySection)

    @property
    def data_path(self) -> Path:
        p = Path(self.server.data_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p


def _merge(obj: Any, values: dict[str, Any]) -> None:
    for f in fields(obj):
        if f.name not in values:
            continue
        cur = getattr(obj, f.name)
        if is_dataclass(cur):
            _merge(cur, values[f.name])
        else:
            setattr(obj, f.name, values[f.name])


def _coerce(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        return raw.lower() in ("1", "true", "yes", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if isinstance(current, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def load_config(path: str | os.PathLike[str] | None = None, env: dict[str, str] | None = None) -> Config:
    """Load config from TOML (path or $SAUTI_CONFIG) and apply SAUTI_<SECTION>__<KEY> overrides."""
    cfg = Config()
    env = dict(os.environ if env is None else env)
    path = path or env.get("SAUTI_CONFIG")
    if path:
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh))
    for key, raw in env.items():
        if not key.startswith("SAUTI_") or "__" not in key:
            continue
        section, _, name = key[6:].lower().partition("__")
        sec = getattr(cfg, section, None)
        if sec is None or not hasattr(sec, name):
            continue
        setattr(sec, name, _coerce(raw, getattr(sec, name)))
    return cfg
