"""Mixed-language speech: two FLEURS sentences in different languages, spoken back to back.

This models a dictation that switches language between sentences (for example an
English instruction followed by a Somali sentence). It is a synthetic splice of
two speakers, so it does not cover switching inside a sentence.

    python bench/mixed_language.py --fleurs ../data/fleurs --model ../models/faster-whisper-large-v3-turbo \
        --n 8 --out bench/results/mixed.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

from audio import read_wav, resample  # noqa: E402
from fleurs import load  # noqa: E402
from metrics import normalise, wer_counts  # noqa: E402

PAIRS = [("en", "so"), ("so", "en"), ("en", "ar"), ("ar", "en"), ("en", "sw"), ("sw", "en")]
ALLOWED = ["en", "so", "ar", "sw"]


def run(model, audio, multilingual: bool):
    segs, info = model.transcribe(audio, language=None, beam_size=1, vad_filter=True, without_timestamps=True,
                                  condition_on_previous_text=False, multilingual=multilingual)
    text = "".join(s.text for s in segs).strip()
    return text, info.language


def part_wer(ref_a: str, ref_b: str, hyp: str, la: str, lb: str) -> tuple[float, float]:
    """Score each half against the best-matching split point of the hypothesis."""
    h = hyp.split()
    best = None
    for k in range(len(h) + 1):
        ea, na = wer_counts(normalise(ref_a, la), normalise(" ".join(h[:k]), la))
        eb, nb = wer_counts(normalise(ref_b, lb), normalise(" ".join(h[k:]), lb))
        if best is None or ea + eb < best[0]:
            best = (ea + eb, ea / max(1, na), eb / max(1, nb))
    return best[1], best[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleurs", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--pin", default="")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.pin:
        from pinning import pin
        pin(args.pin, "high")
    from faster_whisper import WhisperModel

    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=args.threads)
    clips = {lang: load(args.fleurs, lang)[100:100 + args.n * 2] for lang in ALLOWED}
    report = {"model": Path(args.model).name, "pairs": {}, "examples": []}
    for la, lb in PAIRS:
        res = {"single_window": {"a": [], "b": [], "detected": []}, "per_segment_lid": {"a": [], "b": []}}
        for i in range(args.n):
            ca, cb = clips[la][i], clips[lb][args.n + i]
            xa, sa = read_wav(ca.path)
            xb, sb = read_wav(cb.path)
            audio = np.concatenate([resample(xa, sa), np.zeros(8000, np.float32), resample(xb, sb)])
            for key, multi in (("single_window", False), ("per_segment_lid", True)):
                hyp, lang = run(model, audio, multi)
                wa, wb = part_wer(ca.text, cb.text, hyp, la, lb)
                res[key]["a"].append(wa)
                res[key]["b"].append(wb)
                if key == "single_window":
                    res[key]["detected"].append(lang)
                if i == 0:
                    report["examples"].append({"pair": f"{la}+{lb}", "mode": key, "hyp": hyp,
                                               "ref": ca.text + " | " + cb.text})
            print(f"{la}+{lb} {i + 1}/{args.n}", flush=True)
        report["pairs"][f"{la}+{lb}"] = {
            k: {"wer_first": round(float(np.mean(v["a"])), 3), "wer_second": round(float(np.mean(v["b"])), 3)}
            for k, v in res.items()}
        report["pairs"][f"{la}+{lb}"]["detected_first_window"] = {
            l: res["single_window"]["detected"].count(l) for l in set(res["single_window"]["detected"])}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["pairs"], indent=2))


if __name__ == "__main__":
    main()
