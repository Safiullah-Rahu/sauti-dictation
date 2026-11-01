"""Concurrent-user benchmark against a running Sauti server.

Simulated users behave like push-to-talk dictation: stream a real FLEURS clip
at real-time speed, release, wait for the text, pause 2-5 s, repeat. Languages
rotate across English, Somali, Arabic and Swahili.

    python bench/load_test.py --server http://127.0.0.1:8700 --admin admin --admin-password-file pw.txt \
        --fleurs ../data/fleurs --users 1,3,5 --duration 180 --out bench/results/load.json

Reported per concurrency level (all measured, nothing estimated):
  time to first text   start of speech -> first live preview on screen
  final latency        key release -> final cleaned text received
  queue / asr / cleanup  server-side breakdown of the final latency
  accuracy             WER of the recognised text against the FLEURS transcript
  resources            CPU and memory of the server and the LLM process, plus whole-machine CPU
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
import sys
import time
import uuid
from pathlib import Path

import httpx
import psutil
import websockets

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(encoding="utf-8")

from audio import read_wav, resample, to_pcm16  # noqa: E402
from fleurs import load  # noqa: E402
from metrics import normalise, percentile, wer_counts  # noqa: E402

LANGS = ["en", "so", "ar", "sw"]


def find_procs(port: int) -> dict[str, psutil.Process]:
    out = {}
    for p in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (p.info["name"] or "").lower()
            cmd = " ".join(p.info["cmdline"] or [])
            if name.startswith("llama-server"):
                out["llm"] = p
            elif "python" in name and any(k in cmd for k in ("dev_server.py", "sauti_server", "sauti-server")):
                for c in p.connections(kind="tcp"):
                    if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
                        out["server"] = p
        except (psutil.Error, OSError):
            continue
    return out


async def sampler(procs: dict[str, psutil.Process], stop: asyncio.Event, samples: list, pinned: list[int]):
    for p in procs.values():
        p.cpu_percent(None)
    psutil.cpu_percent(None, percpu=True)
    while not stop.is_set():
        await asyncio.sleep(1.0)
        per = psutil.cpu_percent(None, percpu=True)
        row = {"t": time.time(), "machine_cpu": sum(per) / len(per),
               "pinned_cpu": (sum(per[i] for i in pinned) / len(pinned)) if pinned else None,
               "other_cpu": (sum(v for i, v in enumerate(per) if i not in pinned) / max(1, len(per) - len(pinned)))
               if pinned else None}
        for key, p in procs.items():
            try:
                cpu = p.cpu_percent(None)
                row[f"{key}_cores"] = cpu / 100  # logical CPUs kept busy
                row[f"{key}_rss_mb"] = p.memory_info().rss / 2**20
            except psutil.Error:
                pass
        samples.append(row)


async def user_loop(idx: int, token: str, ws_url: str, clips: dict[str, list], until: float, records: list,
                    mode: str, rng: random.Random):
    await asyncio.sleep(rng.uniform(0, 4))
    order = LANGS[idx % 4:] + LANGS[:idx % 4]
    k = 0
    async with websockets.connect(ws_url, max_size=2**20) as ws:
        await ws.send(json.dumps({"type": "hello", "token": token, "client": "bench/1", "protocol": 1}))
        assert json.loads(await ws.recv())["type"] == "ready"
        while time.monotonic() < until:
            lang = order[k % 4]
            clip, pcm, dur = clips[lang][(k // 4 + idx * 7) % len(clips[lang])]
            k += 1
            uid = str(uuid.uuid4())
            rec = {"user": idx, "lang": lang, "speech_s": round(dur, 2), "sid": clip.sid}
            first_partial: list[float] = []
            final_msg: dict = {}
            t_start = time.monotonic()

            async def reader():
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("utterance_id") != uid:
                        continue
                    if m["type"] == "partial" and not first_partial:
                        first_partial.append(time.monotonic())
                    elif m["type"] in ("final", "error"):
                        final_msg.update(m)
                        final_msg["_t"] = time.monotonic()
                        return

            await ws.send(json.dumps({"type": "start", "utterance_id": uid, "mode": mode, "lang": "auto",
                                      "target_lang": "en"}))
            rd = asyncio.create_task(reader())
            step = 3200
            for i in range(0, len(pcm), step):
                await ws.send(pcm[i:i + step])
                await asyncio.sleep(max(0.0, t_start + (i // step + 1) * 0.1 - time.monotonic()))
            t_end = time.monotonic()
            await ws.send(json.dumps({"type": "end", "utterance_id": uid}))
            try:
                await asyncio.wait_for(rd, timeout=120)
            except asyncio.TimeoutError:
                rec["error"] = "client_timeout"
                records.append(rec)
                continue
            if final_msg["type"] == "error":
                rec["error"] = final_msg.get("code")
            else:
                await ws.send(json.dumps({"type": "ack", "utterance_id": uid}))
                rec["final_latency_ms"] = round((final_msg["_t"] - t_end) * 1000)
                rec["ttft_ms"] = round((first_partial[0] - t_start) * 1000) if first_partial else None
                rec["server"] = final_msg.get("timings_ms", {})
                rec["detected"] = final_msg.get("lang")
                rec["notices"] = final_msg.get("notices", [])
                e, n = wer_counts(normalise(clip.text, lang), normalise(final_msg.get("raw", ""), lang))
                rec["wer_e"], rec["wer_n"] = e, n
            records.append(rec)
            await asyncio.sleep(rng.uniform(2, 5))


def summarise(records: list, samples: list, n_users: int, duration: float) -> dict:
    ok = [r for r in records if "final_latency_ms" in r]
    q = lambda vals, p: (round(percentile(vals, p)) if vals else None)  # noqa: E731
    fl = [r["final_latency_ms"] for r in ok]
    tt = [r["ttft_ms"] for r in ok if r.get("ttft_ms") is not None]
    out = {
        "users": n_users, "duration_s": duration, "utterances": len(records), "completed": len(ok),
        "errors": {}, "speech_s_mean": round(sum(r["speech_s"] for r in records) / max(1, len(records)), 1),
        "final_latency_ms": {"p50": q(fl, .5), "p95": q(fl, .95), "max": max(fl) if fl else None},
        "time_to_first_text_ms": {"p50": q(tt, .5), "p95": q(tt, .95), "share_with_preview": round(len(tt) / max(1, len(ok)), 2)},
        "server_queue_ms": {"p50": q([r["server"].get("queue", 0) for r in ok], .5), "p95": q([r["server"].get("queue", 0) for r in ok], .95)},
        "server_asr_ms": {"p50": q([r["server"].get("asr", 0) for r in ok], .5), "p95": q([r["server"].get("asr", 0) for r in ok], .95)},
        "server_cleanup_ms": {"p50": q([r["server"].get("cleanup", 0) for r in ok], .5), "p95": q([r["server"].get("cleanup", 0) for r in ok], .95)},
        "cleanup_fallbacks": sum(1 for r in ok if "cleanup_fallback" in r.get("notices", [])),
        "lid_accuracy": round(sum(1 for r in ok if r.get("detected") == r["lang"]) / max(1, len(ok)), 3),
        "wer_by_lang": {},
    }
    for r in records:
        if "error" in r:
            out["errors"][r["error"]] = out["errors"].get(r["error"], 0) + 1
    for lang in LANGS:
        rs = [r for r in ok if r["lang"] == lang]
        if rs:
            out["wer_by_lang"][lang] = round(sum(r["wer_e"] for r in rs) / max(1, sum(r["wer_n"] for r in rs)), 3)
    if samples:
        def mean(key):
            vals = [s[key] for s in samples if s.get(key) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None
        out["resources"] = {
            "machine_cpu_pct": mean("machine_cpu"), "pinned_cores_cpu_pct": mean("pinned_cpu"),
            "other_cores_cpu_pct": mean("other_cpu"),
            "server_busy_cpus": mean("server_cores"), "llm_busy_cpus": mean("llm_cores"),
            "server_rss_mb_max": round(max((s.get("server_rss_mb") or 0) for s in samples)),
            "llm_rss_mb_max": round(max((s.get("llm_rss_mb") or 0) for s in samples)),
        }
    return out


async def run_level(n: int, tokens: list[str], ws_url: str, clips, duration: float, mode: str, procs, pinned):
    records: list = []
    samples: list = []
    stop = asyncio.Event()
    samp = asyncio.create_task(sampler(procs, stop, samples, pinned))
    until = time.monotonic() + duration
    rng = random.Random(42 + n)
    await asyncio.gather(*(user_loop(i, tokens[i], ws_url, clips, until, records, mode, rng) for i in range(n)))
    stop.set()
    await samp
    return summarise(records, samples, n, duration), records


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8700")
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--admin-password-file", type=Path, required=True)
    ap.add_argument("--fleurs", type=Path, required=True)
    ap.add_argument("--users", default="1,3,5")
    ap.add_argument("--duration", type=float, default=180)
    ap.add_argument("--mode", default="clean")
    ap.add_argument("--max-clip-s", type=float, default=14.0)
    ap.add_argument("--pinned", default="", help="CPU ids the server/LLM are pinned to (for reporting), e.g. 0-11")
    ap.add_argument("--label", default="")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    pinned = []
    for part in filter(None, args.pinned.split(",")):
        a, _, b = part.partition("-")
        pinned.extend(range(int(a), int(b or a) + 1))
    levels = [int(x) for x in args.users.split(",")]
    async with httpx.AsyncClient(base_url=args.server, timeout=30) as http:
        pw = args.admin_password_file.read_text(encoding="utf-8").strip()
        adm = (await http.post("/v1/auth/login", json={"username": args.admin, "password": pw})).json()["token"]
        h = {"Authorization": f"Bearer {adm}"}
        existing = {u["username"] for u in (await http.get("/v1/admin/users", headers=h)).json()["items"]}
        tokens = []
        for i in range(max(levels)):
            name = f"bench{i + 1}"
            secret = secrets.token_urlsafe(12)
            if name in existing:
                uid = next(u["id"] for u in (await http.get("/v1/admin/users", headers=h)).json()["items"] if u["username"] == name)
                await http.patch(f"/v1/admin/users/{uid}", headers=h, json={"new_password": secret})
            else:
                await http.post("/v1/admin/users", headers=h, json={"username": name, "password": secret,
                                                                     "daily_limit_minutes": 100000})
            r = await http.post("/v1/devices/register", json={"username": name, "password": secret,
                                                                "device_name": "load generator", "platform": "other"})
            tokens.append(r.json()["device_token"])
        overview = (await http.get("/v1/admin/overview", headers=h)).json()

    clips = {}
    for lang in LANGS:
        chosen = []
        for c in load(args.fleurs, lang)[60:]:  # skip the clips used by the accuracy benchmark
            x, sr = read_wav(c.path)
            x = resample(x, sr)
            if len(x) / 16000 <= args.max_clip_s:
                chosen.append((c, to_pcm16(x), len(x) / 16000))
            if len(chosen) >= 25:
                break
        clips[lang] = chosen
    port = int(args.server.rsplit(":", 1)[1].split("/")[0])
    procs = find_procs(port)
    ws_url = ("wss://" + args.server[8:] if args.server.startswith("https") else "ws://" + args.server[7:]) + "/v1/dictate"
    report = {"label": args.label, "server_models": overview["models"], "machine": {
        "cpu": "measured host", "logical_cpus": psutil.cpu_count(), "pinned": args.pinned or None},
        "levels": [], "raw": {}}
    for n in levels:
        print(f"== {n} concurrent users for {args.duration:.0f} s", flush=True)
        summary, records = await run_level(n, tokens, ws_url, clips, args.duration, args.mode, procs, pinned)
        report["levels"].append(summary)
        report["raw"][str(n)] = records
        print(json.dumps(summary, indent=2), flush=True)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
