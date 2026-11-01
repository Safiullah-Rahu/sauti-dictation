"""Passwords, tokens and at-rest encryption of personal data.

Each user has a random 256-bit data key (DEK). Their personal dictionary and
transcript history are encrypted with it (AES-256-GCM). The DEK is never stored
in the clear: it is wrapped once with a key derived from the user's password and
once per device/web token with a key derived from that token. The server only
stores token hashes, so the database alone - or an administrator reading it -
cannot unwrap a DEK. While a user is connected the DEK sits in server memory,
which is what lets the server encrypt new history for them.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_ph = PasswordHasher()

DEVICE_PREFIX = "sdt_"
WEB_PREFIX = "sws_"


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(pw_hash: str, password: str) -> bool:
    try:
        return _ph.verify(pw_hash, password)
    except (VerificationError, InvalidHash):
        return False


def new_token(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def new_dek() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def new_salt() -> bytes:
    return os.urandom(16)


def password_kek(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def token_kek(token: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"sauti/token-kek/v1").derive(token.encode())


def seal(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def unseal(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad)


def wrap_dek(kek: bytes, dek: bytes, user_id: int) -> bytes:
    return seal(kek, dek, b"dek:%d" % user_id)


def unwrap_dek(kek: bytes, wrapped: bytes, user_id: int) -> bytes:
    return unseal(kek, wrapped, b"dek:%d" % user_id)


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
