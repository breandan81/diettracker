"""Invite-only photo uploads until monetization / moderation exists."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AppConfig, User


def photos_feature_enabled(db: Session) -> bool:
    """Global kill switch (default on — per-user invite still required)."""
    row = db.get(AppConfig, "photos_feature_enabled")
    if row is None:
        return True
    return str(row.value).strip().lower() in ("1", "true", "yes", "on")


def set_photos_feature_enabled(db: Session, enabled: bool) -> bool:
    row = db.get(AppConfig, "photos_feature_enabled")
    val = "1" if enabled else "0"
    if row:
        row.value = val
    else:
        db.add(AppConfig(key="photos_feature_enabled", value=val))
    db.commit()
    return enabled


def user_can_upload_photos(db: Session, user: User) -> bool:
    if not photos_feature_enabled(db):
        return False
    # Admins always allowed (so you can test)
    if user.id in get_settings().admin_ids:
        return True
    return bool(user.photos_allowed)


def require_photos_allowed(db: Session, user: User) -> None:
    if user_can_upload_photos(db, user):
        return
    if not photos_feature_enabled(db):
        raise HTTPException(
            status_code=403,
            detail="Photo uploads are temporarily disabled by the administrator.",
        )
    raise HTTPException(
        status_code=403,
        detail=(
            "Photo uploads are invite-only until moderation is in place. "
            "Ask an admin for a photo invite code, or redeem one under Photos."
        ),
    )
