# Sauti on Android

The Android app is a voice keyboard. It shows up in the system keyboard list as
**Sauti voice keyboard**. When you speak, the audio goes to your organisation's Sauti server.
The server turns it into text (and cleans it up or translates it), and the keyboard puts the
finished text into whatever field you are typing in. Speech recognition does not happen on the
phone.

- Package: `com.sauti.keyboard`
- Android 8.0 (API 26) or newer
- Source: `android/` (Gradle, Kotlin). The wire protocol is in [protocol.md](protocol.md).

The project has two modules:

| Module | What it is |
|---|---|
| `android/core` | Plain Kotlin/JVM library with no Android dependencies. It holds the protocol models, the REST client, the WebSocket dictation client (reconnect, resume, retries, give-up) and the insertion ledger. It builds and tests without an Android SDK. |
| `android/app` | The Android application: the keyboard (`SautiImeService`), the setup screen (`MainActivity`), microphone capture and secure token storage. |

`settings.gradle.kts` only includes `:app` when an Android SDK is available, meaning `sdk.dir`
in `local.properties`, or `ANDROID_HOME` / `ANDROID_SDK_ROOT` pointing at a directory. Without
an SDK, only `:core` is built.

---

## 1. Building

### 1.1 GitHub Actions (the normal way)

The workflow `.github/workflows/android.yml` runs when a push or pull request changes
`android/**` or the workflow file. You can also start it by hand from the **Actions** tab
(**Android** → **Run workflow**).

It runs on `ubuntu-latest` with Temurin JDK 17 and does:

```
./gradlew :core:test :app:assembleDebug :app:lintDebug
```

Lint findings do not fail the build (`abortOnError = false`). The runner image already has an
Android SDK, so nothing is installed by the workflow itself.

To get the APK, open the finished run and download the artifact
**sauti-keyboard-debug-apk**. It contains `app-debug.apk`. A second artifact,
**sauti-android-reports**, has the unit-test and lint reports.

### 1.2 Locally with Android Studio

1. Install Android Studio (Ladybug 2024.2 or newer; the project uses Android Gradle Plugin
   8.7.3, Gradle 8.10.2 and Kotlin 2.0.21).
2. **File → Open** and choose the `android/` folder (not the repository root).
3. Android Studio writes `android/local.properties` with `sdk.dir`, which makes Gradle include
   the app module. Let it sync.
4. Run the `app` configuration on a device or emulator, or build from a terminal:

```
cd android
./gradlew :app:assembleDebug        # Windows: gradlew.bat :app:assembleDebug
```

The APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`.

### 1.3 The core library only (no Android SDK)

You need only a JDK 17:

```
cd android
./gradlew :core:test
```

There is an end-to-end test against a real server. It is skipped unless these variables are
set:

| Variable | Example | Meaning |
|---|---|---|
| `SAUTI_TEST_SERVER` | `http://127.0.0.1:8700` | Server root URL |
| `SAUTI_TEST_TOKEN` | `sdt_…` | A device token (pair a device, or register one with `POST /v1/devices/register`) |
| `SAUTI_TEST_WAV` | `/path/to/speech.wav` | 16 kHz, mono, 16-bit PCM WAV with speech in it |
| `SAUTI_TEST_REALTIME` | `1` | Optional: send the audio at microphone pace instead of all at once |

```
./gradlew :core:test --tests com.sauti.core.IntegrationTest
```

The test checks `GET /v1/health`, connects, streams the WAV in 100 ms frames, and expects
exactly one non-empty final.

### 1.4 Release builds

A release build type exists, but no signing is configured. Release builds only allow HTTPS
(see section 6). To publish a release, create a keystore and add a `signingConfig`, keeping the
keystore and its passwords out of the repository (for example in GitHub Actions secrets).
Minification is turned off in v1.

---

## 2. Installing the APK

**From the phone.** Copy `app-debug.apk` to the phone and open it. Android asks you to allow
installs from that source (the browser or file manager); allow it, install, and then you can
turn that permission off again.

**With adb.**

```
adb install -r app-debug.apk
```

The APKs that CI builds are signed with a debug key that is created fresh on each runner. Two
builds from different workflow runs therefore have different signatures, and Android refuses
to install one over the other ("App not installed" or `INSTALL_FAILED_UPDATE_INCOMPATIBLE`).
Uninstall the old build first. This also removes the pairing, so you need a new pairing code.
Setting up release signing (section 1.4) removes this step.

---

## 3. Setting up the phone

Open the **Sauti** app. The setup screen has four steps.

1. **Microphone.** Tap **Allow microphone** and choose *While using the app*. If you denied it
   before, the button opens the app's system settings instead.
