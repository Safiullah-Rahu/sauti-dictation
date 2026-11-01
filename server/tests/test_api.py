import sqlite3

from conftest import auth, device_token, web_token


def test_health(client):
    assert client.get("/v1/health").json()["status"] == "ok"


def test_login_and_wrong_password(client):
    assert client.post("/v1/auth/login", json={"username": "amina", "password": "nope-nope"}).status_code == 401
    tok = web_token(client, "amina", "amina-pass-1")
    assert client.get("/v1/me", headers=auth(tok)).json()["user"]["username"] == "amina"


def test_login_rate_limited(client):
    codes = [client.post("/v1/auth/login", json={"username": "omar", "password": "wrong-pass"}).status_code
             for _ in range(12)]
    assert codes[-1] == 429


def test_pairing_flow(client):
    web = web_token(client, "amina", "amina-pass-1")
    code = client.post("/v1/pairing/codes", headers=auth(web)).json()["code"]
    assert len(code) == 6
    r = client.post("/v1/pairing/claim", json={"code": code, "device_name": "Pixel 8", "platform": "android"})
    assert r.status_code == 201
    dev = r.json()["device_token"]
    assert dev.startswith("sdt_")
    # Single use.
    assert client.post("/v1/pairing/claim", json={"code": code}).status_code == 400
    # The paired phone can read the same encrypted dictionary as the browser.
    client.post("/v1/me/dictionary", headers=auth(web), json={"term": "Hargeisa", "aliases": ["hargaysa"]})
    prof = client.get("/v1/me/profile", headers=auth(dev)).json()
    assert prof["dictionary"][0]["term"] == "Hargeisa"


def test_device_token_cannot_create_pairing_codes(client):
    dev = device_token(client)
    assert client.post("/v1/pairing/codes", headers=auth(dev)).status_code == 403


def test_settings_validation_and_version_bump(client):
    dev = device_token(client)
    v1 = client.get("/v1/me/profile", headers=auth(dev)).json()["version"]
    r = client.patch("/v1/me/settings", headers=auth(dev), json={"input_lang": "so", "mode": "translate"})
    assert r.status_code == 200 and r.json()["settings"]["input_lang"] == "so"
    assert r.json()["version"] > v1
    assert client.patch("/v1/me/settings", headers=auth(dev), json={"input_lang": "fr"}).status_code == 400


def test_personal_dictionary_encrypted_at_rest(client):
    dev = device_token(client)
    client.post("/v1/me/dictionary", headers=auth(dev), json={"term": "Mogadishu Clinic", "aliases": []})
    db = sqlite3.connect(client.st.store.path)
    blob = db.execute("SELECT dictionary_blob FROM users WHERE username='amina'").fetchone()[0]
    assert b"Mogadishu" not in blob


def test_users_cannot_see_each_other(client):
    a = device_token(client, "amina", "amina-pass-1")
    o = device_token(client, "omar", "omar-pass-12")
    client.post("/v1/me/dictionary", headers=auth(a), json={"term": "PrivateTerm", "aliases": []})
    assert client.get("/v1/me/profile", headers=auth(o)).json()["dictionary"] == []
    ids = [d["id"] for d in client.get("/v1/me/devices", headers=auth(a)).json()["items"]]
    assert client.delete(f"/v1/me/devices/{ids[0]}", headers=auth(o)).status_code == 404


def test_admin_requires_admin_role(client):
    tok = web_token(client, "amina", "amina-pass-1")
    assert client.get("/v1/admin/overview", headers=auth(tok)).status_code == 403
    dev = device_token(client, "admin", "admin-pass-1")
    assert client.get("/v1/admin/overview", headers=auth(dev)).status_code == 403  # browser session only


def test_admin_overview_and_user_management(client):
    adm = web_token(client)
    ov = client.get("/v1/admin/overview", headers=auth(adm)).json()
    assert ov["stages"][0]["name"] == "asr" and "system" in ov
    r = client.post("/v1/admin/users", headers=auth(adm), json={"username": "faisal", "password": "faisal-pass-1",
                                                                 "daily_limit_minutes": 30})
    assert r.status_code == 201 and r.json()["daily_limit_minutes"] == 30
    uid = r.json()["id"]
    assert client.patch(f"/v1/admin/users/{uid}", headers=auth(adm), json={"disabled": True}).json()["disabled"]
    assert client.post("/v1/auth/login", json={"username": "faisal", "password": "faisal-pass-1"}).status_code == 401
    assert any(a["action"] == "user.create" for a in client.get("/v1/admin/audit", headers=auth(adm)).json()["items"])


def test_cannot_remove_last_admin(client):
    adm = web_token(client)
    me = [u for u in client.get("/v1/admin/users", headers=auth(adm)).json()["items"] if u["username"] == "admin"][0]
    assert client.patch(f"/v1/admin/users/{me['id']}", headers=auth(adm), json={"role": "user"}).status_code == 409


def test_admin_reset_clears_encrypted_data_and_devices(client):
    dev = device_token(client)
    client.post("/v1/me/dictionary", headers=auth(dev), json={"term": "Secret", "aliases": []})
    adm = web_token(client)
    uid = client.st.store.user_by_name("amina")["id"]
    client.patch(f"/v1/admin/users/{uid}", headers=auth(adm), json={"new_password": "brand-new-pass"})
    assert client.get("/v1/me/profile", headers=auth(dev)).status_code == 401
    new = device_token(client, "amina", "brand-new-pass")
    assert client.get("/v1/me/profile", headers=auth(new)).json()["dictionary"] == []


def test_admin_api_exposes_no_transcript_routes(client):
    paths = list(client.app.openapi()["paths"])
    admin_paths = [p for p in paths if p.startswith("/v1/admin")]
    assert admin_paths and not any("history" in p or "transcript" in p for p in admin_paths)


def test_org_dictionary_reaches_users(client):
    adm = web_token(client)
    client.post("/v1/admin/org-dictionary", headers=auth(adm), json={"term": "Proxmox", "aliases": ["prox mocks"]})
    dev = device_token(client)
    assert client.get("/v1/me/profile", headers=auth(dev)).json()["org_dictionary"][0]["term"] == "Proxmox"
