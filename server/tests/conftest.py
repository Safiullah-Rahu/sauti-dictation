from __future__ import annotations

import contextlib
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sauti_server.app import create_app  # noqa: E402
from sauti_server.backends.base import Backends  # noqa: E402
from sauti_server.backends.fake import FakeAsr, FakeLlm, encode_text  # noqa: E402
from sauti_server.config import Config  # noqa: E402


@pytest.fixture
def cfg(tmp_path) -> Config:
    c = Config()
    c.server.data_dir = str(tmp_path / "data")
    c.asr.backend = "fake"
    c.llm.backend = "fake"
    c.translate.backend = "llm"
    c.scheduler.partial_interval_s = 0.5
    return c


@pytest.fixture
def fakes():
    return Backends(asr=FakeAsr(), llm=FakeLlm(), translate=None)


@pytest.fixture
def client(cfg, fakes):
    app = create_app(cfg, fakes)
    with TestClient(app) as c:
        st = app.state.sauti
        st.store.create_user("admin", "Admin", "admin-pass-1", "admin")
        st.store.create_user("amina", "Amina Yusuf", "amina-pass-1")
        st.store.create_user("omar", "Omar Ali", "omar-pass-12")
        c.st = st  # type: ignore[attr-defined]
        c.fakes = fakes  # type: ignore[attr-defined]
        yield c


def device_token(client, username="amina", password="amina-pass-1", name="laptop") -> str:
    r = client.post("/v1/devices/register", json={"username": username, "password": password,
                                                  "device_name": name, "platform": "windows"})
    assert r.status_code == 201, r.text
    return r.json()["device_token"]


def web_token(client, username="admin", password="admin-pass-1") -> str:
    r = client.post("/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def utt() -> str:
    return str(uuid.uuid4())


def say(ws, text: str, lang: str = "en", mode: str | None = None, utterance_id: str | None = None,
        seconds: float = 1.5, **start) -> str:
    """Send one fake utterance (start + PCM + end) and return its id."""
    uid = utterance_id or utt()
    msg = {"type": "start", "utterance_id": uid, **start}
    if mode:
        msg["mode"] = mode
    ws.send_json(msg)
    pcm = encode_text(text, lang, seconds)
    for i in range(0, len(pcm), 3200):
        ws.send_bytes(pcm[i:i + 3200])
    ws.send_json({"type": "end", "utterance_id": uid})
    return uid


def until(ws, kind: str, limit: int = 50) -> dict:
    for _ in range(limit):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
        if msg["type"] == "error" and kind != "error":
            raise AssertionError(f"unexpected error: {msg}")
    raise AssertionError(f"no {kind} message")


@contextlib.contextmanager
def session(client, token: str):
    """An authenticated dictation socket that is always closed, even when a test fails."""
    with client.websocket_connect("/v1/dictate") as conn:
        conn.send_json({"type": "hello", "token": token, "client": "test/1", "protocol": 1})
        ready = conn.receive_json()
        assert ready["type"] == "ready", ready
        yield conn
