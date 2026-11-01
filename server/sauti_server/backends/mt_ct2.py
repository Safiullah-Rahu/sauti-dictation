"""Dedicated translation models through CTranslate2: MADLAD-400 (Apache-2.0, default)
or NLLB-200 (CC-BY-NC-4.0 - research/non-commercial only)."""
from __future__ import annotations

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..config import TranslateSection

NLLB_CODES = {"en": "eng_Latn", "so": "som_Latn", "ar": "arb_Arab", "sw": "swh_Latn"}
_SENT = re.compile(r"(?<=[.!?؟])\s+")


class Ct2Translator:
    name = "ct2"

    def __init__(self, cfg: TranslateSection):
        import ctranslate2
        import sentencepiece

        self.cfg = cfg
        path = Path(cfg.model_path)
        self.family = "nllb" if "nllb" in path.name.lower() else "madlad"
        sp_file = path / ("sentencepiece.bpe.model" if self.family == "nllb" else "spiece.model")
        self.sp = sentencepiece.SentencePieceProcessor(model_file=str(sp_file))
        self.tr = ctranslate2.Translator(str(path), device="cpu", compute_type=cfg.compute_type,
                                         inter_threads=cfg.workers, intra_threads=cfg.cpu_threads)
        self.pool = ThreadPoolExecutor(max_workers=cfg.workers, thread_name_prefix="mt")

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name, "family": self.family, "model_path": self.cfg.model_path,
                "compute_type": self.cfg.compute_type}

    def _translate_sync(self, text: str, src: str, tgt: str) -> str:
        sentences = [s for s in _SENT.split(text.strip()) if s]
        if not sentences:
            return ""
        if self.family == "nllb":
            batch = [[NLLB_CODES[src], *self.sp.encode(s, out_type=str), "</s>"] for s in sentences]
            prefix = [[NLLB_CODES[tgt]] for _ in sentences]
            res = self.tr.translate_batch(batch, target_prefix=prefix, beam_size=self.cfg.beam_size,
                                          max_decoding_length=256)
            outs = [self.sp.decode(r.hypotheses[0][1:]) for r in res]
        else:
            batch = [self.sp.encode(f"<2{tgt}> {s}", out_type=str) for s in sentences]
            res = self.tr.translate_batch(batch, beam_size=self.cfg.beam_size, max_decoding_length=256)
            outs = [self.sp.decode(r.hypotheses[0]) for r in res]
        return " ".join(o.strip() for o in outs)

    async def translate(self, text: str, src: str, tgt: str, *, timeout: float) -> str:
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(loop.run_in_executor(self.pool, self._translate_sync, text, src, tgt),
                                      timeout)
