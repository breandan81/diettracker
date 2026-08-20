"""FastAPI dependencies."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import IngestToken, User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.get(User, int(uid))
    if not user or not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("user_id")
    if not uid:
        return None
    user = db.get(User, int(uid))
    if not user or not user.is_active:
        return None
    return user


def _extract_ingest_token(request: Request) -> str | None:
    """Bearer token or X-Ingest-Token header (ESP32-friendly)."""
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
        if tok:
            return tok
    hdr = (request.headers.get("x-ingest-token") or "").strip()
    return hdr or None


def get_user_session_or_ingest(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Browser session cookie, or a per-user ingest token (ESP / automation)."""
    uid = request.session.get("user_id")
    if uid:
        user = db.get(User, int(uid))
        if user and user.is_active:
            return user
        request.session.clear()

    raw = _extract_ingest_token(request)
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated (sign in, or send Authorization: Bearer <ingest-token>)",
        )

    from app.auth_utils import hash_token

    row = db.scalar(
        select(IngestToken).where(
            IngestToken.token_hash == hash_token(raw),
            IngestToken.revoked.is_(False),
        )
    )
    if not row:
        raise HTTPException(status_code=401, detail="Invalid or revoked ingest token")
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive")
    row.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    from app.config import get_settings

    if user.id not in get_settings().admin_ids:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
