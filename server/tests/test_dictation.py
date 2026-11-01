import sqlite3
import time

from conftest import auth, device_token, say, session, until, utt, web_token

from sauti_server.backends.base import BackendUnavailable


def test_unauthorized_hello(client):
    with client.websocket_connect("/v1/dictate") as ws:
        ws.send_json({"type": "hello", "token": "sdt_bogus"})
        msg = ws.receive_json()
        assert msg["type"] == "error" and msg["code"] == "unauthorized"


def test_clean_dictation_round_trip(client):
    with session(client, device_token(client)) as ws:
        uid = say(ws, "um we need twenty chairs for the meeting", mode="clean")
        final = until(ws, "final")
        assert final["utterance_id"] == uid
        assert final["text"] == "We need twenty chairs for the meeting"
        assert final["raw"].startswith("um ")
        assert set(final["timings_ms"]) >= {"queue", "asr", "cleanup", "translate", "total"}


def test_verbatim_keeps_fillers(client):
    with session(client, device_token(client)) as ws:
        say(ws, "um hello there", mode="verbatim")
        assert until(ws, "final")["text"] == "um hello there"


def test_dictionary_alias_applied(client):
    tok = device_token(client)
    client.post("/v1/me/dictionary", headers=auth(tok), json={"term": "Hargeisa", "aliases": ["hargaysa"]})
    with session(client, tok) as ws:
        say(ws, "the office in hargaysa opens at nine", mode="verbatim")
        assert "Hargeisa" in until(ws, "final")["text"]


def test_translate_mode_uses_llm(client):
    with session(client, device_token(client)) as ws:
        say(ws, "habari za asubuhi rafiki yangu", lang="sw", mode="translate", target_lang="en")
        final = until(ws, "final")
        assert final["text"].startswith("[translated]") and final["source_text"] and final["target_lang"] == "en"


def test_low_confidence_is_not_translated(client):
    """Misrecognised speech must not be turned into fluent invented text by translation."""
    with session(client, device_token(client)) as ws:
        say(ws, "[low] onapo mpugia sime mtuwalia", lang="sw", mode="translate", target_lang="en")
        final = until(ws, "final")
    assert "not_translated" in final["notices"] and "low_confidence" in final["notices"]
    assert not final["text"].startswith("[translated]") and final["confidence"] < 0.5


def test_guard_fallback_when_llm_rewrites(client):
    client.fakes.llm.override = "Totally different words that were never spoken by anyone."
    with session(client, device_token(client)) as ws:
        say(ws, "um please book the room for Tuesday", mode="clean")
        final = until(ws, "final")
        assert final["text"] == "Please book the room for Tuesday"
        assert "cleanup_fallback" in final["notices"]


def test_llm_down_still_returns_rule_cleanup(client):
    client.fakes.llm.down = True
    with session(client, device_token(client)) as ws:
        say(ws, "uh the report is ready", mode="clean")
        final = until(ws, "final")
        assert final["text"] == "The report is ready" and "cleanup_unavailable" in final["notices"]


def test_silence_is_no_speech(client):
    with session(client, device_token(client)) as ws:
        uid = utt()
        ws.send_json({"type": "start", "utterance_id": uid})
        ws.send_bytes(b"\x00\x00" * 16000)
        ws.send_json({"type": "end", "utterance_id": uid})
        final = until(ws, "final")
        assert final["text"] == "" and "no_speech" in final["notices"]


def test_quiet_microphone_is_still_transcribed(client):
    """Very quiet speech (peak -46 dBFS) must reach the recogniser, not be dropped as silence."""
    import numpy as np
    cm_calls = client.fakes.asr.calls
    with session(client, device_token(client)) as ws:
        uid = utt()
        ws.send_json({"type": "start", "utterance_id": uid, "mode": "verbatim"})
        quiet = (0.005 * np.sin(np.arange(32000) * 2 * np.pi * 200 / 16000) * 32767).astype("<i2").tobytes()
        ws.send_bytes(quiet)
        ws.send_json({"type": "end", "utterance_id": uid})
        until(ws, "final")
    assert client.fakes.asr.calls > cm_calls


