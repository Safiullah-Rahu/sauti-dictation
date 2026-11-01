"""Administrator API. Manages accounts, devices, sessions, limits and the shared
dictionary, and reports health. By design there is no endpoint that returns a
user's transcripts, personal dictionary or audio - and the server could not
decrypt stored history for an administrator even if one were added."""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .api_user import ApiError, TermBody, get_state, web_principal
from .db import Principal
from .monitor import system_snapshot


def admin(p: Principal = Depends(web_principal)) -> Principal:
    if p.role != "admin":
        raise ApiError(403, "forbidden", "Administrator access required.")
    return p


router = APIRouter(prefix="/v1/admin", dependencies=[Depends(admin)])


class NewUser(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[A-Za-z0-9._@-]+$")
    display_name: str = Field(default="", max_length=80)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field(default="user", pattern="^(user|admin)$")
    daily_limit_minutes: float | None = None


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    role: str | None = Field(default=None, pattern="^(user|admin)$")
    disabled: bool | None = None
    daily_limit_minutes: float | None = None
    clear_limit: bool = False
    new_password: str | None = Field(default=None, min_length=8, max_length=256)


def _user_row(st, r, history_counts: dict[int, int]) -> dict[str, Any]:
    return {"id": r["id"], "username": r["username"], "display_name": r["display_name"], "role": r["role"],
            "disabled": bool(r["disabled"]), "created_at": r["created_at"],
            "daily_limit_minutes": r["daily_limit_minutes"], "history_enabled": bool(r["history_enabled"]),
            "retention_days": r["retention_days"], "history_items": history_counts.get(r["id"], 0),
            "devices": st.store.device_count(r["id"]),
            "used_today_minutes": round(st.store.usage_today_s(r["id"]) / 60, 1)}


@router.get("/overview")
async def overview(request: Request):
    st = get_state(request)
    day_ago = time.time() - 86400
    usage_today = st.store.q("SELECT COALESCE(SUM(audio_s),0) AS s, COALESCE(SUM(utterances),0) AS n,"
                             " COALESCE(SUM(errors),0) AS e FROM usage WHERE day=?",
                             (time.strftime("%Y-%m-%d", time.gmtime()),))[0]
    return {
        "status": "ok" if st.llm_healthy is not False else "degraded",
        "models": {**st.pipeline.describe(),
                   "llm_healthy": st.llm_healthy},
        "system": system_snapshot(str(st.cfg.data_path)),
        "stages": [s.snapshot() for s in st.stages()],
        "sessions": {"active": len(st.sessions.sessions),
                     "recording": sum(1 for s in st.sessions.sessions.values() if s.state == "recording"),
                     "processing": sum(1 for s in st.sessions.sessions.values() if s.state == "processing")},
        "pending_results": sum(1 for e in st.inflight.values() if e.final is not None),
        "today": {"audio_minutes": round(usage_today["s"] / 60, 1), "utterances": usage_today["n"],
                  "errors": usage_today["e"]},
        "events_24h": st.store.event_counts(day_ago),
        "users": len(st.store.list_users()),
        "devices": len(st.store.devices()),
    }


@router.get("/users")
async def list_users(request: Request):
    st = get_state(request)
    counts = st.store.history_counts()
    return {"items": [_user_row(st, r, counts) for r in st.store.list_users()]}


@router.post("/users", status_code=201)
async def create_user(body: NewUser, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    if st.store.user_by_name(body.username):
        raise ApiError(409, "exists", "That username is taken.")
    uid = st.store.create_user(body.username, body.display_name or body.username, body.password, body.role,
                               st.cfg.privacy.default_history, st.cfg.privacy.default_retention_days)
    if body.daily_limit_minutes is not None:
        st.store.update_user_admin(uid, daily_limit_minutes=body.daily_limit_minutes)
    st.store.audit(p.user_id, "user.create", f"{uid}:{body.username}:{body.role}")
    return _user_row(st, st.store.user(uid), {})


@router.patch("/users/{user_id}")
async def patch_user(user_id: int, body: UserPatch, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    row = st.store.user(user_id)
    if row is None:
        raise ApiError(404, "not_found", "No such user.")
    changes = body.model_dump(exclude_none=True, exclude={"clear_limit", "new_password"})
    demoting = (changes.get("role") == "user" or changes.get("disabled")) and row["role"] == "admin"
    if demoting and st.store.count_admins() <= 1:
        raise ApiError(409, "last_admin", "Keep at least one active administrator.")
    if "disabled" in changes:
        changes["disabled"] = int(changes["disabled"])
    if body.clear_limit:
        changes["daily_limit_minutes"] = None
        st.store.run("UPDATE users SET daily_limit_minutes=NULL WHERE id=?", (user_id,))
        changes.pop("daily_limit_minutes")
    st.store.update_user_admin(user_id, **changes)
    if body.new_password:
        st.store.admin_reset_password(user_id, body.new_password)
        st.store.audit(p.user_id, "user.reset_password", str(user_id))
    if changes.get("disabled") or body.new_password:
        await st.sessions.close_where(lambda s: s.principal.user_id == user_id)
    if changes:
        st.store.audit(p.user_id, "user.update", f"{user_id}:{','.join(sorted(changes))}")
    return _user_row(st, st.store.user(user_id), st.store.history_counts())


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    row = st.store.user(user_id)
    if row is None:
        raise ApiError(404, "not_found", "No such user.")
    if row["role"] == "admin" and st.store.count_admins() <= 1:
        raise ApiError(409, "last_admin", "Keep at least one active administrator.")
    if user_id == p.user_id:
        raise ApiError(409, "self_delete", "You cannot delete your own account here.")
    await st.sessions.close_where(lambda s: s.principal.user_id == user_id)
    st.store.delete_user(user_id)
    st.store.audit(p.user_id, "user.delete", f"{user_id}:{row['username']}")
    return Response(status_code=204)


@router.get("/devices")
async def list_devices(request: Request):
    st = get_state(request)
    online = {s.principal.token_id for s in st.sessions.sessions.values()}
    return {"items": [{"id": r["id"], "user_id": r["user_id"], "username": r["username"], "name": r["name"],
                       "platform": r["platform"], "client": r["client"], "created_at": r["created_at"],
                       "last_seen": r["last_seen"], "online": r["id"] in online} for r in st.store.devices()]}


@router.delete("/devices/{device_id}", status_code=204)
async def revoke_device(device_id: int, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    if not st.store.revoke_token(device_id):
        raise ApiError(404, "not_found", "No such device.")
    await st.sessions.close_where(lambda s: s.principal.token_id == device_id)
    st.store.audit(p.user_id, "device.revoke", str(device_id))
    return Response(status_code=204)


@router.get("/sessions")
async def list_sessions(request: Request):
    return {"items": [s.public() for s in get_state(request).sessions.sessions.values()]}


@router.delete("/sessions/{session_id}", status_code=204)
async def end_session(session_id: str, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    n = await st.sessions.close_where(lambda s: s.id == session_id, code=4000, reason="ended by administrator")
    if not n:
        raise ApiError(404, "not_found", "No such session.")
    st.store.audit(p.user_id, "session.end", session_id)
    return Response(status_code=204)


@router.get("/usage")
async def usage(request: Request, days: int = 14):
    rows = get_state(request).store.usage_report(max(1, min(days, 90)))
    return {"items": [{"day": r["day"], "user_id": r["user_id"], "username": r["username"],
                       "audio_minutes": round(r["audio_s"] / 60, 2), "utterances": r["utterances"],
                       "errors": r["errors"]} for r in rows]}


@router.get("/org-dictionary")
async def org_dictionary(request: Request):
    return {"items": get_state(request).store.org_dictionary()}


@router.post("/org-dictionary", status_code=201)
async def add_org_term(body: TermBody, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    tid = st.store.add_org_term(body.term, [a.strip() for a in body.aliases if a.strip()])
    st.store.audit(p.user_id, "org_dictionary.add", body.term)
    version = st.store.org_version()
    for uid in {s.principal.user_id for s in st.sessions.sessions.values()}:
        row = st.store.user(uid)
        await st.sessions.notify_user(uid, {"type": "profile_changed", "version": row["profile_version"] + version})
    return {"id": tid, "term": body.term, "aliases": body.aliases}


@router.delete("/org-dictionary/{term_id}", status_code=204)
async def delete_org_term(term_id: int, request: Request, p: Principal = Depends(admin)):
    st = get_state(request)
    if not st.store.delete_org_term(term_id):
        raise ApiError(404, "not_found", "No such term.")
    st.store.audit(p.user_id, "org_dictionary.remove", str(term_id))
    return Response(status_code=204)


@router.get("/events")
async def events(request: Request, limit: int = 100):
    rows = get_state(request).store.events(max(1, min(limit, 500)))
    return {"items": [{"id": r["id"], "ts": r["ts"], "kind": r["kind"], "code": r["code"], "stage": r["stage"],
                       "user_id": r["user_id"], "username": r["username"], "detail": r["detail"]} for r in rows]}


@router.get("/audit")
async def audit(request: Request, limit: int = 100):
    rows = get_state(request).store.audit_log(max(1, min(limit, 500)))
    return {"items": [{"id": r["id"], "ts": r["ts"], "actor": r["actor"], "action": r["action"],
                       "target": r["target"]} for r in rows]}
