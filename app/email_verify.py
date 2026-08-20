"""Email verification token helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_utils import hash_token, new_ingest_token
from app.config import Settings
from app.mail import send_verification_email
from app.models import EmailVerifyToken, User

TOKEN_TTL = timedelta(hours=48)


def issue_and_send_verification(db: Session, user: User, settings: Settings) -> str:
    """Create a one-time token, email the link, return the raw token (tests only)."""
    # Invalidate unused prior tokens
    for row in db.scalars(
        select(EmailVerifyToken).where(
            EmailVerifyToken.user_id == user.id,
            EmailVerifyToken.used_at.is_(None),
        )
    ).all():
        row.used_at = datetime.now(timezone.utc)

    raw = new_ingest_token()  # url-safe 32 bytes
    now = datetime.now(timezone.utc)
    db.add(
        EmailVerifyToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            created_at=now,
            expires_at=now + TOKEN_TTL,
        )
    )
    db.commit()

    base = settings.public_base_url.rstrip("/")
    verify_url = f"{base}/api/auth/verify-email?token={raw}"
    send_verification_email(to=user.email, verify_url=verify_url, settings=settings)
    return raw


def consume_verification_token(db: Session, raw_token: str) -> User:
    from fastapi import HTTPException

    if not raw_token or len(raw_token) < 16:
        raise HTTPException(status_code=400, detail="Invalid verification link")
    row = db.scalar(
        select(EmailVerifyToken).where(EmailVerifyToken.token_hash == hash_token(raw_token))
    )
    if not row or row.used_at is not None:
        raise HTTPException(status_code=400, detail="Invalid or already used verification link")
    now = datetime.now(timezone.utc)
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        raise HTTPException(status_code=400, detail="Verification link expired — request a new one")
    user = db.get(User, row.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Account not found or disabled")
    user.email_verified = True
    row.used_at = now
    db.commit()
    return user
