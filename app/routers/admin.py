"""Admin-only APIs."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import require_admin
from app.models import Photo, User, Weight
from app.quotas import get_quota_limits, reset_usage_for_user, set_quota_limits, usage_for_user

router = APIRouter(prefix="/api/admin", tags=["admin"])
settings = get_settings()


class QuotasBody(BaseModel):
    coach: int | None = Field(None, ge=0, le=10000)
    vision: int | None = Field(None, ge=0, le=10000)
    imagine: int | None = Field(None, ge=0, le=10000)


class ActiveBody(BaseModel):
    is_active: bool


@router.get("/me")
def admin_me(admin: User = Depends(require_admin)):
    return {
        "ok": True,
        "admin": {"id": admin.id, "email": admin.email, "name": admin.name},
        "admin_ids": sorted(settings.admin_ids),
    }


@router.get("/users")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc).date()
    users = db.scalars(select(User).order_by(User.id)).all()
    weight_counts = dict(
        db.execute(select(Weight.user_id, func.count()).group_by(Weight.user_id)).all()
    )
    photo_counts = dict(
        db.execute(select(Photo.user_id, func.count()).group_by(Photo.user_id)).all()
    )
    out = []
    for u in users:
        usage = usage_for_user(db, u.id, today)
        out.append(
            {
                "id": u.id,
                "email": u.email,
                "name": u.name,
                "is_active": u.is_active,
                "is_admin": u.id in settings.admin_ids,
                "has_password": bool(u.password_hash),
                "has_google": bool(u.google_sub),
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "weights": int(weight_counts.get(u.id, 0)),
                "photos": int(photo_counts.get(u.id, 0)),
                "usage_today": usage,
            }
        )
    return {"users": out, "quotas": get_quota_limits(db), "day": today.isoformat()}


@router.post("/users/{user_id}/active")
def set_active(
    user_id: int,
    body: ActiveBody,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id and not body.is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
    if user.id in settings.admin_ids and not body.is_active:
        raise HTTPException(status_code=400, detail="Cannot deactivate an admin allowlist user")
    user.is_active = body.is_active
    db.commit()
    return {"ok": True, "id": user.id, "is_active": user.is_active}


@router.post("/users/{user_id}/reset-usage")
def reset_usage(
    user_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    usage = reset_usage_for_user(db, user_id)
    return {"ok": True, "id": user_id, "usage_today": usage}


@router.get("/quotas")
def quotas_get(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return {"quotas": get_quota_limits(db)}


@router.put("/quotas")
def quotas_put(
    body: QuotasBody,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    current = get_quota_limits(db)
    patch = body.model_dump(exclude_unset=True)
    current.update(patch)
    return {"quotas": set_quota_limits(db, current)}
