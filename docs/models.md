# Model recommendations

Measured numbers below come from [benchmarks.md](benchmarks.md); anything not measured is
marked **estimate**. All recommended models allow commercial use (see [licences.md](licences.md)).

## Summary

| Task | Recommended now | Why | Planned upgrade path |
|---|---|---|---|
| Speech recognition (en, ar, sw) | **Whisper large-v3-turbo**, CTranslate2 int8 | Best accuracy per CPU-second of the commercially usable multilingual models; MIT licence | Whisper large-v3 for accuracy if CPU allows; GPU for latency |
| Speech recognition (Somali) | **A Somali-capable model routed by language** — candidate: Meta **Omnilingual ASR** (Apache-2.0) | Whisper's Somali accuracy is not usable (measured, below) | Fine-tune on the client's own Somali recordings |
| Live preview | Whisper small, int8 | ~10× cheaper than turbo; only shown, never inserted | — |
| Cleanup | **Qwen3-4B-Instruct-2507**, GGUF Q4_K_M on llama.cpp — **English**; rule-based cleanup for Somali, Arabic, Swahili | Measured: the 4B model improves English cleanup but not the other three languages | Qwen3-8B or larger on a GPU, re-measured per language |
| Translation | **Qwen3-4B (same model)** for Arabic↔English; **MADLAD-400 3B** to be evaluated for Somali and Swahili | Measured: LLM Arabic→English is good (chrF 65), Swahili/Somali→English weak (34 / 24) | Choose per direction from measured chrF |
| Safety net | Rule-based cleanup + meaning-preservation guard | Guarantees names, numbers, dates, negations and terms survive; falls back instead of guessing | — |

## Speech recognition

### Whisper large-v3-turbo (default)

* **Languages:** trained on 99 languages including English, Arabic, Swahili and Somali.
  Accuracy follows the amount of training data: very good for English, usable for Arabic
  and Swahili, poor for Somali (measured).
* **Accents and noise:** trained on 5M hours of weakly labelled web audio, so it copes well
  with accents and moderate background noise. Voice-activity detection (Silero) trims
  silence before recognition, which also prevents Whisper's known habit of inventing text on
  silence; known hallucination phrases are additionally filtered.
* **Specialist terminology:** dictionary terms are passed as recognition hints, and
  "sounds like" aliases are replaced after recognition. The cleanup step is told to keep
  them and the guard checks it did.
* **Incremental transcription:** Whisper is not a streaming model. Sauti streams audio
  while the user speaks, shows a live preview from a small model, and runs the accurate
  model once on release. Interactive latency is therefore "time after release", which is
  what the benchmarks report.
* **Cost on CPU:** Whisper always encodes a 30-second window, so a 3-second phrase costs
  almost as much as a 25-second one. This is the main reason a GPU is the most effective
  upgrade (below).
* **Licence:** MIT.

### Alternatives considered

| Model | Licence | Verdict |
|---|---|---|
| Whisper large-v3 | MIT | ~8× more decoder work than turbo; better on some low-resource languages. Configurable; benchmarked where time allowed. |
| Meta Omnilingual ASR (CTC 300M–7B, LLM-decoder variants), Nov 2025 | Apache-2.0 | Covers 1,600+ languages including Somali. CTC variants are non-autoregressive and fast on CPU, but output plain lower-case text (the cleanup step restores punctuation). Recommended candidate for Somali; evaluate in milestone 1 on the client's own recordings. |
| Meta MMS-1B-all | CC-BY-NC-4.0 | Good Somali coverage but **non-commercial** — rejected. |
| SeamlessM4T v2 | CC-BY-NC-4.0 | **Non-commercial** — rejected. |
| NVIDIA Parakeet / Canary | CC-BY-4.0 | Excellent speed, but no Somali, Swahili or Arabic. An English-only route is possible as an optimisation. |
| Community Whisper fine-tunes for Somali | varies | Quality and licence vary model by model; can be plugged in via `asr.per_language` after testing. |

**How Somali is handled.** The server routes each language to its own model
(`asr.per_language = { so = "/opt/sauti/models/<somali-model>" }`). Language detection is
restricted to the four configured languages, so a Somali speaker is not mis-detected as an
unrelated language. Until a Somali model is validated, Somali dictation works but should be
treated as low accuracy.

## Cleanup LLM

