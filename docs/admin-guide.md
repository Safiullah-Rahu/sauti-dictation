# Administrator guide

Open `https://<hostname>/admin` and sign in with an administrator account.

The dashboard manages accounts and capacity. It cannot show what anyone dictated:
transcripts and personal dictionaries are encrypted with each user's own key, and no admin
endpoint returns them.

## Overview

* **Status** — healthy, or degraded when the cleanup model is unreachable (dictation keeps
  working with rule-based cleanup).
* **Live sessions** — connected devices and how many are recording or processing right now.
* **Today** — audio minutes, dictations and errors (UTC day).
* **Queues** — for each stage (`asr`, `llm`, `translate`): busy workers, jobs waiting per
  class (final / preview / long recording), p50/p95 wait and run time over the last five
  minutes, and counters for completed, failed, timed-out and rejected ("busy") jobs.
* **Server** — CPU, memory, disk, and the memory of the Sauti and LLM processes.
* **Models** — which recogniser, cleanup model and translator are configured.

Signs that the VM needs more CPU (or a GPU): final-class wait p95 above ~1 s, regular "busy"
counts, or timeouts.

## Users

* **Add a user** — username, display name, initial password, role, optional daily limit
  (minutes of audio). Users can change their password in the portal.
* **Daily limit** — leave empty for the server default (`limits.daily_audio_minutes`).
* **Disable** — signs the user out everywhere and blocks sign-in; data is kept.
* **Reset password** — the user gets a new encryption key. Their saved history and personal
  dictionary are deleted and every device is signed out. This is deliberate: it is what keeps
  personal data unreadable to administrators.
* **Make admin / Make user** — at least one active administrator must remain.
* **Delete** — removes the account and all of its data.

The History column shows only whether history is on and how many items are stored.

## Devices and sessions

* **Devices** — every signed-in desktop or paired phone, with last-seen time. **Revoke**
  signs it out immediately.
* **Sessions** — live connections with state (idle / recording / processing). **End** closes
  one connection; the device reconnects unless it was also revoked.

## Usage

Per user per day: audio minutes, dictations and errors, for the last 14 days.

## Organisation dictionary

Shared spellings for names, places, products and technical terms. Each entry has the
canonical spelling and optional "sounds like" variants (how the recogniser tends to mishear
it). Entries are offered to the recogniser as hints, replace their variants after
recognition, and are kept exactly by the cleanup step. Users' own dictionaries take
precedence over the shared one.

## Events

Failures and safety events: code (`timeout`, `busy`, `unavailable`, `cleanup_fallback`,
`login_failed`, ...), the stage that failed, the account and time. For `cleanup_fallback` the
detail lists which check failed (for example `negation_changed`) — never the text itself.

The audit log records administrator actions (user created, password reset, device revoked).

## Command line (on the VM)

```bash
export SAUTI_CONFIG=/etc/sauti/sauti.toml
sudo -u sauti -E /opt/sauti/venv/bin/sauti-server create-user --admin bob
sudo -u sauti -E /opt/sauti/venv/bin/sauti-server reset-password alice
sudo -u sauti -E /opt/sauti/venv/bin/sauti-server backup --dest /var/backups/sauti
```