2. **Keyboard.** Tap **Keyboard settings** and turn on **Sauti voice keyboard**. Android shows
   its usual notice that a keyboard can read what you type. Sauti only sends audio you record
   with the microphone button; it does not send typed text. Then tap **Choose keyboard** and
   pick Sauti, or switch to it later with the globe key of any keyboard.
3. **Pair with your server.**
   - In the Sauti web portal, sign in and choose **Pair a phone**. The portal shows a 6-digit
     code. The code works once and expires after 10 minutes.
   - On the phone, enter the server address (for example `https://dictate.example.lan`) and the
     code. The phone name is filled in for you; you can change it. Tap **Pair**.
   - The app calls `POST /v1/pairing/claim` and stores the device token it gets back in
     encrypted storage (`EncryptedSharedPreferences`, with a key held in the Android
     Keystore).
   - **Test connection** checks `GET /v1/health` and `GET /v1/me/profile` and shows the server
     version and the signed-in user.
   - **Unpair** revokes the device on the server (`DELETE /v1/me/devices/{id}`) when the server
     can be reached, and always removes the token from the phone.
4. **Defaults.** Spoken language (Auto, English, Somali, Arabic, Swahili), mode (Clean,
   Verbatim, Translate), translation target and style. **Save** stores them on your account
   (`PATCH /v1/me/settings`), so your other devices use them too.

In a debug build, the server field starts filled in with `http://10.0.2.2:8700`. That is the
address of a development server running on the same computer as the Android emulator.

---

## 4. Using the keyboard

```
 ● Ready · English · Clean                         ⚙
   (live preview of what you are saying)
 [ Clean ] [ Auto ]
                     ( 🎤 )
 [🌐] [          space          ] [ ⌫ ] [ ↵ ]
```

- **Microphone button.** Tap it to start, and tap again to stop. Or press and hold it: it
  records while you hold and stops when you let go. While the microphone is on, the button is
  red, shows a stop square, and has a pulsing ring around it. The status line says
  *Listening…*.
- **Live preview.** The grey line shows the server's partial text while you speak. It is only
  a preview and is never inserted.
- **Final text.** When the server finishes, the text is inserted at the cursor. A space is
  added in front if the character before the cursor is not a space. Each dictation is inserted
  at most once, even if the connection drops and the server sends the result again. If no text
  field is active when the result arrives (for example, you closed the app), the text is
  copied to the clipboard and the keyboard tells you so.
- **Mode chip.** Tap to switch between Clean, Verbatim and Translate→XX. Long-press it to change
  the translation target (EN, SO, AR, SW); this also switches to Translate.
- **Language chip.** Tap to switch between Auto, EN, SO, AR and SW. This is the language you
  speak.
  The chips change the settings on this phone and apply from the next dictation. The next
  settings sync from the server (after **Save**, or a change made on another device)
  replaces them.
- **Globe key.** Switches to the next keyboard. Long-press it to open the keyboard picker.
- **Space**, **backspace** (hold to repeat) and **enter**. Enter runs the field's action, such
  as Send, Search or Go, when the field has one; otherwise it inserts a new line.
- **⚙** opens the setup screen.
- In password fields the microphone is turned off.

### Status line

| Status | Meaning |
|---|---|
| ● green *Ready · English · Clean* | Connected; shows the spoken language and mode. |
| ● amber *Connecting…* | Opening the connection. |
| ● red *Listening…* | The microphone is on. |
| *Listening… (offline, will send when connected)* | Recording while the server cannot be reached. The audio is kept in memory and sent once the connection is back. |
| ● blue *Sending… / Queued (position 2)… / Transcribing… / Cleaning up… / Translating…* | The server is working on your dictation. |
| ● amber *Server busy — retrying in 3 s* | The server's queue is full. The same dictation is sent again automatically. |
| ● red *Server unavailable — retrying in 4 s* | No connection. The keyboard tries again after 0.5, 1, 2 and 4 seconds, then every 8 seconds. Tap the status line to try again now. It also retries as soon as the phone's network comes back. |
| *Server certificate not trusted — retrying…* | See section 7.2. |
| *Dictation could not be processed — server unavailable* | A dictation could not be delivered within 2 minutes of when you stopped speaking. Its audio has been discarded. |
| *Pairing no longer valid — open Sauti to pair again* | The server rejected the device token (for example, the device was removed in the portal). The keyboard stops reconnecting until you pair again. |
| *Not paired — open Sauti to set up* / *Microphone permission needed* | A button in place of the microphone opens the setup screen. |

---

## 5. Permissions

| Permission | Why |
|---|---|
| `RECORD_AUDIO` | To hear you while you dictate. Requested in the setup screen; a keyboard cannot ask for permissions itself. |
| `INTERNET` | To talk to your Sauti server. |
| `ACCESS_NETWORK_STATE` | To reconnect promptly when the network comes back. |

