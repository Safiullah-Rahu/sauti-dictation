"""SQLite storage. One file, WAL mode, a single connection guarded by a lock.

At the target scale (a handful of concurrent users) SQLite is simpler to back up
and restore than a database server, and every write here is small.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import security as sec

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    pw_hash TEXT NOT NULL,
    kdf_salt BLOB NOT NULL,
    dek_pw BLOB NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    input_lang TEXT NOT NULL DEFAULT 'auto',
    output_lang TEXT NOT NULL DEFAULT 'en',
    mode TEXT NOT NULL DEFAULT 'clean',
    style TEXT NOT NULL DEFAULT 'neutral',
    history_enabled INTEGER NOT NULL DEFAULT 0,
    retention_days INTEGER NOT NULL DEFAULT 30,
    profile_version INTEGER NOT NULL DEFAULT 1,
    daily_limit_minutes REAL,
    dictionary_blob BLOB
);
CREATE TABLE IF NOT EXISTS tokens (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    token_hash BLOB NOT NULL UNIQUE,
    dek_wrapped BLOB NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    platform TEXT NOT NULL DEFAULT 'other',
    client TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    last_seen REAL,
    expires_at REAL,
    revoked INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS tokens_user ON tokens(user_id, kind);
CREATE TABLE IF NOT EXISTS org_dictionary (
    id INTEGER PRIMARY KEY,
    term TEXT NOT NULL,
    aliases TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    mode TEXT NOT NULL,
    lang TEXT,
    target_lang TEXT,
    blob BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS history_user ON history(user_id, id);
CREATE INDEX IF NOT EXISTS history_expiry ON history(expires_at);
CREATE TABLE IF NOT EXISTS usage (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    audio_s REAL NOT NULL DEFAULT 0,
    utterances INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    code TEXT NOT NULL,
    stage TEXT,
    user_id INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    actor_id INTEGER,
    action TEXT NOT NULL,
    target TEXT
);
"""

USER_PUBLIC = ("id", "username", "display_name", "role")
SETTINGS_FIELDS = ("input_lang", "output_lang", "mode", "style", "history_enabled", "retention_days")


@dataclass
class Principal:
    """An authenticated caller: a user acting through one token, with their data key unlocked."""

    user_id: int
    username: str
    display_name: str
    role: str
    token_id: int
    kind: str  # device | web
    device_name: str
    platform: str
    dek: bytes

    @property
    def public_user(self) -> dict[str, Any]:
        return {"id": self.user_id, "username": self.username, "display_name": self.display_name, "role": self.role}


