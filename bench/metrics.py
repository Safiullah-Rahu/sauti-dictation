"""Dependency-free scoring: WER, CER and chrF, plus text normalisation per language."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

_AR_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def normalise(text: str, lang: str) -> str:
    """Lower-case, strip punctuation and (for Arabic) diacritics and letter variants.

    This is the usual normalisation for comparing ASR systems; it scores words,
    not punctuation or casing."""
    t = unicodedata.normalize("NFKC", text).translate(_DIGITS).casefold()
    if lang == "ar":
        t = _AR_DIACRITICS.sub("", t)
        t = re.sub("[أإآٱ]", "ا", t).replace("ة", "ه").replace("ى", "ي")
    t = "".join(ch if not unicodedata.category(ch).startswith(("P", "S")) else " " for ch in t)
    return " ".join(t.split())


def _edits(ref: list, hyp: list) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def wer_counts(ref: str, hyp: str) -> tuple[int, int]:
    r, h = ref.split(), hyp.split()
    return _edits(r, h), len(r)


def cer_counts(ref: str, hyp: str) -> tuple[int, int]:
    r, h = list(ref.replace(" ", "")), list(hyp.replace(" ", ""))
    return _edits(r, h), len(r)


def chrf(hyps: list[str], refs: list[str], order: int = 6, beta: float = 2.0) -> float:
    """Corpus chrF (character n-grams 1..6, beta 2, whitespace ignored), 0-100.

    Follows the sacrebleu chrF definition: n-gram statistics are summed over the
    corpus, precision/recall averaged over n, then combined into F-beta."""
    match = [0] * order
    hyp_tot = [0] * order
    ref_tot = [0] * order
    for h, r in zip(hyps, refs):
        h, r = h.replace(" ", ""), r.replace(" ", "")
        for n in range(1, order + 1):
            hc = Counter(h[i:i + n] for i in range(len(h) - n + 1))
            rc = Counter(r[i:i + n] for i in range(len(r) - n + 1))
            match[n - 1] += sum((hc & rc).values())
            hyp_tot[n - 1] += sum(hc.values())
            ref_tot[n - 1] += sum(rc.values())
    precs = [m / t if t else 0.0 for m, t in zip(match, hyp_tot)]
    recs = [m / t if t else 0.0 for m, t in zip(match, ref_tot)]
    p, r = sum(precs) / order, sum(recs) / order
    if p + r == 0:
        return 0.0
    return 100 * (1 + beta**2) * p * r / (beta**2 * p + r)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]
