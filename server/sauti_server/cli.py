"""Command line: run the server, manage users, back up the database.

    sauti-server run --config /etc/sauti/sauti.toml
    sauti-server create-user --config ... --admin alice
    sauti-server backup --config ... --dest /var/backups/sauti
    sauti-server doctor --config ...        # devices, models and services check
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

    sub.add_parser("doctor", help="check GPU/CPU, model paths and the LLM server")

    args = ap.parse_args(argv)
    cfg = load_config(args.config)

    if args.cmd == "run":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(cfg), host=args.host or cfg.server.host, port=args.port or cfg.server.port,
                    log_level=cfg.server.log_level, proxy_headers=cfg.server.trust_proxy, ws_ping_interval=20,
                    ws_ping_timeout=20)
        return

    if args.cmd == "doctor":
        sys.exit(doctor(cfg))

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


def doctor(cfg) -> int:
    """Print what the server would use, and return non-zero if something required is missing."""
    import json
    import urllib.request

    from . import devices

    problems = 0
    info = devices.describe()
    print("GPUs visible to CTranslate2:", info.get("cuda_devices", 0))
    for g in info.get("gpus", []):
        print(f"  - GPU {g['index']}: {g['name']}, {g['memory_used_mb']:.0f}/{g['memory_total_mb']:.0f} MB used")
    for name, section in (("speech recognition", cfg.asr), ("translation", cfg.translate)):
        if name == "translation" and cfg.translate.backend != "ct2":
            print(f"{name}: via {cfg.translate.backend}")
            continue
        try:
            dev = devices.resolve_device(section.device)
            print(f"{name}: device={dev} compute_type={devices.resolve_compute_type(section.compute_type, dev)}")
        except Exception as e:
            problems += 1
            print(f"{name}: PROBLEM - {e}")
    paths = [("asr.model", cfg.asr.model), ("asr.partial_model", cfg.asr.partial_model)]
    paths += [(f"asr.per_language.{k}", v) for k, v in cfg.asr.per_language.items()]
    if cfg.translate.backend == "ct2":
        paths.append(("translate.model_path", cfg.translate.model_path))
    for key, value in paths:
        if not value:
            continue
        local = Path(value).expanduser()
        if local.is_absolute() or value.startswith((".", "/")):
            ok = (local / "model.bin").exists()
            problems += 0 if ok else 1
            print(f"{key}: {value} {'ok' if ok else 'MISSING model.bin'}")
        else:
            print(f"{key}: {value} (downloaded by name on first start)")
    if cfg.llm.backend == "openai_compat":
        root = cfg.llm.base_url.rstrip("/").removesuffix("/v1")
        try:
            with urllib.request.urlopen(root + "/health", timeout=3) as r:
                print(f"llm: {cfg.llm.base_url} reachable ({json.loads(r.read() or b'{}').get('status', 'ok')})")
        except Exception as e:
            problems += 1
            print(f"llm: {cfg.llm.base_url} NOT reachable ({e.__class__.__name__}) - clean mode falls back to rules")
    print("OK" if not problems else f"{problems} problem(s) found")
    return 1 if problems else 0


if __name__ == "__main__":
    main()
