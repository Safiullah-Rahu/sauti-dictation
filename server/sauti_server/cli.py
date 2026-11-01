"""Command line: run the server, manage users, back up the database.

    sauti-server run --config /etc/sauti/sauti.toml
    sauti-server create-user --config ... --admin alice
    sauti-server backup --config ... --dest /var/backups/sauti
"""
from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

from .config import load_config


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    pw = getpass.getpass("Password (min 8 chars): ")
    if pw != getpass.getpass("Repeat: "):
        sys.exit("Passwords do not match.")
    return pw


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sauti-server")
    ap.add_argument("--config", help="TOML config (default: $SAUTI_CONFIG)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="start the HTTP/WebSocket server")
    run.add_argument("--host")
    run.add_argument("--port", type=int)

    cu = sub.add_parser("create-user", help="create an account")
    cu.add_argument("username")
    cu.add_argument("--display-name", default="")
    cu.add_argument("--admin", action="store_true")
    cu.add_argument("--password-stdin", action="store_true")

    rp = sub.add_parser("reset-password", help="reset a password (deletes that user's encrypted data)")
    rp.add_argument("username")
    rp.add_argument("--password-stdin", action="store_true")

    bk = sub.add_parser("backup", help="consistent copy of the database while running")
    bk.add_argument("--dest", required=True)

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    if args.cmd == "run":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(cfg), host=args.host or cfg.server.host, port=args.port or cfg.server.port,
                    log_level=cfg.server.log_level, proxy_headers=cfg.server.trust_proxy, ws_ping_interval=20,
                    ws_ping_timeout=20)
        return

    from .db import Store

    store = Store(cfg.data_path / "sauti.db")
    if args.cmd == "create-user":
        if store.user_by_name(args.username):
            sys.exit(f"User {args.username!r} already exists.")
        pw = _password(args)
        if len(pw) < 8:
            sys.exit("Password must be at least 8 characters.")
        uid = store.create_user(args.username, args.display_name or args.username, pw,
                                "admin" if args.admin else "user", cfg.privacy.default_history,
                                cfg.privacy.default_retention_days)
        store.audit(None, "user.create", f"{uid}:{args.username}:cli")
        print(f"Created {'admin' if args.admin else 'user'} {args.username!r} (id {uid}).")
    elif args.cmd == "reset-password":
        row = store.user_by_name(args.username)
        if row is None:
            sys.exit("No such user.")
        store.admin_reset_password(row["id"], _password(args))
        store.audit(None, "user.reset_password", f"{row['id']}:cli")
        print("Password reset. The user's encrypted history and dictionary were cleared and devices signed out.")
    elif args.cmd == "backup":
        dest = Path(args.dest)
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / time.strftime("sauti-%Y%m%d-%H%M%S.db")
        store.backup_to(out)
        print(out)


if __name__ == "__main__":
    main()
