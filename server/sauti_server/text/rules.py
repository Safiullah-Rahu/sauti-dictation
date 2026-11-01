"""Deterministic cleanup that never changes meaning.

This tier always runs. It removes hesitation sounds and stutters on function
words and tidies spacing and punctuation. It is also what the user receives when
the LLM pass is unavailable or its output fails the guard.
"""
from __future__ import annotations

import re

# Hesitation sounds only. Words that carry meaning in some contexts ("like", "you know",
# Arabic "يعني", Somali "haa" = yes, Egyptian "آه" = yes) are deliberately left alone here
# and handled, if at all, by the LLM pass.
_FILLERS = {
    "en": r"u+m+|u+h+|e+r+m+|er|a+h+|h+m+|m{2,}",
    "so": r"e{2,}|e+h+|a{2,}h*|h+m+|m{2,}",
    "sw": r"a{2,}|e{2,}|e+h+|h+m+|m{2,}",
    "ar": r"[اإأآ]?م{2,}|[اآ]{2,}|ا+ه{2,}",
}
_ANY_FILLER = "|".join(f"(?:{p})" for p in _FILLERS.values())

_EN_STUTTER = re.compile(
    r"\b(i|the|a|an|to|we|you|it|and|but|is|of|in|on|that|this|my|he|she|they|so|for|with)(?:[\s,]+\1\b)+",
    re.IGNORECASE,
)

_EN_I = re.compile(r"(?<![\w'’])i(?=$|[\s,.!?;:]|['’](?:m|ve|ll|d))")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.!?;:،؟؛])")
_DUP_PUNCT = re.compile(r"([,،])(?:\s*[,،])+")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
_LEADING_PUNCT = re.compile(r"^[\s,.;:،؛]+")
_COMMA_BEFORE_STOP = re.compile(r"[,،]\s*([.!?؟])")


def _filler_re(lang: str | None) -> re.Pattern[str]:
    pattern = _FILLERS.get(lang or "", _ANY_FILLER)
    return re.compile(rf"(?<!\w)(?:{pattern})(?!\w)[,،]?", re.IGNORECASE)


def remove_fillers(text: str, lang: str | None) -> str:
    return _filler_re(lang).sub(" ", text)


def tidy(text: str) -> str:
    text = _MULTI_SPACE.sub(" ", text.replace("\n ", "\n"))
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _DUP_PUNCT.sub(r"\1", text)
    text = _COMMA_BEFORE_STOP.sub(r"\1", text)
    text = _LEADING_PUNCT.sub("", text).strip()
    if text and text[0].isalpha() and text[0].islower() and text[0].isascii():
        text = text[0].upper() + text[1:]
    return text


def clean(text: str, lang: str | None) -> str:
    text = remove_fillers(text, lang)
    if lang in (None, "en"):
        text = _EN_STUTTER.sub(r"\1", text)
        text = _EN_I.sub("I", text)
    return tidy(text)
