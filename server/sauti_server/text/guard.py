"""Meaning-preservation guard for LLM output.

The cleanup LLM is asked to fix fillers, repetitions and punctuation only. This
module checks that it did not do more: every number, negation, name and
dictionary term in the source must still be there, the length must stay close,
and almost no new words may appear. If any check fails the caller discards the
LLM output and uses the rule-based cleanup instead, so a model mistake can cost
polish but never meaning.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NUM = re.compile(r"\d+(?:[.,:]\d+)*")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)

_EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
            "ninety": 90}
_EN_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}

NEGATIONS = {
    "en": {"not", "no", "never", "nothing", "nobody", "none", "neither", "nor", "without", "cannot", "nowhere"},
    "ar": {"لا", "لم", "لن", "ليس", "ليست", "لست", "لسنا", "ما", "غير", "بدون", "ولا", "ولم", "ولن", "مش", "مو",
           "بلا"},
    "so": {"ma", "maya", "aan", "mana", "maan", "majiro"},
    "sw": {"si", "sio", "siyo", "hapana", "hakuna", "bila", "wala", "hamna"},
}
_CONTRACTION_NEG = re.compile(r"n['’]t$", re.IGNORECASE)
# Expansions a cleanup pass may legitimately produce from casual speech.
_ALLOWED_NEW = {"going", "want", "kind", "sort", "got", "have", "because", "them", "about", "until", "okay"}


@dataclass
class GuardResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS)
    # Drop Arabic diacritics/tatweel so harakat changes do not count as new words.
    return re.sub(r"[ً-ٰٟـ]", "", text)


def _words(text: str) -> list[str]:
    return [w.casefold() for w in _WORD.findall(_norm(text))]


_TOKEN = re.compile(r"\d+(?:[.,:]\d+)*|[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)


def _analyse(text: str, lang: str | None) -> tuple[list[str], list[str]]:
    """Split text into (numbers in reading order as digit strings, the remaining words).

    English number words are folded into numbers ('twenty five' -> '25'); groups
    spoken separately stay separate ('twenty twenty six' -> '20', '26')."""
    nums: list[str] = []
    words: list[str] = []
    total = current = 0
    in_num = False
    last = ""

    def flush():
        nonlocal total, current, in_num, last
        if in_num:
            nums.append(str(total + current))
        total = current = 0
        in_num = False
        last = ""

    english = lang in (None, "en")
    for tok in _TOKEN.findall(_norm(text)):
        if tok[0].isdigit():
            flush()
            s = tok.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", tok) else tok
            nums.extend(p for p in re.split(r"[:.,]", s) if p)
            continue
        w = tok.casefold()
        if english and w in _EN_UNITS:
            if in_num and last in ("unit", "teen") or (last == "tens" and _EN_UNITS[w] >= 10):
                flush()
            current += _EN_UNITS[w]
            in_num, last = True, ("teen" if _EN_UNITS[w] >= 10 else "unit")
        elif english and w in _EN_TENS:
            if in_num and last in ("unit", "teen", "tens"):
                flush()
            current += _EN_TENS[w]
            in_num, last = True, "tens"
        elif english and w in _EN_SCALES and in_num:
            if _EN_SCALES[w] == 100:
                current *= 100
            else:
                total += current * _EN_SCALES[w]
                current = 0
            last = "scale"
        elif english and w == "and" and in_num and last == "scale":
            continue
        else:
            flush()
            words.append(w)
    flush()
    return nums, words


def numbers(text: str, lang: str | None) -> list[str]:
    return _analyse(text, lang)[0]


def _numbers_match(a: list[str], b: list[str]) -> bool:
    # Equal as a multiset, or equal once every digit is read in order ("twenty twenty six" vs "2026").
    return Counter(a) == Counter(b) or "".join(a) == "".join(b)


def negations(text: str, lang: str | None) -> int:
    words = _words(text)
    vocab = NEGATIONS.get(lang or "", set()).union(NEGATIONS["en"] if lang in (None, "en") else set())
    n = sum(1 for w in words if w in vocab)
    if lang in (None, "en"):
        n += sum(1 for w in words if _CONTRACTION_NEG.search(w) and w not in vocab)
    return n


def names(text: str) -> set[str]:
    """Capitalised Latin-script words that are not sentence-initial (likely proper nouns)."""
    out = set()
    for sentence in re.split(r"(?<=[.!?])\s+", _norm(text)):
        tokens = _WORD.findall(sentence)
        for tok in tokens[1:]:
            if tok[0].isupper() and tok[0].isascii() and tok.casefold() != "i" and not tok.isupper():
                out.add(tok.casefold())
    return out


def check_cleanup(source: str, candidate: str, lang: str | None, protected_terms: list[str]) -> GuardResult:
    reasons: list[str] = []
    cand = candidate.strip()
    if not cand:
        return GuardResult(False, ["empty"]) if source.strip() else GuardResult(True)

    src_nums, src_words = _analyse(source, lang)
    cand_nums, cand_words = _analyse(cand, lang)
    src_set = set(src_words)

    if not _numbers_match(src_nums, cand_nums):
        reasons.append("numbers_changed")

    if negations(source, lang) != negations(cand, lang):
        reasons.append("negation_changed")

    cand_set = set(cand_words)
    src_names, cand_names = names(source), names(cand)
    if any(n not in cand_set for n in src_names):
        reasons.append("name_dropped")
    if any(n not in src_set for n in cand_names):
        reasons.append("name_added")
    low = _norm(cand).casefold()
    if any(_norm(t).casefold() not in low for t in protected_terms):
        reasons.append("term_dropped")

    n_src, n_cand = len(src_words) + len(src_nums), len(cand_words) + len(cand_nums)
    if n_src >= 6:
        ratio = n_cand / n_src
        if ratio < 0.6:
            reasons.append("too_short")
        elif ratio > 1.3:
            reasons.append("too_long")
    elif abs(n_cand - n_src) > 3:
        reasons.append("length_changed")

    novel = [w for w in cand_words if w not in src_set and w not in _ALLOWED_NEW
             and not difflib.get_close_matches(w, src_set, n=1, cutoff=0.75)]
    if len(novel) > max(1, int(0.15 * n_cand)):
        reasons.append("new_words")

    return GuardResult(not reasons, reasons)


def check_translation(source: str, candidate: str) -> GuardResult:
    cand = candidate.strip()
    if not cand:
        return GuardResult(False, ["empty"]) if source.strip() else GuardResult(True)
    reasons = []
    src_digits = numbers(source, "xx")
    cand_digits = numbers(cand, "xx")
    if src_digits and not _numbers_match(src_digits, cand_digits):
        reasons.append("numbers_changed")
    ratio = len(cand) / max(1, len(source))
    if len(source) > 20 and not 0.3 <= ratio <= 3.0:
        reasons.append("length_ratio")
    if len(source) > 20 and _norm(cand).casefold() == _norm(source).casefold():
        reasons.append("untranslated")
    return GuardResult(not reasons, reasons)