The app asks for no other permissions: no contacts, storage, location or phone state.

---

## 6. Privacy and security

- **Microphone.** It is opened only when you tap or hold the microphone button, and closed
  when you stop, when the keyboard is hidden, or when the server's length limit is reached
  (default 120 seconds). On Android 12 and newer, the system also shows its own microphone
  indicator while it is on.
- **Audio.** It is captured at 16 kHz mono and streamed to your server in 100 ms frames. Each
  dictation's audio is kept in memory only until its text arrives, or until it fails for good,
  so that it can be resent after a dropped connection. Audio is never written to the phone's
  storage.
- **Text.** The keyboard keeps no text history. To make sure no text is inserted twice, it
  keeps the IDs (random UUIDs) of the last 500 inserted dictations; it does not keep their
  text. Whether the server keeps transcript history is an account setting (`history_enabled`,
  off by default).
- **Token.** The device token is stored with `EncryptedSharedPreferences`. It is sent in the
  first WebSocket message and in the `Authorization` header of REST calls, never in a URL.
  Android backup is turned off for the app.
- **Network.**
  - Release builds use HTTPS only. They trust the system certificate authorities and also CAs
    the user has installed, so a server with an internal company CA works once that CA
    certificate is installed on the phone.
  - Debug builds also allow plain `http://`, for development servers.
- **No third parties.** There are no analytics, crash reporters or other outside services. The
  app's logs (`adb logcat -s Sauti`) contain connection events only, never transcript text,
  audio or tokens.

---

## 7. Troubleshooting

### 7.1 "Server unavailable" or "Server not found"

- Check the server address in the setup screen. It must be the same address the portal uses,
  for example `https://dictate.example.lan`, with no `/v1/...` at the end.
- Make sure the phone is on a network that can reach the server: the office Wi-Fi, or the VPN
  if the server is internal. Mobile data usually cannot reach an internal server.
- On a laptop on the same network, run `curl https://dictate.example.lan/v1/health`. It should
  return `{"status": "ok", ...}`.
- Tap **Test connection** in the app. The message it shows names the problem: name not found,
  connection refused, timeout, or certificate.
- If the status says *degraded*, the server is reachable but one of its speech or language
  models is down. Ask the server administrator.

### 7.2 Certificate errors ("Server certificate not trusted")

The phone does not trust the server's TLS certificate. If your organisation uses its own
certificate authority:

1. Get the CA certificate (a `.crt` or `.pem` file) from the administrator. You need the CA
   certificate, not the server's own certificate.
2. On the phone: **Settings → Security → Encryption & credentials → Install a certificate → CA
   certificate**. The exact menu names vary by manufacturer.
3. Try **Test connection** again.

Also check that:

- the certificate covers the exact host name you typed (the subject alternative name);
- the certificate has not expired;
- the phone's date and time are correct.

### 7.3 "Plain http is blocked"

Release builds refuse `http://` addresses. Use the server's `https://` address. Plain HTTP only
works in debug builds.

### 7.4 Microphone

- If the keyboard shows **Allow microphone**, tap it. The setup screen opens and asks for the
  permission.
- If you denied it permanently, go to **Settings → Apps → Sauti → Permissions → Microphone →
  Allow only while using the app**.
- *Microphone unavailable* means another app (a call, or a voice recorder) is using it. Close
  that app and try again.

### 7.5 The keyboard does not appear

- Make sure **Sauti voice keyboard** is turned on in the system keyboard settings (setup step
  2).
- Switch to it with the globe key of your current keyboard, or with the keyboard icon in the
  navigation bar.
- After reinstalling, Android may turn the keyboard off again. Turn it back on.

### 7.6 "Pairing no longer valid"

The device was removed in the portal, or its token was revoked. Open Sauti, tap **Unpair** if
the phone still shows a pairing, and pair again with a new code.

---

## 8. Known limitations (v1)

- The interface is in English only.
- This is a voice keyboard with no letter keys. To type, switch to another keyboard with the
  globe key.
- Debug builds from CI cannot be installed over each other without uninstalling first (section
  2). Release signing is not set up yet.
- If the connection drops in the middle of a recording, the keyboard keeps recording and sends
  everything, from the start, once it reconnects. This relies on the server not having
  processed the half-received recording on its own. If the server did finish it, only the part
  before the drop is transcribed.
- A dictation that cannot be delivered within 2 minutes of when you stopped speaking is
  dropped, and the keyboard tells you so. If its text arrives later anyway, it is not
  inserted.
- The personal dictionary and transcript history are managed in the web portal, not in the
  app.
- If you change settings on another device while the phone is offline, the phone picks them
  up the next time it connects.
- There is no offline speech recognition: dictation needs the server.
