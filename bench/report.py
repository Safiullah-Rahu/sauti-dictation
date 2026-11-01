"""Render bench/results/*.json into docs/benchmarks.md tables.

    python bench/report.py > docs/benchmark-tables.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
RES = Path(__file__).resolve().parent / "results"
NAMES = {"en": "English", "so": "Somali", "ar": "Arabic", "sw": "Swahili"}


def load(name: str):
    p = RES / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def pct(x):
    return "—" if x is None else f"{100 * x:.1f}%"


def asr_tables() -> None:
    files = sorted(RES.glob("asr_*.json"))
    if not files:
        return
    print("### Recognition accuracy (FLEURS dev, word error rate — lower is better)\n")
    print("| Model | Language | Clips | WER (language given) | CER | WER (auto-detect) | Language ID correct | Speed (RTF) |")
    print("|---|---|---:|---:|---:|---:|---:|---:|")
    for f in files:
        r = json.loads(f.read_text(encoding="utf-8"))
        for lang, s in r["langs"].items():
            wer_auto = pct(s.get("wer_auto")) if s.get("wer_auto") is not None else "—"
            lid = pct(s.get("lid_accuracy")) if s.get("lid_accuracy") is not None else "—"
            print(f"| {r['model']} | {NAMES.get(lang, lang)} | {s['clips']} | {pct(s['wer'])} | {pct(s['cer'])} | "
                  f"{wer_auto} | {lid} | {s['rtf'] if s.get('rtf') is not None else '—'} |")
    print()


def load_tables() -> None:
    for f in sorted(RES.glob("load*.json")):
        r = json.loads(f.read_text(encoding="utf-8"))
        print(f"### Concurrent users — {r.get('label') or f.stem}\n")
        print("| Users | Dictations | Time to first text p50 / p95 | Final text after release p50 / p95 | Queue wait p50 / p95 | "
              "Recognition p50 / p95 | Cleanup p50 / p95 | Errors | Server CPUs busy | LLM CPUs busy | Machine CPU |")
        print("|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|")
        for lv in r["levels"]:
            res = lv.get("resources", {})
            ms = lambda d: f"{d.get('p50') if d.get('p50') is not None else '—'} / {d.get('p95') if d.get('p95') is not None else '—'} ms"  # noqa: E731
            errs = ", ".join(f"{k} {v}" for k, v in lv["errors"].items()) or "none"
            print(f"| {lv['users']} | {lv['completed']}/{lv['utterances']} | {ms(lv['time_to_first_text_ms'])} | "
                  f"{ms(lv['final_latency_ms'])} | {ms(lv['server_queue_ms'])} | {ms(lv['server_asr_ms'])} | "
                  f"{ms(lv['server_cleanup_ms'])} | {errs} | {res.get('server_busy_cpus', '—')} | "
                  f"{res.get('llm_busy_cpus', '—')} | {res.get('machine_cpu_pct', '—')}% |")
        print()
        print("| Users | WER en | WER so | WER ar | WER sw | Language ID correct | Cleanup fallbacks | Mean speech length |")
        print("|---:|---:|---:|---:|---:|---:|---:|---:|")
        for lv in r["levels"]:
            w = lv["wer_by_lang"]
            print(f"| {lv['users']} | {pct(w.get('en'))} | {pct(w.get('so'))} | {pct(w.get('ar'))} | {pct(w.get('sw'))} | "
                  f"{pct(lv['lid_accuracy'])} | {lv['cleanup_fallbacks']} | {lv['speech_s_mean']} s |")
        print()


def cleanup_table() -> None:
    r = load("cleanup.json")
    if not r:
        return
    print("### Cleanup: disfluency removal without changing words (WER vs the original sentence — lower is better)\n")
    print("| Language | Items | Rules only | LLM output (unchecked) | LLM + guard (what users get) | Guard fallbacks | LLM latency p50 / p95 |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for lang, s in r["langs"].items():
        print(f"| {NAMES[lang]} | {s['items']} | {pct(s['wer_rules_only'])} | {pct(s['wer_llm_raw'])} | "
              f"{pct(s['wer_llm_with_guard'])} | {pct(s['guard_fallback_rate'])} | {s['llm_latency_ms_p50']} / {s['llm_latency_ms_p95']} ms |")
    print()
    if r.get("injection"):
        print("Dictated instructions (the cleanup must transcribe them, not obey them):\n")
        print("| Dictated | Delivered text |")
        print("|---|---|")
        for it in r["injection"]:
            print(f"| {it['dictated']} | {it['final']} |")
        print()


def translate_table() -> None:
    r = load("translate.json")
    if not r:
        return
    print("### Translation quality (chrF on FLEURS/FLORES sentences — higher is better)\n")
    dirs = list(next(iter(r["systems"].values())).keys())
    print("| System | " + " | ".join(dirs) + " |")
    print("|---|" + "---:|" * len(dirs))
    for name, s in r["systems"].items():
        print(f"| {name} | " + " | ".join(f"{s[d]['chrf']} ({s[d]['latency_ms_p50']} ms)" if d in s else "—" for d in dirs) + " |")
    print()


def mixed_table() -> None:
    r = load("mixed.json")
    if not r:
        return
    print("### Mixed-language speech (two sentences, different languages, back to back)\n")
    print("| Pair | First part WER | Second part WER | Second part WER with per-segment language ID | First window detected as |")
    print("|---|---:|---:|---:|---|")
    for pair, s in r["pairs"].items():
        det = ", ".join(f"{k}×{v}" for k, v in s.get("detected_first_window", {}).items())
        print(f"| {pair} | {pct(s['single_window']['wer_first'])} | {pct(s['single_window']['wer_second'])} | "
              f"{pct(s['per_segment_lid']['wer_second'])} | {det} |")
    print()


if __name__ == "__main__":
    asr_tables()
    load_tables()
    cleanup_table()
    translate_table()
    mixed_table()
