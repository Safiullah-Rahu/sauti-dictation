"""Load FLEURS clips (see fetch_fleurs.py) as (sentence id, wav path, transcript)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Clip:
    lang: str
    sid: str
    path: Path
    text: str


def load(root: Path, lang: str, split: str = "dev") -> list[Clip]:
    """One recording per sentence id (the first listed), sorted by id for repeatability."""
    seen: dict[str, Clip] = {}
    with open(root / lang / f"{split}.tsv", encoding="utf-8") as fh:
        for row in csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE):
            sid, fname, raw = row[0], row[1], row[2]
            if sid not in seen:
                seen[sid] = Clip(lang, sid, root / lang / split / fname, raw)
    return [seen[k] for k in sorted(seen, key=int)]


def parallel(root: Path, langs: list[str], split: str = "dev") -> dict[str, dict[str, str]]:
    """sentence id -> {lang: text} for ids present in every language."""
    per = {lang: {c.sid: c.text for c in load(root, lang, split)} for lang in langs}
    common = set.intersection(*(set(d) for d in per.values()))
    return {sid: {lang: per[lang][sid] for lang in langs} for sid in sorted(common, key=int)}
