"""Tray / menu-bar application: glues hotkeys, audio, the network client and insertion."""
from __future__ import annotations

import argparse
import logging
import os
import platform
import sys
import time
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSystemTrayIcon,
                               QVBoxLayout, QWidget)

from . import __version__
from .api import Api, ApiError
from .audio import FileSource, MicSource, list_inputs
from .client import DictationClient
from .hotkeys import Hotkeys, parse
from .insert import Inserter, foreground
from .ledger import Ledger
from .overlay import Overlay
from .settings import Settings, config_dir, delete_token, load_token, save_token

log = logging.getLogger("sauti")
LANGS = {"auto": "Auto-detect", "en": "English", "so": "Somali", "ar": "Arabic", "sw": "Swahili"}
TARGETS = {k: v for k, v in LANGS.items() if k != "auto"}
MODES = {"clean": "Clean dictation", "verbatim": "Verbatim", "translate": "Translate"}
STYLES = {"neutral": "Neutral", "formal": "Formal", "casual": "Casual", "message": "Chat message"}
NOTICES = {
    "cleanup_fallback": "Basic cleanup used (meaning check)",
    "cleanup_unavailable": "Cleanup model offline — basic cleanup used",
    "translation_unverified": "Check translation: numbers or length differ",
    "truncated": "Recording hit the length limit",
}


STYLE = """
QDialog { background: #ffffff; }
QDialog QLabel { color: #15201f; }
QLabel#h1 { font-size: 15pt; font-weight: 650; }
QLabel#sub { color: #66736f; }
QLabel#err { color: #b3261e; }
QLineEdit, QComboBox { border: 1px solid #d3d8d0; border-radius: 8px; padding: 6px 9px; background: #ffffff;
                       color: #15201f; selection-background-color: #0f6e5c; min-height: 20px; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #0f6e5c; }
QPushButton { border: 1px solid #d3d8d0; border-radius: 8px; padding: 7px 16px; background: #ffffff; color: #15201f; font-weight: 600; }
QPushButton:hover { background: #eceee9; }
QPushButton:default { background: #0f6e5c; color: #ffffff; border-color: #0f6e5c; }
QPushButton:default:hover { background: #13866f; }
QCheckBox { color: #15201f; spacing: 8px; }
QMenu { background: #1b2322; color: #e9eeec; border: 1px solid #2c3634; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: #24413a; }
QMenu::item:disabled { color: #8f9c98; }
QMenu::separator { height: 1px; background: #2c3634; margin: 5px 8px; }
QMenu::indicator { width: 14px; height: 14px; left: 4px; }
"""


def dialog_header(title: str, subtitle: str) -> QWidget:
    w = QWidget()
    row = QHBoxLayout(w)
    row.setContentsMargins(0, 0, 0, 8)
    logo = QLabel()
    logo.setPixmap(make_icon("#0f6e5c").pixmap(40, 40))
    text = QVBoxLayout()
    h1 = QLabel(title)
    h1.setObjectName("h1")
    sub = QLabel(subtitle)
    sub.setObjectName("sub")
    sub.setWordWrap(True)
    text.addWidget(h1)
    text.addWidget(sub)
    row.addWidget(logo)
    row.addSpacing(10)
    row.addLayout(text, 1)
    return w


def make_icon(color: str, ring: bool = False) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 16, 16)
    p.setBrush(QColor("#ffffff"))
    path = QPainterPath()
    path.addRoundedRect(QRectF(24, 12, 16, 26), 8, 8)
    p.drawPath(path)
    p.drawRect(QRectF(30, 42, 4, 8))
    p.drawRect(QRectF(22, 49, 20, 4))
    pen = p.pen()
    pen.setColor(QColor("#ffffff"))
    pen.setWidth(4)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(17, 18, 30, 26), 200 * 16, 140 * 16)
    if ring:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e5483b"))
        p.drawEllipse(QRectF(44, 2, 18, 18))
    p.end()
    return QIcon(pm)


