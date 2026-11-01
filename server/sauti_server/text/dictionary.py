"""Personal and organisation dictionaries.

A dictionary entry is a canonical term plus optional "sounds like" aliases, e.g.
``{"term": "Hargeisa", "aliases": ["hargaysa", "hargeysa"]}``. Terms are used three ways:
as recognition hints for the ASR model, as alias -> term replacements after
recognition, and as words the cleanup step must keep exactly.
"""
from __future__ import annotations

import re
from typing import Iterable

MAX_HINT_CHARS = 220


def merge(personal: Iterable[dict], org: Iterable[dict]) -> list[dict]:
    """Personal entries win over organisation entries with the same term."""
    seen: dict[str, dict] = {}
    for entry in list(org) + list(personal):
        term = (entry.get("term") or "").strip()
        if term:
            seen[term.casefold()] = {"term": term, "aliases": [a.strip() for a in entry.get("aliases", []) if a.strip()]}
    return list(seen.values())


def hint_text(entries: Iterable[dict]) -> str | None:
    """Space-separated terms for the recogniser's hotword prompt, bounded in length."""
    out: list[str] = []
    size = 0
    for e in entries:
        t = e["term"]
        if size + len(t) + 1 > MAX_HINT_CHARS:
            break
        out.append(t)
        size += len(t) + 1
    return " ".join(out) or None


def _alias_pattern(alias: str) -> re.Pattern[str]:
    escaped = r"\s+".join(re.escape(part) for part in alias.split())
    return re.compile(rf"(?<!\w){escaped}(?!\w)", re.IGNORECASE)


def apply_aliases(text: str, entries: Iterable[dict]) -> str:
    """Replace recognised aliases with their canonical spelling (whole words, any case)."""
    for e in entries:
        for alias in sorted(e.get("aliases", []), key=len, reverse=True):
            text = _alias_pattern(alias).sub(e["term"], text)
        # Also normalise the casing of the term itself ("proxmox" -> "Proxmox").
        text = _alias_pattern(e["term"]).sub(e["term"], text)
    return text


def terms_in(text: str, entries: Iterable[dict]) -> list[str]:
    """Dictionary terms that occur in ``text`` (used by the guard)."""
    return [e["term"] for e in entries if _alias_pattern(e["term"]).search(text)]