def test_partial_preview_arrives_while_speaking(client):
    client.fakes.asr.delay_s = 0.0
    with session(client, device_token(client)) as ws:
        uid = utt()
        from sauti_server.backends.fake import encode_text
        ws.send_json({"type": "start", "utterance_id": uid, "mode": "verbatim"})
        pcm = encode_text("one two three four five six", "en", seconds=3.0)
        for i in range(0, len(pcm), 3200):
            ws.send_bytes(pcm[i:i + 3200])
            time.sleep(0.005)
        time.sleep(0.2)
        ws.send_json({"type": "end", "utterance_id": uid})
        kinds = []
        for _ in range(20):
            m = ws.receive_json()
            kinds.append(m["type"])
            if m["type"] == "final":
                break
        assert "partial" in kinds and kinds[-1] == "final"


def test_reconnect_resume_replays_once(client):
    tok = device_token(client)
    with session(client, tok) as ws:
        uid = say(ws, "send the invoice today", mode="verbatim")
        first = until(ws, "final")
    with session(client, tok) as ws2:
        ws2.send_json({"type": "resume", "utterance_id": uid})
        replay = until(ws2, "final")
        assert replay["replayed"] is True and replay["text"] == first["text"]
        ws2.send_json({"type": "ack", "utterance_id": uid})
        ws2.send_json({"type": "ping", "t": 1})
        until(ws2, "pong")
        ws2.send_json({"type": "resume", "utterance_id": uid})
        assert until(ws2, "resume_result")["state"] == "unknown"


def test_duplicate_start_replays_instead_of_reprocessing(client):
    with session(client, device_token(client)) as ws:
        uid = say(ws, "hello again", mode="verbatim")
        until(ws, "final")
        calls = client.fakes.asr.calls
        say(ws, "hello again", mode="verbatim", utterance_id=uid)
        replay = until(ws, "final")
        assert replay["replayed"] is True and client.fakes.asr.calls == calls


def test_resume_unknown_after_drop_mid_recording(client):
    tok = device_token(client)
    with session(client, tok) as ws:
        uid = utt()
        ws.send_json({"type": "start", "utterance_id": uid})
        ws.send_bytes(b"\x01\x00" * 1600)
    with session(client, tok) as ws2:
        ws2.send_json({"type": "resume", "utterance_id": uid})
        assert until(ws2, "resume_result")["state"] == "unknown"
        say(ws2, "full resend works", mode="verbatim", utterance_id=uid)
        assert until(ws2, "final")["text"] == "full resend works"


def test_cancel_while_recording(client):
    with session(client, device_token(client)) as ws:
        uid = utt()
        ws.send_json({"type": "start", "utterance_id": uid})
        ws.send_json({"type": "cancel", "utterance_id": uid})
        err = until(ws, "error")
        assert err["code"] == "cancelled"


def test_cancel_while_processing(client):
    client.fakes.asr.delay_s = 1.0
    with session(client, device_token(client)) as ws:
        uid = say(ws, "this will be cancelled", mode="verbatim")
        time.sleep(0.1)
        ws.send_json({"type": "cancel", "utterance_id": uid})
        assert until(ws, "error")["code"] == "cancelled"


