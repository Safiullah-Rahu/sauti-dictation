"""Any OpenAI-compatible chat server on the local network: llama.cpp's llama-server
(default), vLLM, or Ollama. Only localhost/LAN URLs are expected here - the
project rule is that no transcript leaves the organisation's hardware."""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..config import LlmSection
from .base import BackendUnavailable

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class OpenAICompatLlm:
    name = "openai_compat"

    def __init__(self, cfg: LlmSection):
        self.cfg = cfg
        headers = {"Authorization": f"Bearer {cfg.api_key}"} if cfg.api_key else {}
        self.client = httpx.AsyncClient(base_url=cfg.base_url.rstrip("/"), headers=headers,
                                        timeout=httpx.Timeout(cfg.timeout_s, connect=3.0))

    def describe(self) -> dict[str, Any]:
        return {"backend": self.name, "base_url": self.cfg.base_url, "model": self.cfg.model,
                "max_concurrency": self.cfg.max_concurrency}

    async def chat(self, messages: list[dict[str, str]], *, max_tokens: int, timeout: float) -> str:
        body = {"model": self.cfg.model, "messages": messages, "temperature": 0.0, "top_p": 1.0,
                "max_tokens": max_tokens, "stream": False, "cache_prompt": True}
        try:
            r = await self.client.post("/chat/completions", json=body, timeout=timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise BackendUnavailable(f"LLM server unreachable: {e}") from e
        if r.status_code >= 500:
            raise BackendUnavailable(f"LLM server error {r.status_code}")
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"] or ""
        return _THINK.sub("", content).strip()

    async def healthy(self) -> bool:
        root = self.cfg.base_url.rstrip("/")
        root = root[:-3] if root.endswith("/v1") else root
        try:
            r = await self.client.get(root + "/health", timeout=2.0)
            if r.status_code == 404:
                r = await self.client.get("/models", timeout=2.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def close(self) -> None:
        await self.client.aclose()