class Bridge(QObject):
    """Moves events from the network/audio/hotkey threads onto the Qt thread."""

    connection = Signal(str, str)
    partial = Signal(str, str)
    status = Signal(str, str, object)
    final = Signal(str, dict)
    failed = Signal(str, str, str)
    profile_changed = Signal()
    hotkey_down = Signal(str)
    hotkey_up = Signal(str)
    level = Signal(float)
    captured = Signal(str)


class _Events:
    def __init__(self, b: Bridge):
        self.b = b

    def connection(self, state, detail): self.b.connection.emit(state, detail)
    def partial(self, uid, text): self.b.partial.emit(uid, text)
    def status(self, uid, state, pos): self.b.status.emit(uid, state, pos)
    def final(self, uid, msg): self.b.final.emit(uid, msg)
    def failed(self, uid, code, message): self.b.failed.emit(uid, code, message)
    def profile_changed(self): self.b.profile_changed.emit()


class SignInDialog(QDialog):
    def __init__(self, settings: Settings):
        super().__init__()
        self.setWindowTitle("Sign in to Sauti")
        self.settings = settings
        self.result_data: dict | None = None
        form = QFormLayout()
        self.server = QLineEdit(settings.server_url)
        self.user = QLineEdit(settings.username)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.name = QLineEdit(settings.device_name or platform.node())
        self.ca = QLineEdit(settings.ca_file)
        self.ca.setPlaceholderText("optional: CA certificate file for a self-hosted server")
        form.addRow("Server address", self.server)
        form.addRow("Username", self.user)
        form.addRow("Password", self.pw)
        form.addRow("This computer's name", self.name)
        form.addRow("Custom CA", self.ca)
        self.err = QLabel("")
        self.err.setWordWrap(True)
        self.err.setObjectName("err")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Sign in")
        buttons.button(QDialogButtonBox.Ok).setDefault(True)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.addWidget(dialog_header("Sign in to Sauti", "Your voice is processed only on your organisation's own server."))
        lay.addLayout(form)
        lay.addWidget(self.err)
        lay.addWidget(buttons)
        self.resize(460, 0)

    def _submit(self) -> None:
        url = self.server.text().strip().rstrip("/")
        try:
            data = Api(url, ca_file=self.ca.text().strip()).register(self.user.text().strip(), self.pw.text(),
                                                                     self.name.text().strip())
        except ApiError as e:
            self.err.setText(str(e))
            return
        except Exception as e:  # certificate problems etc.
            self.err.setText(f"Connection failed: {e}")
            return
        self.settings.server_url = url
        self.settings.username = self.user.text().strip()
        self.settings.device_name = self.name.text().strip()
        self.settings.ca_file = self.ca.text().strip()
        self.settings.device_id = data["device_id"]
        self.result_data = data
        self.accept()


