# Privacy and security

## What goes where

| Data | Where it exists | How long |
|---|---|---|
| Raw audio | Device memory while recording; server memory while being recognised | Discarded when recognition finishes. Never written to disk by default. |
| Final text (not acknowledged yet) | Server memory | Until the device acknowledges it, at most 10 minutes (`privacy.result_cache_s`) |
| Transcript history | Server database, encrypted per user | Only if the user turns history on; deleted after the user's retention period, or immediately on request |
| Personal dictionary | Server database, encrypted per user | Until the user removes entries |
| Organisation dictionary | Server database, plain | Managed by administrators |
| Accounts, devices, usage counts, error codes | Server database, plain | Usage and events 30–90 days; accounts until deleted |
| Logs | Server journal and Caddy access log | Request lines and error codes only — no dictated text |

No component sends content to any third party. The pages served to browsers load no
external scripts, fonts or analytics, and the Content-Security-Policy forbids them.

## Encryption

* **In transit:** TLS between every device and the server (Caddy). Device tokens travel in
  HTTP headers or inside the WebSocket, never in URLs, so they do not appear in access logs.
* **At rest, per user:** each user has a random 256-bit data key. History and the personal
  dictionary are encrypted with it (AES-256-GCM). The key is stored only in wrapped form:
  once under a key derived from the user's password (scrypt) and once per device or browser
  session under a key derived from that token. The database stores only SHA-256 hashes of
  tokens.

**What this means for administrators.** An administrator — or anyone holding a copy of the
database — cannot read history or personal dictionaries, and the admin API has no endpoint
that returns them. When an administrator resets a password, the user starts with a new data
key: their old history and dictionary become unreadable and are deleted, and all their
devices are signed out.

**Honest limit.** While a user is connected, their data key is in server memory, because the
server must process their speech. Someone with root access to the running VM could in
principle instrument the process. The design keeps content away from routine
administration, backups and database copies; it is not end-to-end encryption, which is not
possible when the server itself must hear the audio.

## Authentication

* Passwords are hashed with Argon2id.
* Desktop apps sign in once with username + password and receive a device token stored in
  the OS keychain (Windows Credential Manager, macOS Keychain).
* Phones pair with a 6-digit code created in the user portal: single use, valid 10 minutes,
  held only in server memory, attempts rate-limited per address.
* Devices can be revoked by the user or an administrator; open connections close at once.
* Login attempts are rate-limited per address and username.

## Network exposure

Only Caddy listens on the network (443, and 80 for redirects / certificate issuance). The
Sauti server listens on 127.0.0.1:8700 and the LLM server on 127.0.0.1:8081.

**Remote access, recommended:** a VPN into the office network (WireGuard on the Proxmox host
or router, or a self-hosted Headscale server). Phones and laptops then reach the LAN address
and nothing is exposed to the internet.

**Remote access, alternative:** a public DNS name pointing at the VM with ports 80/443
forwarded; Caddy obtains a Let's Encrypt certificate. Add the firewall rules in
[deployment.md](deployment.md) and keep login rate limits enabled.

## Visible recording

* Desktop: the status pill shows a pulsing red dot and a timer while the microphone is open,
  and the tray icon changes. Windows and macOS also show their own microphone indicators,
  because the microphone stream is opened only while recording.
* Android: the keyboard shows a pulsing indicator and "Listening"; Android 12+ also shows its
  green privacy dot.

## External dependencies and recurring costs

| Item | Needed? | Cost |
|---|---|---|
| Hugging Face (model download) | Once at install / upgrade | Free |
| GitHub (llama.cpp release download, source code) | Once at install / upgrade | Free |
| Let's Encrypt | Only for a public hostname | Free |
| Domain name | Only for a public hostname | ~USD 10–20 per year |
| Tailscale (hosted coordination) | Optional alternative to self-hosted WireGuard/Headscale; sees device metadata, not traffic | Free tier for small teams |
| Apple Developer ID (code signing / notarisation of the macOS app) | Recommended for smooth installation | USD 99 per year |
| Windows code-signing certificate | Optional; avoids SmartScreen warnings | ~USD 100–400 per year |
| Google Play developer account | Optional; the APK can be sideloaded instead | USD 25 once |

There are no per-minute or per-user fees: nothing is processed by a cloud AI service.