def _day(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts or time.time()))


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA synchronous=NORMAL")
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            self._db.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('org_version', '1')")

    # -- low level ---------------------------------------------------------
    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    def run(self, sql: str, args: tuple = ()) -> int:
        """Execute a statement; returns the number of rows changed."""
        with self._lock:
            return self._db.execute(sql, args).rowcount

    def insert(self, sql: str, args: tuple = ()) -> int:
        """Execute an INSERT; returns the new row id."""
        with self._lock:
            return int(self._db.execute(sql, args).lastrowid)

    def tx(self):
        return _Tx(self)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def backup_to(self, dest: Path) -> None:
        """Consistent online copy (safe while the server is running)."""
        with self._lock:
            out = sqlite3.connect(str(dest))
            try:
                self._db.backup(out)
            finally:
                out.close()

    # -- users -------------------------------------------------------------
    def create_user(self, username: str, display_name: str, password: str, role: str = "user",
                    history_default: bool = False, retention_days: int = 30) -> int:
        salt = sec.new_salt()
        dek = sec.new_dek()
        with self.tx():
            uid = self.insert(
                "INSERT INTO users(username, display_name, role, pw_hash, kdf_salt, dek_pw, created_at,"
                " history_enabled, retention_days) VALUES (?,?,?,?,?,?,?,?,?)",
                (username.strip(), display_name.strip() or username.strip(), role, sec.hash_password(password),
                 salt, b"pending", time.time(), int(history_default), retention_days),
            )
            self.run("UPDATE users SET dek_pw=? WHERE id=?",
                     (sec.wrap_dek(sec.password_kek(password, salt), dek, uid), uid))
        return uid

    def user(self, user_id: int) -> sqlite3.Row | None:
        return self.one("SELECT * FROM users WHERE id=?", (user_id,))

    def user_by_name(self, username: str) -> sqlite3.Row | None:
        return self.one("SELECT * FROM users WHERE username=?", (username.strip(),))

    def list_users(self) -> list[sqlite3.Row]:
        return self.q("SELECT * FROM users ORDER BY id")

    def count_admins(self) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=0")
        return int(row["n"]) if row else 0

    def authenticate(self, username: str, password: str) -> tuple[sqlite3.Row, bytes] | None:
        row = self.user_by_name(username)
        if row is None:
            sec.verify_password(sec.hash_password("timing-equaliser"), password)
            return None
        if row["disabled"] or not sec.verify_password(row["pw_hash"], password):
            return None
        dek = sec.unwrap_dek(sec.password_kek(password, row["kdf_salt"]), row["dek_pw"], row["id"])
        return row, dek

    def change_password(self, user_id: int, dek: bytes, new_password: str) -> None:
        salt = sec.new_salt()
        self.run("UPDATE users SET pw_hash=?, kdf_salt=?, dek_pw=? WHERE id=?",
                 (sec.hash_password(new_password), salt,
                  sec.wrap_dek(sec.password_kek(new_password, salt), dek, user_id), user_id))

    def admin_reset_password(self, user_id: int, new_password: str) -> None:
        """An administrator cannot unwrap the old data key, so a reset starts a fresh one.

        Encrypted personal data (dictionary, history) becomes unreadable and is deleted,
        and every token is revoked because each one wraps the old key.
        """
        salt = sec.new_salt()
        dek = sec.new_dek()
        with self.tx():
            self.run("UPDATE users SET pw_hash=?, kdf_salt=?, dek_pw=?, dictionary_blob=NULL,"
                     " profile_version=profile_version+1 WHERE id=?",
                     (sec.hash_password(new_password), salt,
                      sec.wrap_dek(sec.password_kek(new_password, salt), dek, user_id), user_id))
            self.run("DELETE FROM history WHERE user_id=?", (user_id,))
            self.run("UPDATE tokens SET revoked=1 WHERE user_id=?", (user_id,))

    def update_user_admin(self, user_id: int, **fields: Any) -> None:
        allowed = {"display_name", "role", "disabled", "daily_limit_minutes"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        self.run(f"UPDATE users SET {cols} WHERE id=?", (*sets.values(), user_id))
        if sets.get("disabled"):
            self.run("UPDATE tokens SET revoked=1 WHERE user_id=?", (user_id,))

    def delete_user(self, user_id: int) -> None:
        self.run("DELETE FROM users WHERE id=?", (user_id,))

    # -- settings & dictionary ----------------------------------------------
    def update_settings(self, user_id: int, values: dict[str, Any]) -> None:
        sets = {k: v for k, v in values.items() if k in SETTINGS_FIELDS}
        if not sets:
            return
        cols = ", ".join(f"{k}=?" for k in sets)
        self.run(f"UPDATE users SET {cols}, profile_version=profile_version+1 WHERE id=?",
                 (*sets.values(), user_id))

    def dictionary(self, user_id: int, dek: bytes) -> list[dict[str, Any]]:
        row = self.one("SELECT dictionary_blob FROM users WHERE id=?", (user_id,))
        if not row or not row["dictionary_blob"]:
            return []
        return json.loads(sec.unseal(dek, row["dictionary_blob"], b"dict:%d" % user_id))

    def save_dictionary(self, user_id: int, dek: bytes, entries: list[dict[str, Any]]) -> None:
        blob = sec.seal(dek, json.dumps(entries, ensure_ascii=False).encode(), b"dict:%d" % user_id)
        self.run("UPDATE users SET dictionary_blob=?, profile_version=profile_version+1 WHERE id=?",
                 (blob, user_id))

    def org_dictionary(self) -> list[dict[str, Any]]:
        return [{"id": r["id"], "term": r["term"], "aliases": json.loads(r["aliases"])}
                for r in self.q("SELECT * FROM org_dictionary ORDER BY term COLLATE NOCASE")]

    def org_version(self) -> int:
        row = self.one("SELECT value FROM meta WHERE key='org_version'")
        return int(row["value"]) if row else 1

    def add_org_term(self, term: str, aliases: list[str]) -> int:
        with self.tx():
            tid = self.insert("INSERT INTO org_dictionary(term, aliases, created_at) VALUES (?,?,?)",
                           (term.strip(), json.dumps(aliases, ensure_ascii=False), time.time()))
            self.run("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='org_version'")
        return tid

    def delete_org_term(self, term_id: int) -> bool:
        with self.tx():
            n = self.run("DELETE FROM org_dictionary WHERE id=?", (term_id,))
            self.run("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='org_version'")
        return bool(n)

    # -- tokens ---------------------------------------------------------------
    def create_token(self, user_id: int, kind: str, dek: bytes, name: str = "", platform: str = "other",
                     client: str = "", ttl_s: float | None = None) -> tuple[int, str]:
        token = sec.new_token(sec.DEVICE_PREFIX if kind == "device" else sec.WEB_PREFIX)
        now = time.time()
        tid = self.insert(
            "INSERT INTO tokens(user_id, kind, token_hash, dek_wrapped, name, platform, client, created_at,"
            " last_seen, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, kind, sec.token_hash(token), sec.wrap_dek(sec.token_kek(token), dek, user_id),
             name[:80], platform, client[:80], now, now, now + ttl_s if ttl_s else None),
        )
        return tid, token

    def resolve_token(self, token: str) -> Principal | None:
        if not token or not token.startswith((sec.DEVICE_PREFIX, sec.WEB_PREFIX)):
            return None
        row = self.one(
            "SELECT t.*, u.username, u.display_name, u.role, u.disabled FROM tokens t"
            " JOIN users u ON u.id=t.user_id WHERE t.token_hash=?", (sec.token_hash(token),))
        if row is None or row["revoked"] or row["disabled"]:
            return None
        if row["expires_at"] and row["expires_at"] < time.time():
            return None
        try:
            dek = sec.unwrap_dek(sec.token_kek(token), row["dek_wrapped"], row["user_id"])
        except Exception:
            return None
        if row["last_seen"] is None or time.time() - row["last_seen"] > 60:
            self.run("UPDATE tokens SET last_seen=? WHERE id=?", (time.time(), row["id"]))
        return Principal(row["user_id"], row["username"], row["display_name"], row["role"], row["id"],
                         row["kind"], row["name"], row["platform"], dek)

    def revoke_token(self, token_id: int, user_id: int | None = None) -> bool:
        if user_id is None:
            return bool(self.run("UPDATE tokens SET revoked=1 WHERE id=? AND revoked=0", (token_id,)))
        return bool(self.run("UPDATE tokens SET revoked=1 WHERE id=? AND user_id=? AND revoked=0",
                             (token_id, user_id)))

    def devices(self, user_id: int | None = None) -> list[sqlite3.Row]:
        sql = ("SELECT t.id, t.user_id, t.name, t.platform, t.client, t.created_at, t.last_seen, u.username"
               " FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.kind='device' AND t.revoked=0")
        if user_id is not None:
            return self.q(sql + " AND t.user_id=? ORDER BY t.id", (user_id,))
        return self.q(sql + " ORDER BY t.id")

    def device_count(self, user_id: int) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM tokens WHERE user_id=? AND kind='device' AND revoked=0",
                       (user_id,))
        return int(row["n"]) if row else 0

    def purge_tokens(self) -> None:
        self.run("DELETE FROM tokens WHERE revoked=1 OR (expires_at IS NOT NULL AND expires_at < ?)",
                 (time.time() - 86400,))

    # -- history --------------------------------------------------------------
    def add_history(self, user_id: int, dek: bytes, mode: str, lang: str | None, target_lang: str | None,
                    text: str, raw: str, retention_days: int) -> int:
        now = time.time()
        blob = sec.seal(dek, json.dumps({"text": text, "raw": raw}, ensure_ascii=False).encode(),
                        b"hist:%d" % user_id)
        return self.insert("INSERT INTO history(user_id, created_at, expires_at, mode, lang, target_lang, blob)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (user_id, now, now + retention_days * 86400, mode, lang, target_lang, blob))

    def history(self, user_id: int, dek: bytes, limit: int = 50, before: int | None = None) -> list[dict]:
        sql = "SELECT * FROM history WHERE user_id=? AND expires_at>?"
        args: list[Any] = [user_id, time.time()]
        if before:
            sql += " AND id<?"
            args.append(before)
        rows = self.q(sql + " ORDER BY id DESC LIMIT ?", (*args, max(1, min(limit, 200))))
        out = []
        for r in rows:
            body = json.loads(sec.unseal(dek, r["blob"], b"hist:%d" % user_id))
            out.append({"id": r["id"], "created_at": r["created_at"], "mode": r["mode"], "lang": r["lang"],
                        "target_lang": r["target_lang"], "text": body["text"], "raw": body["raw"]})
        return out

    def delete_history(self, user_id: int, item_id: int | None = None) -> int:
        if item_id is None:
            return self.run("DELETE FROM history WHERE user_id=?", (user_id,))
        return self.run("DELETE FROM history WHERE user_id=? AND id=?", (user_id, item_id))

    def history_counts(self) -> dict[int, int]:
        return {r["user_id"]: r["n"] for r in self.q("SELECT user_id, COUNT(*) AS n FROM history GROUP BY user_id")}

    def purge_expired_history(self) -> int:
        return self.run("DELETE FROM history WHERE expires_at<=?", (time.time(),))

    # -- usage, events, audit -------------------------------------------------
    def add_usage(self, user_id: int, audio_s: float, utterances: int = 1, errors: int = 0) -> None:
        self.run("INSERT INTO usage(user_id, day, audio_s, utterances, errors) VALUES (?,?,?,?,?)"
                 " ON CONFLICT(user_id, day) DO UPDATE SET audio_s=audio_s+excluded.audio_s,"
                 " utterances=utterances+excluded.utterances, errors=errors+excluded.errors",
                 (user_id, _day(), audio_s, utterances, errors))

    def usage_today_s(self, user_id: int) -> float:
        row = self.one("SELECT audio_s FROM usage WHERE user_id=? AND day=?", (user_id, _day()))
        return float(row["audio_s"]) if row else 0.0

    def usage_report(self, days: int = 14) -> list[sqlite3.Row]:
        since = _day(time.time() - days * 86400)
        return self.q("SELECT u.day, u.user_id, us.username, u.audio_s, u.utterances, u.errors FROM usage u"
                      " JOIN users us ON us.id=u.user_id WHERE u.day>=? ORDER BY u.day DESC, us.username",
                      (since,))

    def add_event(self, kind: str, code: str, stage: str | None = None, user_id: int | None = None,
                  detail: str = "") -> None:
        self.run("INSERT INTO events(ts, kind, code, stage, user_id, detail) VALUES (?,?,?,?,?,?)",
                 (time.time(), kind, code, stage, user_id, detail[:300]))

    def events(self, limit: int = 100, since: float | None = None) -> list[sqlite3.Row]:
        return self.q("SELECT e.*, u.username FROM events e LEFT JOIN users u ON u.id=e.user_id"
                      " WHERE e.ts>=? ORDER BY e.id DESC LIMIT ?", (since or 0, limit))

    def event_counts(self, since: float) -> dict[str, int]:
        return {r["code"]: r["n"] for r in self.q(
            "SELECT code, COUNT(*) AS n FROM events WHERE ts>=? GROUP BY code", (since,))}

    def purge_events(self, keep_days: int = 30) -> None:
        self.run("DELETE FROM events WHERE ts<?", (time.time() - keep_days * 86400,))

    def audit(self, actor_id: int | None, action: str, target: str = "") -> None:
        self.run("INSERT INTO audit(ts, actor_id, action, target) VALUES (?,?,?,?)",
                 (time.time(), actor_id, action, target[:200]))

    def audit_log(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.q("SELECT a.*, u.username AS actor FROM audit a LEFT JOIN users u ON u.id=a.actor_id"
                      " ORDER BY a.id DESC LIMIT ?", (limit,))


class _Tx:
    def __init__(self, store: Store):
        self.store = store

    def __enter__(self):
        self.store._lock.acquire()
        self.store._db.execute("BEGIN")
        return self

    def __exit__(self, exc_type, *_):
        try:
            self.store._db.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.store._lock.release()
