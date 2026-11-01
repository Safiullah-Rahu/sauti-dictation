"""REST API for users and devices: sign-in, pairing, profile, dictionary, history."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from . import __version__
from .config import LANGUAGES, MODES, STYLES
from .db import Principal
from .state import AppState


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


def get_state(request: Request) -> AppState:
    return request.app.state.sauti


def client_ip(request: Request) -> str:
    st = get_state(request)
    if st.cfg.server.trust_proxy and request.headers.get("x-forwarded-for"):
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "?"


def principal(request: Request) -> Principal:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    p = get_state(request).store.resolve_token(token)
    if p is None:
        raise ApiError(401, "unauthorized", "Sign in again.")
    return p


def web_principal(p: Principal = Depends(principal)) -> Principal:
    if p.kind != "web":
        raise ApiError(403, "forbidden", "This action needs a browser session.")
    return p


router = APIRouter(prefix="/v1")


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterBody(LoginBody):
    device_name: str = Field(default="", max_length=80)
    platform: str = "other"


class ClaimBody(BaseModel):
    code: str = Field(min_length=6, max_length=12)
    device_name: str = Field(default="", max_length=80)
    platform: str = "android"


class SettingsBody(BaseModel):
    input_lang: str | None = None
    output_lang: str | None = None
    mode: str | None = None
    style: str | None = None
    history_enabled: bool | None = None
    retention_days: int | None = None


class TermBody(BaseModel):
    term: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=10)


class PasswordBody(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


PLATFORMS = {"windows", "macos", "android", "web", "linux", "other"}


def _user_public(row) -> dict[str, Any]:
    return {"id": row["id"], "username": row["username"], "display_name": row["display_name"], "role": row["role"]}


def _authenticate(st: AppState, request: Request, username: str, password: str):
    if not st.login_limiter.allow(f"{client_ip(request)}:{username.lower()}"):
        raise ApiError(429, "rate_limited", "Too many attempts. Wait a minute and try again.")
    found = st.store.authenticate(username, password)
    if found is None:
        st.store.add_event("auth", "login_failed", None, None, "")
        raise ApiError(401, "invalid_credentials", "Wrong username or password.")
    return found


@router.get("/health")
async def health(request: Request):
    st = get_state(request)
    ok = st.llm_healthy is not False
    return {"status": "ok" if ok else "degraded", "version": __version__}


@router.post("/auth/login")
async def login(body: LoginBody, request: Request):
    st = get_state(request)
    row, dek = await asyncio.to_thread(_authenticate, st, request, body.username, body.password)
    ttl = 8 * 3600
    tid, token = st.store.create_token(row["id"], "web", dek, name="browser", platform="web", ttl_s=ttl)
    st.store.purge_tokens()
    expires = st.store.one("SELECT expires_at FROM tokens WHERE id=?", (tid,))["expires_at"]
    return {"token": token, "expires_at": expires, "user": _user_public(row)}


@router.post("/auth/logout", status_code=204)
async def logout(request: Request, p: Principal = Depends(principal)):
    get_state(request).store.revoke_token(p.token_id)
    return Response(status_code=204)


def _issue_device(st: AppState, user_row, dek: bytes, name: str, platform: str, client: str) -> dict[str, Any]:
    if st.store.device_count(user_row["id"]) >= st.cfg.limits.max_devices_per_user:
        raise ApiError(409, "too_many_devices", "Device limit reached. Remove an old device first.")
    platform = platform if platform in PLATFORMS else "other"
    tid, token = st.store.create_token(user_row["id"], "device", dek, name=name or f"{platform} device",
                                       platform=platform, client=client)
    st.store.audit(user_row["id"], "device.add", f"{tid}:{platform}")
    return {"device_id": tid, "device_token": token, "user": _user_public(user_row)}


@router.post("/devices/register", status_code=201)
async def register_device(body: RegisterBody, request: Request):
    st = get_state(request)
    row, dek = await asyncio.to_thread(_authenticate, st, request, body.username, body.password)
    return _issue_device(st, row, dek, body.device_name, body.platform, request.headers.get("user-agent", "")[:80])


@router.post("/pairing/codes", status_code=201)
async def create_pairing_code(request: Request, p: Principal = Depends(web_principal)):
    st = get_state(request)
    code, exp = st.pairing.create(p.user_id, p.dek)
    return {"code": code, "expires_at": exp, "server_url": st.cfg.server.public_url}


@router.post("/pairing/claim", status_code=201)
async def claim_pairing_code(body: ClaimBody, request: Request):
    st = get_state(request)
    if not st.pairing_limiter.allow(client_ip(request)):
        raise ApiError(429, "rate_limited", "Too many attempts. Wait a minute and try again.")
    entry = st.pairing.claim(body.code)
    if entry is None:
        st.store.add_event("auth", "pairing_failed", None, None, "")
        raise ApiError(400, "invalid_code", "That code is wrong or has expired. Create a new one.")
    row = st.store.user(entry.user_id)
    if row is None or row["disabled"]:
        raise ApiError(400, "invalid_code", "That code is wrong or has expired. Create a new one.")
    return _issue_device(st, row, entry.dek, body.device_name, body.platform, request.headers.get("user-agent", "")[:80])


def profile(st: AppState, p: Principal) -> dict[str, Any]:
    row = st.store.user(p.user_id)
    return {
        "version": int(row["profile_version"]) + st.store.org_version(),
        "user": _user_public(row),
        "settings": {"input_lang": row["input_lang"], "output_lang": row["output_lang"], "mode": row["mode"],
                     "style": row["style"], "history_enabled": bool(row["history_enabled"]),
                     "retention_days": row["retention_days"]},
        "dictionary": st.store.dictionary(p.user_id, p.dek),
        "org_dictionary": st.store.org_dictionary(),
        "languages": list(st.cfg.asr.languages),
        "modes": list(MODES),
        "styles": list(STYLES),
        "limits": {"daily_audio_minutes": row["daily_limit_minutes"] or st.cfg.limits.daily_audio_minutes,
                   "used_today_minutes": round(st.store.usage_today_s(p.user_id) / 60, 1),
                   "max_retention_days": st.cfg.privacy.max_retention_days},
    }


async def _changed(st: AppState, p: Principal) -> dict[str, Any]:
    prof = profile(st, p)
    await st.sessions.notify_user(p.user_id, {"type": "profile_changed", "version": prof["version"]})
    return prof


@router.get("/me")
async def me(request: Request, p: Principal = Depends(principal)):
    return {"user": p.public_user, "kind": p.kind, "device": p.device_name}


@router.get("/me/profile")
async def get_profile(request: Request, p: Principal = Depends(principal)):
    return profile(get_state(request), p)


@router.patch("/me/settings")
async def patch_settings(body: SettingsBody, request: Request, p: Principal = Depends(principal)):
    st = get_state(request)
    values = body.model_dump(exclude_none=True)
    if "input_lang" in values and values["input_lang"] != "auto" and values["input_lang"] not in LANGUAGES:
        raise ApiError(400, "bad_request", "Unknown input language.")
    if "output_lang" in values and values["output_lang"] not in LANGUAGES:
        raise ApiError(400, "bad_request", "Unknown output language.")
    if "mode" in values and values["mode"] not in MODES:
        raise ApiError(400, "bad_request", "Unknown mode.")
    if "style" in values and values["style"] not in STYLES:
        raise ApiError(400, "bad_request", "Unknown style.")
    if "retention_days" in values:
        values["retention_days"] = max(1, min(int(values["retention_days"]), st.cfg.privacy.max_retention_days))
    if "history_enabled" in values:
        values["history_enabled"] = int(values["history_enabled"])
        if not values["history_enabled"]:
            st.store.delete_history(p.user_id)  # turning history off also deletes what was kept
    st.store.update_settings(p.user_id, values)
    return await _changed(st, p)


@router.post("/me/dictionary", status_code=201)
async def add_term(body: TermBody, request: Request, p: Principal = Depends(principal)):
    st = get_state(request)
    entries = st.store.dictionary(p.user_id, p.dek)
    if len(entries) >= 500:
        raise ApiError(409, "dictionary_full", "Personal dictionary is limited to 500 terms.")
    next_id = max((e["id"] for e in entries), default=0) + 1
    entry = {"id": next_id, "term": body.term.strip(), "aliases": [a.strip() for a in body.aliases if a.strip()]}
    entries = [e for e in entries if e["term"].casefold() != entry["term"].casefold()] + [entry]
    st.store.save_dictionary(p.user_id, p.dek, entries)
    await _changed(st, p)
    return entry


@router.delete("/me/dictionary/{term_id}", status_code=204)
async def delete_term(term_id: int, request: Request, p: Principal = Depends(principal)):
    st = get_state(request)
    entries = st.store.dictionary(p.user_id, p.dek)
    st.store.save_dictionary(p.user_id, p.dek, [e for e in entries if e["id"] != term_id])
    await _changed(st, p)
    return Response(status_code=204)


@router.get("/me/history")
async def list_history(request: Request, limit: int = 50, before: int | None = None,
                       p: Principal = Depends(principal)):
    return {"items": get_state(request).store.history(p.user_id, p.dek, limit, before)}


@router.delete("/me/history/{item_id}", status_code=204)
async def delete_history_item(item_id: int, request: Request, p: Principal = Depends(principal)):
    get_state(request).store.delete_history(p.user_id, item_id)
    return Response(status_code=204)


@router.delete("/me/history", status_code=204)
async def delete_all_history(request: Request, p: Principal = Depends(principal)):
    get_state(request).store.delete_history(p.user_id)
    return Response(status_code=204)


@router.get("/me/devices")
async def list_devices(request: Request, p: Principal = Depends(principal)):
    rows = get_state(request).store.devices(p.user_id)
    return {"items": [{"id": r["id"], "name": r["name"], "platform": r["platform"], "created_at": r["created_at"],
                       "last_seen": r["last_seen"], "current": r["id"] == p.token_id} for r in rows]}


@router.delete("/me/devices/{device_id}", status_code=204)
async def delete_device(device_id: int, request: Request, p: Principal = Depends(principal)):
    st = get_state(request)
    if not st.store.revoke_token(device_id, p.user_id):
        raise ApiError(404, "not_found", "No such device.")
    st.store.audit(p.user_id, "device.remove", str(device_id))
    await st.sessions.close_where(lambda s: s.principal.token_id == device_id)
    return Response(status_code=204)


@router.post("/me/password", status_code=204)
async def change_password(body: PasswordBody, request: Request, p: Principal = Depends(web_principal)):
    st = get_state(request)
    await asyncio.to_thread(_authenticate, st, request, p.username, body.current_password)
    await asyncio.to_thread(st.store.change_password, p.user_id, p.dek, body.new_password)
    return Response(status_code=204)
