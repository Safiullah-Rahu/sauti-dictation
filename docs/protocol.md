# Sauti client protocol (v1)

Every client (Windows/macOS tray app, Android keyboard, web test page) talks to the
server through the same two surfaces:

* a small **REST API** (JSON over HTTPS) for sign-in, pairing, settings, dictionary and history;
* one **WebSocket** per device (`/v1/dictate`) that carries audio up and text down.

Model choice lives entirely on the server. Clients send 16 kHz PCM and receive text, so
swapping Whisper for another recogniser, or the cleanup LLM for another model, never
requires a client release.

All examples use `https://dictate.example.lan`. In development the server listens on
`http://127.0.0.1:8700` (Android emulator: `http://10.0.2.2:8700`).

---

## 1. Authentication model

| Credential | Who gets it | Lifetime | Used for |
|---|---|---|---|
| **Web session token** (`sws_…`) | Browser sign-in (user portal, admin dashboard) | 8 h | REST, admin REST |
| **Device token** (`sdt_…`) | Desktop sign-in or phone pairing | until revoked | REST + WebSocket |
| **Pairing code** (6 digits) | Created in the user portal by a signed-in user | 10 min, single use | Exchanged once for a device token |

Tokens are sent as `Authorization: Bearer <token>` on REST calls. On the WebSocket the
token is sent in the first message (not in the URL, so it never lands in access logs).

The server stores only a SHA-256 hash of each token. Each token also unlocks that
user's data key, which encrypts their dictionary and transcript history at rest, so
the database on its own (or an administrator reading it) cannot reveal that content.

### Errors

REST errors use HTTP status codes with a JSON body:

```json
{"error": {"code": "invalid_credentials", "message": "Wrong username or password."}}
```

---

## 2. REST endpoints

### 2.1 Desktop sign-in (creates a device)

`POST /v1/devices/register`

```json
{"username": "amina", "password": "…", "device_name": "Amina's laptop", "platform": "windows"}
```

`201 Created`

```json
{"device_id": 12, "device_token": "sdt_…", "user": {"id": 3, "username": "amina", "display_name": "Amina Yusuf", "role": "user"}}
```

`platform` is one of `windows`, `macos`, `android`, `web`, `other`.

### 2.2 Browser sign-in

`POST /v1/auth/login` with `{"username": "…", "password": "…"}` →
`200 {"token": "sws_…", "expires_at": 1767225600.0, "user": {…}}`

`POST /v1/auth/logout` → `204`

### 2.3 Phone pairing

1. The user signs in to the portal and presses **Pair a phone**. The portal calls
   `POST /v1/pairing/codes` (web token) → `201 {"code": "483920", "expires_at": …, "server_url": "https://dictate.example.lan"}`.
2. The phone app asks for the server URL and the code, then calls
   `POST /v1/pairing/claim` (no auth):

```json
{"code": "483920", "device_name": "Pixel 8", "platform": "android"}
```

`201` → same body as `/v1/devices/register`. Errors: `400 invalid_code` (wrong or
expired), `429 rate_limited` (too many attempts from this address).

### 2.4 Profile (settings + dictionaries)

`GET /v1/me/profile` (device or web token)

```json
{
  "version": 7,
  "user": {"id": 3, "username": "amina", "display_name": "Amina Yusuf", "role": "user"},
  "settings": {
    "input_lang": "auto",          // auto | en | so | ar | sw
    "output_lang": "en",           // target for translate mode
    "mode": "clean",               // verbatim | clean | translate
    "style": "neutral",            // neutral | formal | casual | message
    "history_enabled": false,
    "retention_days": 30
  },
  "dictionary": [{"id": 5, "term": "Hargeisa", "aliases": ["hargaysa"]}],
  "org_dictionary": [{"id": 1, "term": "Proxmox", "aliases": []}],
  "languages": ["en", "so", "ar", "sw"],
  "modes": ["verbatim", "clean", "translate"],
  "styles": ["neutral", "formal", "casual", "message"]
}
```

