"""Download the FLEURS dev split for the four target languages.

FLEURS (Google, CC-BY-4.0) is read speech of FLORES sentences. Because every
language reads the same FLORES sentence ids, the same clips double as a
parallel corpus for scoring translation.

    python bench/fetch_fleurs.py --dest ../data/fleurs
"""
from __future__ import annotations

import argparse
import tarfile
from pathlib import Path

from huggingface_hub import hf_hub_download

LANGS = {"en": "en_us", "so": "so_so", "ar": "ar_eg", "sw": "sw_ke"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, required=True)
    ap.add_argument("--split", default="dev")
    args = ap.parse_args()
    for short, code in LANGS.items():
        out = args.dest / short
        out.mkdir(parents=True, exist_ok=True)
        tsv = hf_hub_download("google/fleurs", f"data/{code}/{args.split}.tsv", repo_type="dataset", local_dir=args.dest / "_raw")
        tgz = hf_hub_download("google/fleurs", f"data/{code}/audio/{args.split}.tar.gz", repo_type="dataset", local_dir=args.dest / "_raw")
        (out / f"{args.split}.tsv").write_bytes(Path(tsv).read_bytes())
        with tarfile.open(tgz) as tf:
            tf.extractall(out, filter="data")
        print(f"[ok] {short}: {sum(1 for _ in out.rglob('*.wav'))} wav files", flush=True)


if __name__ == "__main__":
    main()
