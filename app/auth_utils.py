"""Password hashing and token helpers."""

from __future__ import annotations

import hashlib
import secrets

import bcrypt


def hash_password(password: str) -> str:
    raw = password.encode("utf-8")[:72]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        raw = password.encode("utf-8")[:72]
        return bcrypt.checkpw(raw, password_hash.encode("ascii"))
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_ingest_token() -> str:
    return secrets.token_urlsafe(32)