class SettingsDialog(QDialog):
    def __init__(self, app: "SautiApp"):
        super().__init__()
        self.app = app
        s = app.settings
        self.setWindowTitle("Sauti settings")
        form = QFormLayout()
        self.ptt = QLineEdit(s.push_to_talk)
        self.toggle = QLineEdit(s.toggle)
        form.addRow("Hold to talk", self._with_record(self.ptt))
        form.addRow("Start / stop", self._with_record(self.toggle))
        self.method = QComboBox()
        for k, v in {"paste": "Paste into the active app", "type": "Type characters", "clipboard": "Copy to clipboard only"}.items():
            self.method.addItem(v, k)
        self.method.setCurrentIndex(max(0, self.method.findData(s.insert_method)))
        form.addRow("Insert text by", self.method)
        self.restore = QCheckBox("Restore my clipboard after pasting")
        self.restore.setChecked(s.restore_clipboard)
        form.addRow("", self.restore)
        self.mic = QComboBox()
        self.mic.addItem("System default", "")
        for name in list_inputs():
            self.mic.addItem(name, name)
        self.mic.setCurrentIndex(max(0, self.mic.findData(s.mic_device)))
        form.addRow("Microphone", self.mic)
        self.overlay = QCheckBox("Show the status pill while dictating")
        self.overlay.setChecked(s.show_overlay)
        form.addRow("", self.overlay)
        self.style = QComboBox()
        for k, v in STYLES.items():
            self.style.addItem(v, k)
        self.style.setCurrentIndex(max(0, self.style.findData(app.profile_settings.get("style", "neutral"))))
        form.addRow("Writing style (synced)", self.style)
        self.err = QLabel("")
        self.err.setObjectName("err")
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setDefault(True)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.addWidget(dialog_header("Sauti settings", f"Signed in as {s.username} on {s.server_url}"))
        lay.addLayout(form)
        lay.addWidget(self.err)
        lay.addWidget(buttons)
        self.resize(480, 0)

    def _with_record(self, edit: QLineEdit) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(edit)
        btn = QPushButton("Record…")

        def record():
            btn.setText("Press keys…")
            self.app.bridge.captured.connect(lambda combo: (edit.setText(combo), btn.setText("Record…")),
                                             Qt.SingleShotConnection)
            self.app.hotkeys.record_next(self.app.bridge.captured.emit)
        btn.clicked.connect(record)
        h.addWidget(btn)
        return w

    def _save(self) -> None:
        try:
            parse(self.ptt.text())
            parse(self.toggle.text())
        except ValueError as e:
            self.err.setText(f"Hotkey not understood: {e}")
            return
        s = self.app.settings
        s.push_to_talk, s.toggle = self.ptt.text().strip(), self.toggle.text().strip()
        s.insert_method = self.method.currentData()
        s.restore_clipboard = self.restore.isChecked()
        s.mic_device = self.mic.currentData()
        s.show_overlay = self.overlay.isChecked()
        s.save()
        self.app.apply_settings()
        if self.style.currentData() != self.app.profile_settings.get("style"):
            self.app.update_profile(style=self.style.currentData())
        self.accept()