def test_busy_when_user_queue_full(client):
    client.st.cfg.scheduler.partials = False
    client.st.asr_stage.max_per_user[0] = 1
    client.fakes.asr.delay_s = 0.5
    with session(client, device_token(client)) as ws:
        # Two ASR workers run two utterances, one waits in the queue, the fourth is rejected.
        ids = [say(ws, f"utterance number {i}", mode="verbatim") for i in range(4)]
        finals, errors = [], []
        while len(finals) + len(errors) < 4:
            m = ws.receive_json()
            if m["type"] == "final":
                finals.append(m)
            elif m["type"] == "error":
                errors.append(m)
        assert errors and errors[0]["code"] == "busy" and errors[0]["retryable"] and errors[0]["retry_after_ms"] > 0
        # Retrying the rejected utterance with the same id now succeeds.
        rejected = errors[0]["utterance_id"]
        assert rejected in ids
        say(ws, "retried", mode="verbatim", utterance_id=rejected)
        assert until(ws, "final")["utterance_id"] == rejected


def test_backend_unavailable_is_retryable(client):
    client.st.cfg.scheduler.partials = False
    client.fakes.asr.fail_next = BackendUnavailable("model not loaded")
    with session(client, device_token(client)) as ws:
        say(ws, "anything", mode="verbatim")
        err = until(ws, "error")
        assert err["code"] == "unavailable" and err["retryable"]


def test_quota_exceeded(client):
    uid_user = client.st.store.user_by_name("amina")["id"]
    client.st.store.update_user_admin(uid_user, daily_limit_minutes=0.01)
    client.st.store.add_usage(uid_user, 5.0)
    with session(client, device_token(client)) as ws:
        say(ws, "too much talking", mode="verbatim")
        assert until(ws, "error")["code"] == "quota_exceeded"


def test_history_opt_in_encrypted_and_deletable(client):
    tok = device_token(client)
    client.patch("/v1/me/settings", headers=auth(tok), json={"history_enabled": True, "retention_days": 7})
    with session(client, tok) as ws:
        say(ws, "the patient code is 4471", mode="verbatim")
        until(ws, "final")
    items = client.get("/v1/me/history", headers=auth(tok)).json()["items"]
    assert items and "4471" in items[0]["text"]
    raw = sqlite3.connect(client.st.store.path).execute("SELECT blob FROM history").fetchone()[0]
    assert b"4471" not in raw
    # Other users and the admin API never see it.
    other = device_token(client, "omar", "omar-pass-12")
    assert client.get("/v1/me/history", headers=auth(other)).json()["items"] == []
    adm = web_token(client)
    users = client.get("/v1/admin/users", headers=auth(adm)).json()["items"]
    assert [u for u in users if u["username"] == "amina"][0]["history_items"] == 1
    assert "4471" not in client.get("/v1/admin/overview", headers=auth(adm)).text
    assert client.delete(f"/v1/me/history/{items[0]['id']}", headers=auth(tok)).status_code == 204
    assert client.get("/v1/me/history", headers=auth(tok)).json()["items"] == []


def test_history_off_by_default(client):
    tok = device_token(client)
    with session(client, tok) as ws:
        say(ws, "nothing is stored", mode="verbatim")
        until(ws, "final")
    assert client.get("/v1/me/history", headers=auth(tok)).json()["items"] == []


def test_revoking_device_closes_socket(client):
    tok = device_token(client)
    with session(client, tok) as ws:
        dev_id = client.get("/v1/me/devices", headers=auth(tok)).json()["items"][0]["id"]
        adm = web_token(client)
        assert client.delete(f"/v1/admin/devices/{dev_id}", headers=auth(adm)).status_code == 204
        assert until(ws, "error")["code"] == "unauthorized"


def test_profile_change_pushed_to_other_devices(client):
    laptop = device_token(client, name="laptop")
    phone = device_token(client, name="phone")
    with session(client, laptop) as ws:
        client.patch("/v1/me/settings", headers=auth(phone), json={"style": "formal"})
        assert until(ws, "profile_changed")["version"] > 0


def test_admin_sees_live_session_without_content(client):
    with session(client, device_token(client)) as ws:
        adm = web_token(client)
        sessions = client.get("/v1/admin/sessions", headers=auth(adm)).json()["items"]
        assert sessions and sessions[0]["username"] == "amina" and "text" not in sessions[0]
