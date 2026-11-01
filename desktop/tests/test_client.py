"""Network client against a real server process with fake models.

Covers the reliability rules: delivery exactly once, resume after the server
restarts mid-utterance, retries after "busy", and in-order delivery.
"""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "desktop"))

import uvicorn  # noqa: E402

from sauti_desktop.client import DictationClient  # noqa: E402
from sauti_desktop.ledger import Ledger  # noqa: E402
from sauti_server.app import create_app  # noqa: E402
from sauti_server.backends.base import Backends  # noqa: E402
from sauti_server.backends.fake import FakeAsr, FakeLlm, encode_text  # noqa: E402
from sauti_server.config import Config  # noqa: E402
from sauti_server.db import Store  # noqa: E402


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class ServerThread:
    def __init__(self, data_dir: Path, port: int, asr_delay: float = 0.05):
        cfg = Config()
        cfg.server.data_dir = str(data_dir)
        cfg.scheduler.partials = False
        self.fakes = Backends(FakeAsr(delay_s=asr_delay), FakeLlm(), None)
        self.server = uvicorn.Server(uvicorn.Config(create_app(cfg, self.fakes), host="127.0.0.1", port=port,
                                                    log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        while not self.server.started:
            time.sleep(0.02)
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(5)


class Recorder:
    def __init__(self):
        self.finals: list[tuple[str, str]] = []
        self.failures: list[tuple[str, str]] = []
        self.states: list[str] = []

    def connection(self, state, detail): self.states.append(state)
    def partial(self, uid, text): pass
    def status(self, uid, state, pos): pass
    def final(self, uid, msg): self.finals.append((uid, msg["text"]))
    def failed(self, uid, code, message): self.failures.append((uid, code))
    def profile_changed(self): pass


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    store = Store(data / "sauti.db")
    uid = store.create_user("amina", "Amina", "amina-pass-1")
    _, dek = store.authenticate("amina", "amina-pass-1")
    _, token = store.create_token(uid, "device", dek, name="test")
    store.close()
    return data, token, tmp_path


def wait(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def speak(client: DictationClient, text: str, lang: str = "en") -> str:
    uid = client.begin(mode="verbatim", lang="auto", target_lang="en")
    pcm = encode_text(text, lang, 1.0)
    for i in range(0, len(pcm), 3200):
        client.push_audio(pcm[i:i + 3200])
    client.finish(uid)
    return uid


def test_final_delivered_once(env):
    data, token, tmp = env
    port = free_port()
    with ServerThread(data, port):
        ev = Recorder()
        c = DictationClient(f"ws://127.0.0.1:{port}/v1/dictate", token, ev, Ledger(tmp / "ledger.json"))
        c.start()
        assert wait(lambda: "ready" in ev.states)
        uid = speak(c, "hello from the desktop")
        assert wait(lambda: ev.finals)
        assert ev.finals == [(uid, "hello from the desktop")]
        c.stop()


def test_server_restart_mid_utterance_resends_and_delivers_once(env):
    data, token, tmp = env
    port = free_port()
    ev = Recorder()
    ledger = Ledger(tmp / "ledger.json")
    srv = ServerThread(data, port, asr_delay=1.5).__enter__()
    c = DictationClient(f"ws://127.0.0.1:{port}/v1/dictate", token, ev, ledger)
    c.start()
    assert wait(lambda: "ready" in ev.states)
    uid = speak(c, "survives a server restart")
    time.sleep(0.3)  # the server is now transcribing ...
    srv.__exit__()  # ... and goes away; its in-memory result is lost
    assert wait(lambda: "offline" in ev.states)
    with ServerThread(data, port, asr_delay=0.05):
        # The client reconnects, resumes, learns the server does not know the
        # utterance and resends the audio it kept in memory.
        assert wait(lambda: ev.finals, timeout=20)
    assert ev.finals == [(uid, "survives a server restart")]
    assert uid in ledger
    c.stop()


def test_duplicate_final_is_ignored(env):
    data, token, tmp = env
    port = free_port()
    with ServerThread(data, port):
        ev = Recorder()
        ledger = Ledger(tmp / "ledger.json")
        c = DictationClient(f"ws://127.0.0.1:{port}/v1/dictate", token, ev, ledger)
        c.start()
        assert wait(lambda: "ready" in ev.states)
        uid = speak(c, "only once please")
        assert wait(lambda: ev.finals)
        # Simulate a replay arriving later (e.g. a resume after a reconnect).
        c.loop.call_soon_threadsafe(lambda: c.loop.create_task(c._on_message(
            {"type": "final", "utterance_id": uid, "text": "only once please"})))
        time.sleep(0.3)
        assert len(ev.finals) == 1
        c.stop()


def test_busy_is_retried_with_same_id(env):
    data, token, tmp = env
    port = free_port()
    with ServerThread(data, port, asr_delay=0.6) as srv:
        app_state = None
        ev = Recorder()
        c = DictationClient(f"ws://127.0.0.1:{port}/v1/dictate", token, ev, Ledger(tmp / "ledger.json"))
        c.start()
        assert wait(lambda: "ready" in ev.states)
        # Make the server's per-user queue tiny so the 4th utterance is rejected as busy.
        app_state = srv.server.config.app.state.sauti
        app_state.asr_stage.max_per_user[0] = 1
        ids = [speak(c, f"utterance {i}") for i in range(4)]
        assert wait(lambda: len(ev.finals) == 4, timeout=30)
        assert [u for u, _ in ev.finals] == ids  # all delivered, in order
        assert app_state.asr_stage.counters["rejected"] >= 1
        c.stop()
