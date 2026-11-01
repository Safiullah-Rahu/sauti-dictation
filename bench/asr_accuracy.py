"""Recognition accuracy per language (and language identification) on FLEURS.

    python bench/asr_accuracy.py --fleurs ../data/fleurs --model ../models/faster-whisper-large-v3-turbo \
        --n 40 --threads 6 --out bench/results/asr_turbo.json

Each clip is run twice: with the language given (what a user who picks a
language gets) and in auto mode restricted to the four configured languages.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

from audio import read_wav, resample  # noqa: E402
from fleurs import load  # noqa: E402
from metrics import cer_counts, normalise, wer_counts  # noqa: E402

LANGS = ["en", "so", "ar", "sw"]


def transcribe(model, audio, lang, beam):
    segs, info = model.transcribe(audio, language=lang, beam_size=beam, vad_filter=True, without_timestamps=True,
                                  condition_on_previous_text=False, temperature=[0.0, 0.2, 0.4])
    text = "".join(s.text for s in segs).strip()
    return text, info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleurs", type=Path, required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--beam", type=int, default=1)
    ap.add_argument("--compute-type", default="auto", help="auto = float16 on GPU, int8 on CPU")
    ap.add_argument("--device", default="auto", help="auto | cpu | cuda")
    ap.add_argument("--langs", default=",".join(LANGS))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--pin", default="", help="logical CPUs to pin to, e.g. 0-11 (6 P-cores with HT)")
    ap.add_argument("--priority", default="high", choices=["high", "above_normal", "normal"])
    args = ap.parse_args()
    from pinning import pin
    pinned = pin(args.pin, args.priority) if args.pin else "none"

    from faster_whisper import WhisperModel

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
    from sauti_server.devices import resolve_compute_type, resolve_device
    device = resolve_device(args.device)
    args.compute_type = resolve_compute_type(args.compute_type, device)
    model = WhisperModel(args.model, device=device, compute_type=args.compute_type, cpu_threads=args.threads)
    langs = args.langs.split(",")
    results = {"model": Path(args.model).name, "threads": args.threads, "beam": args.beam,
               "compute_type": args.compute_type, "device": device, "n_per_lang": args.n, "pinning": pinned, "langs": {}, "samples": []}
    for lang in langs:
        clips = load(args.fleurs, lang)[: args.n]
        stats = {"wer_e": 0, "wer_n": 0, "cer_e": 0, "cer_n": 0, "auto_wer_e": 0, "lid_ok": 0, "audio_s": 0.0,
                 "proc_s": 0.0, "n": 0}
        for clip in clips:
            x, sr = read_wav(clip.path)
            x = resample(x, sr)
            ref = normalise(clip.text, lang)
            t0 = time.perf_counter()
            hyp, _ = transcribe(model, x, lang, args.beam)
            dt = time.perf_counter() - t0
            # Auto mode, restricted to the configured languages (as the server does).
            auto_hyp, info = transcribe(model, x, None, args.beam)
            detected = info.language
            if detected not in langs:
                probs = dict(info.all_language_probs or [])
                detected = max(langs, key=lambda c: probs.get(c, 0.0))
                auto_hyp, _ = transcribe(model, x, detected, args.beam)
            e, n = wer_counts(ref, normalise(hyp, lang))
            ce, cn = cer_counts(ref, normalise(hyp, lang))
            ae, _ = wer_counts(ref, normalise(auto_hyp, lang))
            stats["wer_e"] += e
            stats["wer_n"] += n
            stats["cer_e"] += ce
            stats["cer_n"] += cn
            stats["auto_wer_e"] += ae
            stats["lid_ok"] += int(detected == lang)
            stats["audio_s"] += len(x) / 16000
            stats["proc_s"] += dt
            stats["n"] += 1
            if len(results["samples"]) < 200 and stats["n"] <= 5:
                results["samples"].append({"lang": lang, "ref": clip.text, "hyp": hyp, "detected": info.language})
            print(f"[{lang} {stats['n']}/{len(clips)}] wer_so_far={stats['wer_e'] / max(1, stats['wer_n']):.3f} "
                  f"rtf={stats['proc_s'] / stats['audio_s']:.2f}", flush=True)
        results["langs"][lang] = {
            "clips": stats["n"],
            "wer": round(stats["wer_e"] / max(1, stats["wer_n"]), 4),
            "cer": round(stats["cer_e"] / max(1, stats["cer_n"]), 4),
            "wer_auto": round(stats["auto_wer_e"] / max(1, stats["wer_n"]), 4),
            "lid_accuracy": round(stats["lid_ok"] / max(1, stats["n"]), 3),
            "audio_minutes": round(stats["audio_s"] / 60, 2),
            "rtf": round(stats["proc_s"] / max(1e-9, stats["audio_s"]), 3),
        }
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results["langs"], indent=2))


if __name__ == "__main__":
    main()