`PATCH /v1/me/settings` with any subset of `settings` → returns the full profile.
`POST /v1/me/dictionary` `{"term": "…", "aliases": ["…"]}` → `201` entry.
`DELETE /v1/me/dictionary/{id}` → `204`.

`version` increases on every change. When it changes, the server pushes
`{"type": "profile_changed", "version": 8}` to the user's other connected devices,
which then re-fetch the profile.

### 2.5 History (only stored when `history_enabled` is true)

`GET /v1/me/history?limit=50&before=<id>` → `{"items": [{"id": 91, "created_at": …, "mode": "clean", "lang": "so", "target_lang": null, "text": "…"}]}`
`DELETE /v1/me/history/{id}` → `204`; `DELETE /v1/me/history` → `204` (deletes everything).

Entries expire automatically after `retention_days`.

### 2.6 Devices

`GET /v1/me/devices` → `{"items": [{"id": 12, "name": "Pixel 8", "platform": "android", "created_at": …, "last_seen": …, "current": true}]}`
`DELETE /v1/me/devices/{id}` → `204` (revokes the token; an open WebSocket is closed).

### 2.7 Health

`GET /v1/health` (no auth) → `{"status": "ok", "version": "0.1.0"}`; `status` is
`degraded` when a model backend is down.

---

## 3. Dictation WebSocket: `/v1/dictate`

### 3.1 Handshake

Client → `{"type": "hello", "token": "sdt_…", "client": "android/0.1.0", "protocol": 1}`

Server → 

```json
{"type": "ready", "session_id": "c0ffee…", "user": {…}, "profile_version": 7,
 "limits": {"max_utterance_seconds": 120, "sample_rate": 16000}}
```

If the token is invalid: `{"type": "error", "code": "unauthorized", "retryable": false}` then
close code `4401`. The hello must arrive within 10 s.

### 3.2 One utterance

```
client                                   server
  | {"type":"start","utterance_id":U,…}   |
  | <binary PCM> <binary PCM> …           |   ← {"type":"partial",…}   (preview only)
  | {"type":"end","utterance_id":U}       |
  |                                       |   ← {"type":"status","state":"transcribing"}
  |                                       |   ← {"type":"final","utterance_id":U,"text":"…"}
  | {"type":"ack","utterance_id":U}       |
```

**start**

```json
{"type": "start", "utterance_id": "6f1c4c1e-…", "mode": "clean", "lang": "auto",
 "target_lang": "en", "sample_rate": 16000, "format": "pcm_s16le"}
```

* `utterance_id` — a fresh UUID v4 generated by the client. It is the idempotency key.
* `mode`, `lang`, `target_lang` — optional; missing fields use the profile settings.
* Only `pcm_s16le`, mono, 16000 Hz is accepted in v1.

**audio** — binary frames of little-endian signed 16-bit mono samples. Any frame size
works; 20–100 ms (640–3200 bytes) is recommended. Audio belongs to the most recent
`start`. Starting a new utterance implicitly ends the previous one.

**end** — `{"type": "end", "utterance_id": "…"}`

**cancel** — `{"type": "cancel", "utterance_id": "…"}` → server answers
`{"type": "error", "utterance_id": "…", "code": "cancelled", "retryable": false}` and drops
all queued work for it.

### 3.3 Server messages

