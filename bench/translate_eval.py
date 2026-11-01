"""Translation quality and speed on FLEURS (which reads FLORES sentences, so the
same sentence id is a human translation in every language).

    python bench/translate_eval.py --fleurs ../data/fleurs --llm http://127.0.0.1:8081/v1 \
        --madlad ../models/madlad400-3b-mt-ct2-int8 --n 30 --out bench/results/translate.json

Note: FLORES is widely used as an evaluation set, so it may have been seen
during model development; treat absolute scores as optimistic and use them to
compare systems.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.stdout.reconfigure(encoding="utf-8")

from fleurs import parallel  # noqa: E402
from metrics import chrf, percentile  # noqa: E402
from sauti_server.backends.llm_openai import OpenAICompatLlm  # noqa: E402
from sauti_server.config import LlmSection, TranslateSection  # noqa: E402
from sauti_server.text.llm_tasks import max_tokens_for, translate_messages  # noqa: E402

DIRECTIONS = [("so", "en"), ("sw", "en"), ("ar", "en"), ("en", "so"), ("en", "sw"), ("en", "ar")]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fleurs", type=Path, required=True)
    ap.add_argument("--llm", default="")
    ap.add_argument("--model", default="qwen3-4b-instruct")
    ap.add_argument("--madlad", default="")
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--pin", default="")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.pin:
        from pinning import pin
        pin(args.pin, "high")
    data = list(parallel(args.fleurs, ["en", "so", "ar", "sw"]).values())[: args.n]
    systems = {}
    if args.llm:
        llm = OpenAICompatLlm(LlmSection(base_url=args.llm, model=args.model, timeout_s=180))

        async def via_llm(text, src, tgt):
            return await llm.chat(translate_messages(text, src, tgt, []), max_tokens=max_tokens_for(text) * 2, timeout=180)
        systems["llm:" + args.model] = via_llm
    if args.madlad:
        from sauti_server.backends.mt_ct2 import Ct2Translator
        mt = Ct2Translator(TranslateSection(backend="ct2", model_path=args.madlad, cpu_threads=args.threads, beam_size=2))

        async def via_madlad(text, src, tgt):
            return await mt.translate(text, src, tgt, timeout=300)
        systems["madlad400-3b-mt (ct2 int8)"] = via_madlad

    report = {"sentences": len(data), "systems": {}, "examples": []}
    for name, fn in systems.items():
        report["systems"][name] = {}
        for src, tgt in DIRECTIONS:
            hyps, refs, lat = [], [], []
            for row in data:
                t0 = time.perf_counter()
                hyps.append((await fn(row[src], src, tgt)).strip())
                lat.append((time.perf_counter() - t0) * 1000)
                refs.append(row[tgt])
            score = chrf(hyps, refs)
            report["systems"][name][f"{src}-{tgt}"] = {"chrf": round(score, 1), "latency_ms_p50": round(percentile(lat, .5)),
                                                      "latency_ms_p95": round(percentile(lat, .95))}
            report["examples"].append({"system": name, "dir": f"{src}-{tgt}", "src": data[0][src], "hyp": hyps[0],
                                       "ref": refs[0]})
            print(name, f"{src}-{tgt}", report["systems"][name][f"{src}-{tgt}"], flush=True)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
