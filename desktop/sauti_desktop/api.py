"""Small REST client (standard library only)."""
from __future__ import annotations

import json
import platform
import ssl
import urllib.error
import urllib.request
from typing import Any

from . import __version__


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def ssl_context(ca_file: str = "") -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if ca_file:
        ctx.load_verify_locations(ca_file)
    return ctx


def platform_name() -> str:
    return {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}.get(platform.system(), "other")


class Api:
    def __init__(self, server_url: str, token: str | None = None, ca_file: str = ""):
        self.base = server_url.rstrip("/")
        self.token = token
        self.ctx = ssl_context(ca_file) if self.base.startswith("https") else None

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 10) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", f"sauti-desktop/{__version__} ({platform_name()})")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                raw = r.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read())["error"]
                raise ApiError(e.code, err.get("code", "error"), err.get("message", str(e))) from None
            except (ValueError, KeyError, TypeError):
                raise ApiError(e.code, "http_error", f"Server returned {e.code}") from None
        except urllib.error.URLError as e:
            raise ApiError(0, "unreachable", f"Cannot reach the server: {e.reason}") from None

    def register(self, username: str, password: str, device_name: str) -> dict:
        return self.request("POST", "/v1/devices/register", {"username": username, "password": password,
                                                            "device_name": device_name, "platform": platform_name()})

    def profile(self) -> dict:
        return self.request("GET", "/v1/me/profile")

    def patch_settings(self, **values: Any) -> dict:
        return self.request("PATCH", "/v1/me/settings", values)

    def remove_device(self, device_id: int) -> None:
        self.request("DELETE", f"/v1/me/devices/{device_id}")

    def ws_url(self) -> str:
        return ("wss://" + self.base[8:] if self.base.startswith("https://") else "ws://" + self.base[7:]) + "/v1/dictate"