| type | fields | meaning |
|---|---|---|
| `partial` | `utterance_id`, `text`, `seq` | Live preview while the user is still speaking. **Never insert it.** May stop arriving under load. |
| `status` | `utterance_id`, `state` (`queued`/`transcribing`/`cleaning`/`translating`), `position` (when queued) | Progress for the UI. |
| `final` | `utterance_id`, `text`, `raw`, `lang`, `lang_prob`, `confidence`, `mode`, `target_lang`, `replayed`, `timings_ms`, `notices`, `source_text` (translate mode) | The text to insert. `text` may be `""` (no speech) — insert nothing. `confidence` (0–1) is the recogniser's confidence in the words. |
| `error` | `utterance_id` (optional), `code`, `message`, `retryable`, `retry_after_ms` | See codes below. |
| `resume_result` | `utterance_id`, `state` (`processing`/`unknown`) | Answer to `resume`. |
| `profile_changed` | `version` | Re-fetch `GET /v1/me/profile`. |
| `pong` | `t` | Answer to `ping`. |

`timings_ms` — `{"queue": 12, "asr": 820, "cleanup": 640, "translate": 0, "total": 1490}`,
measured on the server from the `end` message to the `final` message.

`notices` — machine-readable remarks the UI may show:
`no_speech`, `cleanup_fallback` (LLM output failed the meaning-preservation check, so the
rule-based cleanup was used), `cleanup_unavailable`, `translation_unverified`,
`truncated` (audio exceeded the length limit), `lang_restricted` (detected language was
outside the allowed set and the best allowed language was used), `low_confidence` (the
recogniser was unsure; show the text as "please check"), `not_translated` (translate mode, but
recognition confidence was too low to translate safely — `text` is the transcript in the spoken
language, so nothing is invented).

**Error codes**

| code | retryable | client behaviour |
|---|---|---|
| `busy` | yes | Queue full. Retry after `retry_after_ms` with the **same** `utterance_id`. |
| `timeout` | yes | Processing deadline passed. Retry once with the same id. |
| `quota_exceeded` | no | Daily audio limit reached. |
| `too_long` | no | Utterance longer than `max_utterance_seconds`. |
| `bad_request` | no | Malformed message. |
| `cancelled` | no | Confirmation of `cancel`. |
| `unavailable` | yes | A model backend is down. |
| `internal` | yes | Unexpected server error. |
| `unauthorized` | no | Token revoked or invalid — sign in / pair again. |

### 3.4 Reliability rules (no lost or duplicated text)

1. **Keep the audio until the final arrives.** The client holds each utterance's PCM in
   memory until it receives its `final` (or a non-retryable error), then discards it.
   Nothing is written to disk.
2. **Reconnect and resume.** If the socket drops, reconnect with backoff
   (0.5 s, 1 s, 2 s, 4 s, then every 8 s), send `hello`, and for every pending utterance send
   `{"type": "resume", "utterance_id": "…"}`:
   * the server replays the stored `final` with `"replayed": true`, or
   * answers `resume_result` `processing` (the final will follow), or
   * answers `resume_result` `unknown` → resend `start` + all audio + `end` with the same id.
3. **Insert at most once.** The client keeps a ledger of inserted `utterance_id`s (the last
   500, persisted) and ignores any `final` whose id is already in it. A replayed or
   duplicated final can therefore never insert text twice.
4. **Acknowledge.** After inserting (or deciding not to), send `ack`. The server keeps an
   un-acknowledged final in memory for 10 minutes, then drops it.
5. **Give up visibly.** If an utterance cannot be delivered within 2 minutes, tell the user
   ("Dictation could not be processed — server unavailable") and discard the audio.
6. **Duplicate starts are safe.** A `start` with an id that is already finished replays the
   final; one that is still processing attaches to the running job; audio sent for it is
   ignored.

### 3.5 Keep-alive

The server sends WebSocket pings every 20 s. Clients may also send
`{"type": "ping", "t": 123}`; the server answers `{"type": "pong", "t": 123}`. A client that
has heard nothing for 45 s should treat the connection as dead and reconnect.

---

## 4. Admin API (web token, `role = admin`)

Under `/v1/admin/…`: `overview`, `users`, `users/{id}`, `devices`, `sessions`, `usage`,
`org-dictionary`, `events`, `audit`. None of them return transcript text, dictionary
entries of individual users, or audio; see [admin-guide.md](admin-guide.md).
