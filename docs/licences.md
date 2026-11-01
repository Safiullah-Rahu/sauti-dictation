# Licences

## This project

Sauti's own source code is released under the Apache License 2.0 (see [LICENSE](../LICENSE)).

## Models

| Model | Used for | Licence | Commercial use |
|---|---|---|---|
| Whisper large-v3-turbo / large-v3 / small (OpenAI) | Speech recognition | MIT | Yes |
| Qwen3-4B-Instruct-2507 (Alibaba Qwen) | Cleanup, LLM translation | Apache-2.0 | Yes |
| MADLAD-400 3B MT (Google) | Optional dedicated translation | Apache-2.0 | Yes |
| Omnilingual ASR (Meta FAIR) | Candidate Somali recogniser | Apache-2.0 | Yes |
| Silero VAD (bundled with faster-whisper) | Silence trimming | MIT | Yes |
| NLLB-200 (Meta) | Supported by the code, **not recommended** | CC-BY-NC-4.0 | **No** |
| MMS (Meta), SeamlessM4T (Meta) | Considered, rejected | CC-BY-NC-4.0 | **No** |

The GGUF and CTranslate2 conversions downloaded by `scripts/download_models.py` are
redistributions of the models above under the same licences.

## Software dependencies

| Component | Licence |
|---|---|
| faster-whisper, CTranslate2 | MIT |
| llama.cpp | MIT |
| FastAPI, Starlette, uvicorn, httpx, websockets | MIT / BSD-3-Clause |
| argon2-cffi, cryptography | MIT / Apache-2.0 or BSD |
| psutil, NumPy | BSD-3-Clause |
| PySide6 (Qt for Python) | LGPL-3.0 (used unmodified via dynamic linking; the desktop build ships Qt as separate libraries) |
| pynput | LGPL-3.0 (unmodified, dynamically imported) |
| sounddevice / PortAudio | MIT |
| keyring | MIT |
| Caddy | Apache-2.0 |
| OkHttp, kotlinx.serialization, AndroidX | Apache-2.0 |

## Benchmark data

FLEURS (Google), CC-BY-4.0 — used for accuracy, mixed-language and translation tests. Audio
is downloaded by `bench/fetch_fleurs.py` and not redistributed in this repository.
