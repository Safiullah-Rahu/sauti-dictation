# User guide

Sauti turns speech into text in whatever application you are using. Your voice is processed
only on your organisation's own server.

## Windows and macOS

### Install

* **Windows:** unzip `sauti-desktop-windows-x64.zip` and run `Sauti.exe` (or use the installer
  your administrator provides). To start it with Windows, put a shortcut to `Sauti.exe` in
  `shell:startup`.
* **macOS:** unzip and move `Sauti.app` to Applications. On first use macOS asks for three
  permissions — allow them in System Settings → Privacy & Security:
  * **Microphone** — to hear you while you hold the key;
  * **Accessibility** — to paste the text into the app you are using;
  * **Input Monitoring** — to notice the dictation key while another app is in front.

### Sign in

Sauti appears in the system tray (Windows) or menu bar (macOS). On first start enter the
server address from your administrator (for example `https://dictate.example.lan`), your
username and password. The password is used once; afterwards the computer has its own key in
the system keychain.

### Dictate

1. Click into the place where the text should go (an email, a chat box, a document).
2. **Hold Right Ctrl** (Windows) or **Right Option** (macOS) and speak. Release when done.
   Or press **Ctrl+Alt+Space** to start and again to stop, for longer dictation.
3. The text appears where your cursor is, usually within one to a few seconds.

The pill at the bottom of the screen shows what is happening: a red dot and timer while
listening (with a live preview of the words), dots while processing, then "Inserted".

If the text cannot be typed directly — you switched windows, or the target is an
administrator window — it is placed on the clipboard and the pill says "press Ctrl+V".
**Copy last dictation** in the tray menu copies it again.

### Modes and languages (tray menu)

* **Clean dictation** — removes "um", repeated words and false starts, fixes punctuation;
  never adds or summarises. If the cleanup model's version would change a name, number,
  date or negation, Sauti falls back to a simpler cleanup and keeps your words.
* **Verbatim** — exactly what was recognised.
* **Translate** — speak in one language, get the text in another (choose under
  *Translate into*).
* **Spoken language** — *Auto-detect* or a fixed language (English, Somali, Arabic, Swahili).
  Fixing the language is more reliable for short phrases.

These settings follow you to all your devices.

### Settings

Hotkeys (click *Record…* and press the keys), how text is inserted (paste, type, or clipboard
only), whether your clipboard is restored after pasting, microphone, the status pill, and
writing style (neutral, formal, casual, chat message).

## Android

1. Install the Sauti keyboard APK from your administrator.
2. Open the Sauti app and follow the three steps: allow the microphone, enable the Sauti
   keyboard, pair the phone.
3. To pair, sign in to the portal on a computer (`https://<server>/app` → Devices → *Pair a
   phone*) and type the 6-digit code into the app within 10 minutes.
4. In any app (WhatsApp, Gmail, ...), switch to the Sauti keyboard with the globe key, tap the
   microphone, speak, tap again. Long-press to hold-to-talk.

If the connection drops, the keyboard says so and keeps your recording until it can be sent.

## Portal (browser)

`https://<server>/app`: dictation defaults, personal dictionary, history, devices and phone
pairing. There is also a *Try dictation* page for checking languages from the browser.

### Personal dictionary

Add names and terms you use often. Under *Sounds like*, add how they tend to be misheard
(for example `Hargeisa` — sounds like `hargaysa`). They are corrected automatically and kept
exactly by the cleanup step.

### History

Off by default. When on, your dictations are stored encrypted with your own key for the
number of days you choose; only you can read them. You can delete single items or everything.
Turning history off deletes what was stored. Audio is never stored.

## Tips for accuracy

* Hold the key a moment before you start speaking and release after you finish.
* A headset or the laptop's built-in microphone in a quiet room works best.
* Pick the spoken language when you know it, especially for one- or two-word phrases.
* Add recurring names to your dictionary.
