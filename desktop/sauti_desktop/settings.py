"""Local settings (JSON in the user's config folder) and the device token (OS keychain)."""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

log = logging.getLogger("sauti.settings")
KEYRING_SERVICE = "Sauti dictation"


def config_dir() -> Path:
    override = os.environ.get("SAUTI_CONFIG_DIR")  # tests and portable installs
    if override:
        Path(override).mkdir(parents=True, exist_ok=True)
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    d = base / "Sauti"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_ptt() -> str:
    return "<alt_r>" if sys.platform == "darwin" else "<ctrl_r>"


@dataclass
class Settings:
    server_url: str = "https://dictate.example.lan"
    username: str = ""
    device_id: int = 0
    device_name: str = ""
    push_to_talk: str = field(default_factory=_default_ptt)
    toggle: str = "<ctrl>+<alt>+<space>"
    insert_method: str = "paste"  # paste | type | clipboard
    restore_clipboard: bool = True
    mic_device: str = ""  # empty = system default
    show_overlay: bool = True
    ca_file: str = ""  # extra CA certificate for a self-hosted server
    min_hold_ms: int = 300

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        s = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for f in fields(cls):
                    if f.name in data:
                        setattr(s, f.name, data[f.name])
            except (OSError, ValueError) as e:
                log.warning("settings unreadable, using defaults: %s", e)
        return s

    def save(self) -> None:
        path = config_dir() / "settings.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def _keyring():
    try:
        import keyring
        return keyring
    except Exception:  # pragma: no cover - keyring backend missing
        return None


def load_token(server_url: str) -> str | None:
    if os.environ.get("SAUTI_DEVICE_TOKEN"):  # automated tests only; normal installs use the keychain
        return os.environ["SAUTI_DEVICE_TOKEN"]
    kr = _keyring()
    if kr is not None:
        try:
            return kr.get_password(KEYRING_SERVICE, server_url)
        except Exception as e:
            log.warning("keychain read failed: %s", e)
    return None


def save_token(server_url: str, token: str) -> None:
    kr = _keyring()
    if kr is None:
        raise RuntimeError("No OS keychain available to store the device token.")
    kr.set_password(KEYRING_SERVICE, server_url, token)


def delete_token(server_url: str) -> None:
    kr = _keyring()
    if kr is not None:
        try:
            kr.delete_password(KEYRING_SERVICE, server_url)
        except Exception:
            pass