class SautiApp(QObject):
    def __init__(self, qt: QApplication, test_audio: list[str] | None):
        super().__init__()
        self.qt = qt
        self.settings = Settings.load()
        self.bridge = Bridge()
        self.overlay = Overlay()
        self.overlay.enabled = self.settings.show_overlay
        self.inserter = Inserter(self.settings.insert_method, self.settings.restore_clipboard)
        self.ledger = Ledger(config_dir() / "ledger.json")
        self.test_source = FileSource(test_audio) if test_audio else None
        if self.test_source:
            self.overlay.badge = "TEST AUDIO"
        self.client: DictationClient | None = None
        self.api: Api | None = None
        self.profile_settings: dict = {}
        self.token: str | None = None
        self.conn_state = "offline"
        self.source = None
        self.recording_id: str | None = None
        self.recording_mode = ""  # ptt | toggle
        self.recording_started = 0.0
        self.targets: dict[str, int] = {}
        self.last_text = ""

        self.tray = QSystemTrayIcon(make_icon("#0f6e5c"))
        self.icons = {"ready": make_icon("#0f6e5c"), "offline": make_icon("#7d8790"),
                      "recording": make_icon("#0f6e5c", ring=True), "processing": make_icon("#b7791f")}
        self.menu = QMenu()
        self.status_action = self.menu.addAction("Starting…")
        self.status_action.setEnabled(False)
        self.menu.addSeparator()
        self.mode_menu = self.menu.addMenu("Mode")
        self.lang_menu = self.menu.addMenu("Spoken language")
        self.target_menu = self.menu.addMenu("Translate into")
        self.groups = {}
        for key, menu, options in (("mode", self.mode_menu, MODES), ("input_lang", self.lang_menu, LANGS),
                                   ("output_lang", self.target_menu, TARGETS)):
            grp = QActionGroup(self)
            for k, v in options.items():
                act = QAction(v, self, checkable=True)
                act.setData(k)
                act.triggered.connect(lambda _=False, key=key, k=k: self.update_profile(**{key: k}))
                grp.addAction(act)
                menu.addAction(act)
            self.groups[key] = grp
        self.menu.addSeparator()
        self.copy_action = self.menu.addAction("Copy last dictation", self.copy_last)
        self.copy_action.setEnabled(False)
        self.hint_action = self.menu.addAction("")
        self.hint_action.setEnabled(False)
        self.menu.addSeparator()
        self.menu.addAction("Settings…", self.open_settings)
        self.signin_action = self.menu.addAction("Sign in…", self.sign_in)
        self.signout_action = self.menu.addAction("Sign out", self.sign_out)
        self.menu.addAction(f"Quit Sauti {__version__}", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Sauti dictation")
        self.tray.show()

        b = self.bridge
        b.connection.connect(self.on_connection)
        b.partial.connect(self.on_partial)
        b.status.connect(self.on_status)
        b.final.connect(self.on_final)
        b.failed.connect(self.on_failed)
        b.profile_changed.connect(self.refresh_profile)
        b.hotkey_down.connect(self.on_hotkey_down)
        b.hotkey_up.connect(self.on_hotkey_up)
        b.level.connect(self.overlay.set_level)

        self.hotkeys = Hotkeys(self.settings.push_to_talk, self.settings.toggle, b.hotkey_down.emit, b.hotkey_up.emit)
        self.hotkeys.start()
        self._update_hint()
        QTimer.singleShot(0, self.connect_or_sign_in)

    # -- account ---------------------------------------------------------------------
    def connect_or_sign_in(self) -> None:
        self.token = load_token(self.settings.server_url)
        if self.token:
            self.start_client()
        else:
            self.set_status("Not signed in", "offline")
            self.sign_in()

    def sign_in(self) -> None:
        dlg = SignInDialog(self.settings)
        if dlg.exec() and dlg.result_data:
            save_token(self.settings.server_url, dlg.result_data["device_token"])
            self.settings.save()
            self.token = dlg.result_data["device_token"]
            self.start_client()

    def sign_out(self) -> None:
        if self.api and self.settings.device_id:
            try:
                self.api.remove_device(self.settings.device_id)
            except ApiError:
                pass
        self.stop_client()
        delete_token(self.settings.server_url)
        self.token = None
        self.settings.device_id = 0
        self.settings.save()
        self.set_status("Signed out", "offline")

    def start_client(self) -> None:
        self.stop_client()
        self.api = Api(self.settings.server_url, self.token, self.settings.ca_file)
        self.client = DictationClient(self.api.ws_url(), self.token, _Events(self.bridge), self.ledger,
                                      self.settings.ca_file)
        self.client.start()
        self.refresh_profile()

    def stop_client(self) -> None:
        if self.client:
            self.client.stop()
            self.client = None

    def refresh_profile(self) -> None:
        if not self.api:
            return
        try:
            prof = self.api.profile()
        except ApiError as e:
            log.info("profile fetch failed: %s", e.code)
            return
        self.profile_settings = prof["settings"]
        for key, grp in self.groups.items():
            for act in grp.actions():
                act.setChecked(act.data() == self.profile_settings.get(key))
        self.target_menu.setEnabled(self.profile_settings.get("mode") == "translate")
        mode = self.profile_settings.get("mode", "clean")
        lang = self.profile_settings.get("input_lang", "auto")
        self.overlay.chip = (f"Translate → {self.profile_settings.get('output_lang', 'en').upper()}" if mode == "translate"
                             else f"{MODES.get(mode, mode).split()[0]} · {'Auto' if lang == 'auto' else lang.upper()}")
        self._update_hint()

    def update_profile(self, **values) -> None:
        if not self.api:
            return
        try:
            prof = self.api.patch_settings(**values)
            self.profile_settings = prof["settings"]
        except ApiError as e:
            self.tray.showMessage("Sauti", f"Could not save setting: {e}", QSystemTrayIcon.Warning)
        self.refresh_profile()

    def apply_settings(self) -> None:
        s = self.settings
        self.hotkeys.update(s.push_to_talk, s.toggle)
        self.inserter.method, self.inserter.restore_clipboard = s.insert_method, s.restore_clipboard
        self.overlay.enabled = s.show_overlay
        self._update_hint()

    def open_settings(self) -> None:
        SettingsDialog(self).exec()

    # -- status ------------------------------------------------------------------------------
    def set_status(self, text: str, icon: str) -> None:
        self.status_action.setText(text)
        self.tray.setIcon(self.icons.get(icon, self.icons["ready"]))
        self.tray.setToolTip(f"Sauti — {text}")

    def _update_hint(self) -> None:
        mode = MODES.get(self.profile_settings.get("mode", "clean"), "")
        lang = LANGS.get(self.profile_settings.get("input_lang", "auto"), "")
        self.hint_action.setText(f"Hold {self.settings.push_to_talk} · {mode} · {lang}")

    def on_connection(self, state: str, detail: str) -> None:
        log.info("connection %s %s", state, detail)
        self.conn_state = state
        if state == "ready":
            self.set_status("Connected", "ready")
            if self.overlay.isVisible() and self.overlay.kind == "error":
                self.overlay.show_state("done", "Reconnected", hide_after_ms=1500)
        elif state == "connecting":
            self.set_status("Connecting…", "offline")
        elif state == "unauthorized":
            self.set_status("Signed out by the server", "offline")
            self.tray.showMessage("Sauti", "This device was signed out. Sign in again.", QSystemTrayIcon.Warning)
        else:
            self.set_status(f"Server unavailable — {detail}", "offline")
            if self.client and self.client.has_pending():
                self.overlay.show_state("error", "Server unavailable — your dictation is kept", detail)

    # -- recording -----------------------------------------------------------------------------
    def on_hotkey_down(self, name: str) -> None:
        log.info("hotkey down %s (recording=%s)", name, self.recording_id is not None)
        if name == "ptt" and self.recording_id is None:
            self.start_recording("ptt")
        elif name == "toggle":
            if self.recording_id is None:
                self.start_recording("toggle")
            else:
                self.stop_recording()

    def on_hotkey_up(self, name: str) -> None:
        log.info("hotkey up %s", name)
        if name == "ptt" and self.recording_mode == "ptt":
            self.stop_recording()

    def start_recording(self, how: str) -> None:
        if not self.client:
            self.overlay.show_state("error", "Not signed in", "Open the Sauti tray menu to sign in", 2500)
            return
        self.recording_mode = how
        self.recording_started = time.monotonic()
        opts = {k: v for k, v in self.profile_settings.items() if k in ("mode", "input_lang", "output_lang")}
        uid = self.client.begin(mode=opts.get("mode", "clean"), lang=opts.get("input_lang", "auto"),
                                target_lang=opts.get("output_lang", "en"))
        self.recording_id = uid
        self.targets[uid] = foreground()
        self.source = self.test_source or MicSource(self.settings.mic_device)
        client = self.client

        def on_chunk(pcm: bytes, peak: float) -> None:
            client.push_audio(pcm)
            self.bridge.level.emit(peak)
        try:
            self.source.start(on_chunk)
        except Exception as e:
            log.warning("microphone failed: %s", e)
            client.cancel(uid)
            self.recording_id = None
            self.overlay.show_state("error", "Microphone unavailable", str(e)[:80], 3500)
            return
        log.info("recording %s started (%s), target window %s", uid[:8], how, self.targets[uid])
        hint = "release to finish" if how == "ptt" else f"press {self.settings.toggle} to finish"
        self.overlay.show_state("recording", "Listening", hint)
        self.set_status("Recording", "recording")

    def stop_recording(self) -> None:
        uid, self.recording_id = self.recording_id, None
        if uid is None:
            return
        if self.source:
            self.source.stop()
        held_ms = (time.monotonic() - self.recording_started) * 1000
        if held_ms < self.settings.min_hold_ms:
            self.client.cancel(uid)
            self.targets.pop(uid, None)
            self.overlay.show_state("info", "Too short — hold the key while you speak", hide_after_ms=1500)
            self.set_status("Connected" if self.conn_state == "ready" else "Offline", "ready")
            return
        self.client.finish(uid)
        log.info("recording %s finished after %d ms", uid[:8], held_ms)
        self.overlay.show_state("processing", "Processing…", "" if self.conn_state == "ready" else "waiting for the server")
        self.set_status("Processing", "processing")

    def on_partial(self, uid: str, text: str) -> None:
        if uid == self.recording_id:
            self.overlay.set_detail(text)

    def on_status(self, uid: str, state: str, position) -> None:
        if uid == self.recording_id:
            return
        label = {"queued": f"Queued{f' (position {position})' if position else ''}…", "transcribing": "Transcribing…",
                 "cleaning": "Cleaning up…", "translating": "Translating…", "retrying": "Server busy — retrying…"}.get(state)
        if label:
            self.overlay.show_state("processing", label)

    def on_final(self, uid: str, msg: dict) -> None:
        target = self.targets.pop(uid, 0)
        text = msg.get("text", "")
        if not text:
            log.info("final %s: no speech", uid[:8])
            self.overlay.show_state("info", "No speech detected", hide_after_ms=1500)
        else:
            self.last_text = text
            self.copy_action.setEnabled(True)
            outcome = self.inserter.insert(text, target)
            log.info("final %s: %d chars, inserted=%s %s, server %s ms", uid[:8], len(text), outcome.inserted,
                     outcome.reason, msg.get("timings_ms", {}).get("total"))
            notes = [NOTICES[n] for n in msg.get("notices", []) if n in NOTICES]
            t = msg.get("timings_ms", {})
            detail = " · ".join(notes) if notes else f"{msg.get('lang') or ''}{(' → ' + msg['target_lang']) if msg.get('target_lang') else ''} · {t.get('total', 0) / 1000:.1f} s"
            self.overlay.show_state("done" if outcome.inserted else "info", outcome.message, detail,
                                    hide_after_ms=1800 if outcome.inserted else 4500)
        if self.recording_id is None:
            self.set_status("Connected" if self.conn_state == "ready" else "Offline", "ready")

    def on_failed(self, uid: str, code: str, message: str) -> None:
        log.info("utterance %s failed: %s", uid[:8], code)
        self.targets.pop(uid, None)
        self.overlay.show_state("error", "Dictation failed", message, 5000)
        self.set_status("Connected" if self.conn_state == "ready" else "Offline", "ready")

    def copy_last(self) -> None:
        if self.last_text:
            self.inserter.copy_only(self.last_text)
            self.overlay.show_state("info", "Copied last dictation", hide_after_ms=1200)

    def quit(self) -> None:
        if self.source:
            self.source.stop()
        self.hotkeys.stop()
        self.stop_client()
        self.tray.hide()
        self.qt.quit()


def _setup_logging() -> None:
    handler = RotatingFileHandler(config_dir() / "sauti.log", maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler], format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sauti")
    ap.add_argument("--test-audio", default=os.environ.get("SAUTI_TEST_AUDIO", ""),
                    help="comma-separated WAV files streamed instead of the microphone (testing/demos)")
    args = ap.parse_args(argv)
    _setup_logging()
    QApplication.setQuitOnLastWindowClosed(False)
    qt = QApplication(sys.argv[:1])
    qt.setApplicationName("Sauti")
    qt.setStyleSheet(STYLE)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        QMessageBox.critical(None, "Sauti", "No system tray / menu bar is available.")
        sys.exit(1)
    app = SautiApp(qt, [p for p in args.test_audio.split(",") if p] or None)
    qt.aboutToQuit.connect(lambda: app.hotkeys.stop())
    sys.exit(qt.exec())
