"""Fetch the model weights Sauti uses into a local models directory.

Every model here is pulled once from Hugging Face and then served entirely
offline; nothing in the running system talks to Hugging Face.

    python scripts/download_models.py --dest /opt/sauti/models            # defaults
    python scripts/download_models.py --dest ./models --set asr-large-v3   # one extra set

Sets (licence in brackets):
    asr-turbo      Whisper large-v3-turbo, CTranslate2 int8-ready   [MIT]
    asr-large-v3   Whisper large-v3, CTranslate2                    [MIT]
    asr-small      Whisper small, CTranslate2 (fast partial preview)[MIT]
    llm-qwen3-4b   Qwen3-4B-Instruct-2507 GGUF Q4_K_M               [Apache-2.0]
    mt-madlad-3b   MADLAD-400 3B MT, CTranslate2 int8               [Apache-2.0]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

SETS = {
    "asr-turbo": ("snapshot", "mobiuslabsgmbh/faster-whisper-large-v3-turbo", "faster-whisper-large-v3-turbo", None),
    "asr-large-v3": ("snapshot", "Systran/faster-whisper-large-v3", "faster-whisper-large-v3", None),
    "asr-small": ("snapshot", "Systran/faster-whisper-small", "faster-whisper-small", None),
    "llm-qwen3-4b": ("file", "unsloth/Qwen3-4B-Instruct-2507-GGUF", "gguf", "Qwen3-4B-Instruct-2507-Q4_K_M.gguf"),
    "mt-madlad-3b": ("snapshot", "Nextcloud-AI/madlad400-3b-mt-ct2-int8", "madlad400-3b-mt-ct2-int8", None),
}
DEFAULT = ["asr-turbo", "asr-small", "llm-qwen3-4b"]


def fetch(name: str, dest: Path) -> Path:
    kind, repo, sub, filename = SETS[name]
    target = dest / sub
    target.mkdir(parents=True, exist_ok=True)
    if kind == "file":
        path = hf_hub_download(repo_id=repo, filename=filename, local_dir=target)
    else:
        path = snapshot_download(repo_id=repo, local_dir=target, allow_patterns=["*.json", "*.bin", "*.txt", "*.model", "*.md"])
    print(f"[ok] {name} -> {path}", flush=True)
    return Path(path)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", type=Path, default=Path("models"))
    ap.add_argument("--set", action="append", choices=sorted(SETS), help="model set to fetch (repeatable)")
    args = ap.parse_args()
    for name in args.set or DEFAULT:
        fetch(name, args.dest)


if __name__ == "__main__":
    main()