**Qwen3-4B-Instruct-2507** (Apache-2.0), 4-bit GGUF, served by llama.cpp:

* Follows the "fix, don't rewrite" instruction reliably in tests, including refusing to
  follow instructions that are part of the dictation (see benchmarks).
* Multilingual, but measured cleanup quality differs by language: for English the LLM pass
  cuts residual errors from 2.7% to 1.2%; for Somali and Swahili it is slightly worse than
  the rules, and for Arabic it rewrote words often enough that the guard rejected 40% of its
  outputs. `llm.cleanup_languages` therefore defaults to `["en"]`.
* Runs in ~3 GB of RAM. The system prompt and examples are fixed, so llama.cpp keeps them in
  its prompt cache and only the new transcript is processed per request.
* **Guard:** every LLM output is compared with the input. If a number, negation, name or
  dictionary term changed, if words were added or the text shrank (a summary), or if new
  names appeared (an answer), the output is discarded and the rule-based cleanup is used.
  The fallback costs polish, never meaning.

Alternatives: Gemma 3 4B (Gemma Terms of Use — commercial use allowed with a prohibited-use
policy), Llama 3.2 3B (Llama licence with an acceptable-use policy), Phi-4-mini (MIT, weaker
multilingual), Qwen3-1.7B (faster, noticeably weaker). Any of them can be swapped in by
changing the GGUF file in `sauti-llm.service`.

## Translation

* **Default: the cleanup LLM** — no extra memory, handles conversational text, keeps names
  and numbers when told to (and the number check verifies it). Measured chrF: Arabic→English
  65, Swahili→English 34, Somali→English 24 (see benchmarks).
* **Invented translations.** When recognition is poor, an LLM translates the garbled words
  into fluent but invented sentences — a direct conflict with "never invent information". The
  server therefore withholds translation when the recogniser's confidence is below
  `asr.low_confidence` (default 0.5) and returns the transcript with a `not_translated`
  notice. Measured examples: garbled Swahili scored 0.46 and garbage Somali 0.32; clean Arabic
  and English scored 0.90–0.98.
* **MADLAD-400 3B MT** (Apache-2.0, CTranslate2 int8, ~3 GB) — a dedicated translation model
  covering 400+ languages including Somali and Swahili. Selected per deployment with
  `translate.backend = "ct2"`.
* NLLB-200 is supported by the code but is **CC-BY-NC (non-commercial)**, so it is not
  recommended for this deployment.

## Hardware

**Measured on a development workstation (not the client's server)** — see
[benchmarks.md](benchmarks.md) for conditions. The client's Ryzen 5 9600X has six Zen 5
cores with AVX-512, which CTranslate2 and llama.cpp both use.

**Recommendation:** start CPU-only in a dedicated VM (8 vCPU, `cpu: host`, higher
`cpuunits`, 24 GB RAM). Run the included benchmark on the real host in milestone 1 with the
other guests running. Add a GPU only if the measured final-text p95 at five concurrent users
is above the agreed target.

**If a GPU is justified (estimate):** a 16–24 GB NVIDIA card (for example RTX 4000 SFF Ada
20 GB — low profile, 70 W — or RTX 4060 Ti 16 GB / used RTX 3090 if the case and power supply
allow) runs Whisper large-v3 (not just turbo) and an 8B cleanup model with sub-second final
latency for five users. Check the server has a free PCIe x16 slot, airflow and PSU headroom.

## How models are benchmarked on the client's hardware

1. `bench/fetch_fleurs.py` downloads the public FLEURS test audio for all four languages.
2. `bench/asr_accuracy.py` — word/character error rate and language-ID accuracy per
   language, per model.
3. `bench/mixed_language.py` — two languages in one dictation.
4. `bench/cleanup_eval.py` — disfluency removal without word changes, guard fallback rate,
   prompt-injection resistance.
5. `bench/translate_eval.py` — chrF per direction for each translation backend.
6. `bench/load_test.py` — 1, 3 and 5 concurrent simulated users streaming real speech at
   real-time pace while the other Proxmox guests run; reports time to first text, time to
   final text, queue delay, accuracy and CPU/memory.
7. `bench/report.py` renders the tables in [benchmarks.md](benchmarks.md).

In milestone 1 the same scripts also run on 30–60 minutes of the client's own recordings
(with consent) in each language, because public read speech is cleaner than real dictation.
