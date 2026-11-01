<p align="center"><img src="desktop/assets/sauti.png" width="88" alt=""></p>

<h1 align="center">Sauti — private AI dictation on your own server</h1>

<p align="center">
Speak into any app on Windows, macOS or Android and get polished text back in about a second or two.<br>
Speech recognition, cleanup and translation run on <b>your</b> hardware: no cloud AI, no per-minute fees.
</p>

<p align="center">
<a href="#demo">Demo</a> · <a href="docs/architecture.md">Architecture</a> · <a href="docs/models.md">Models</a> ·
<a href="docs/benchmarks.md">Benchmarks</a> · <a href="docs/deployment.md">Deploy</a> · <a href="docs/privacy-security.md">Privacy</a>
</p>

---

Sauti ("voice" in Swahili) is a self-hosted alternative to tools like Wispr Flow, built for an
organisation that dictates in **English, Somali, Arabic and Swahili** and must keep every word
on its own Proxmox server.

* **System-wide dictation.** A tray / menu-bar app for Windows and macOS (hold a key, speak,
  release — the text appears where your cursor is) and an Android voice keyboard that works in
  WhatsApp, Gmail and any other app.
* **Three modes.** Verbatim transcription; clean dictation (hesitations, repetitions and
  punctuation fixed, meaning preserved); and translation into a chosen language. Automatic or
  manual language selection.
* **Meaning-preservation guard.** The cleanup LLM's output is checked against what was said.
  If a name, number, date, negation or dictionary term changed — or the text was summarised,
  answered or padded — Sauti discards it and returns a safe rule-based cleanup instead.
* **Built for a shared CPU server.** A fair scheduler with priorities, bounded queues,
  deadlines and cancellation keeps dictation responsive for several people at once, and
  degrades gracefully (live previews go first) when the machine is busy.
* **No lost or duplicated text.** Audio is kept on the device until the result arrives;
  dropped connections resume automatically, and every dictation is inserted exactly once.
* **Multi-user with real privacy.** Accounts, multiple devices per user, synced settings and
  personal dictionaries, an organisation dictionary, optional transcript history with
  deletion controls. Personal data is encrypted with each user's own key — administrators
  manage accounts and capacity but cannot read what anyone dictated.
* **Admin dashboard.** Users, devices, live sessions, usage limits, queue and server health,
  failure log, audit log.
* **Replaceable models.** Recogniser, cleanup model and translator are configuration on the
  server. Upgrading a model never requires a new client release.

## Demo

> The demo video is in [`docs/media/sauti-demo.mp4`](docs/media/sauti-demo.mp4). It shows the real
> Windows tray app dictating into an editor through the real server and models on a
> CPU-only workstation; speech is played into the app's microphone path from audio files
> (marked "TEST AUDIO" in the status pill) so the recording is reproducible.

## How it fits together

```mermaid
flowchart LR
  W["Windows tray app"] --> C
  M["macOS menu-bar app"] --> C
  A["Android voice keyboard"] --> C
  B["Browser: portal + admin"] --> C
  subgraph VM["Linux VM on the Proxmox host"]
    C["Caddy · TLS"] --> S["Sauti server<br>FastAPI · fair scheduler"]
    S --> R["Whisper large-v3-turbo<br>(CTranslate2, int8)"]
    S --> L["Qwen3-4B-Instruct<br>(llama.cpp)"]
    S --> D[("SQLite<br>per-user encryption")]
  end
```

Details: [docs/architecture.md](docs/architecture.md) · wire protocol: [docs/protocol.md](docs/protocol.md)

## Results at a glance

<!-- RESULTS-START -->
Benchmarks are being finalised; see [docs/benchmarks.md](docs/benchmarks.md).
<!-- RESULTS-END -->

## Repository layout

| Path | What |
|---|---|
| `server/` | Backend: REST + WebSocket API, scheduler, pipeline, model backends, admin dashboard and user portal (`sauti_server/web`). 59 tests. |
| `desktop/` | Windows / macOS tray app (PySide6). Network-client and Windows end-to-end tests. |
| `android/` | Android voice keyboard (Kotlin). Pure-Kotlin `core` protocol module with 48 tests; `app` module with the IME. |
| `bench/` | Accuracy, cleanup, translation, mixed-language and concurrent-user benchmarks. |
| `deploy/` | Proxmox VM script, installer, systemd units, Caddy config, backup and update scripts. |
| `docs/` | Architecture, protocol, models, benchmarks, deployment, user / admin guides, privacy, licences. |

## Quick start (development)

```bash
# Server with fake models (no downloads): http://127.0.0.1:8700/admin
cd server && pip install -e ".[dev]" && pytest -q
python ../scripts/dev_server.py deploy/dev-fake.toml      # from the repo root
python -c "import sys; sys.path.insert(0,'server'); from sauti_server.cli import main; main(['--config','deploy/dev-fake.toml','create-user','--admin','admin'])"

# Real models on a workstation
python scripts/download_models.py --dest ../models
llama-server -m ../models/gguf/Qwen3-4B-Instruct-2507-Q4_K_M.gguf --port 8081 -c 8192 --parallel 2
python scripts/dev_server.py deploy/dev.toml

# Desktop app from source
pip install -r desktop/requirements.txt && cd desktop && python -m sauti_desktop
```

Production install on Proxmox: [docs/deployment.md](docs/deployment.md).

## Builds

GitHub Actions builds the Windows app, the macOS app (Apple Silicon and Intel) and the
Android APK on every push (`.github/workflows/`). Tagged versions (`v*`) collect them into a
draft release.

## Status and known limitations

This repository is a working proof of concept (milestone 1 of the proposed plan):

* **Somali accuracy is the main open item.** Whisper's Somali output is not usable
  (measured word error rate around 95%) and Whisper's language detection does not recognise
  Somali. The server already routes a language to its own model; a Somali-capable recogniser
  (candidate: Meta Omnilingual ASR, Apache-2.0) must be validated on the client's recordings.
* Benchmarks here were measured on a development workstation, not the client's Ryzen 5
  9600X; the same scripts are meant to be re-run on the Proxmox host.
* The macOS app is built by CI but has not been tested on a Mac in this milestone; it needs
  code signing/notarisation for smooth installation.
* The Android APK is built by CI; it has not yet been tested on physical devices.
* Mixed-language speech within a single sentence is only partly handled by Whisper.

## Licence

Apache-2.0 for this code. Model and dependency licences: [docs/licences.md](docs/licences.md).
