"""Daily AI quota defaults (env + optional AppConfig overrides)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AiUsageDaily, AppConfig

QUOTA_KEYS = {
    "coach": "daily_limit_coach",
    "vision": "daily_limit_vision",
    "imagine": "daily_limit_imagine",
}


def _env_defaults() -> dict[str, int]:
    s = get_settings()
    return {
        "coach": int(s.daily_limit_coach),
        "vision": int(s.daily_limit_vision),
        "imagine": int(s.daily_limit_imagine),
    }


def get_quota_limits(db: Session) -> dict[str, int]:
    limits = _env_defaults()
    for kind, cfg_key in QUOTA_KEYS.items():
        row = db.get(AppConfig, cfg_key)
        if row and str(row.value).strip().isdigit():
            limits[kind] = int(str(row.value).strip())
    return limits


def set_quota_limits(db: Session, limits: dict[str, int]) -> dict[str, int]:
    for kind, value in limits.items():
        if kind not in QUOTA_KEYS:
            continue
        key = QUOTA_KEYS[kind]
        row = db.get(AppConfig, key)
        if row:
            row.value = str(int(value))
        else:
            db.add(AppConfig(key=key, value=str(int(value))))
    db.commit()
    return get_quota_limits(db)


def usage_for_user(db: Session, user_id: int, day: date | None = None) -> dict[str, int]:
    day = day or datetime.now(timezone.utc).date()
    out = {"coach": 0, "vision": 0, "imagine": 0}
    rows = db.scalars(
        select(AiUsageDaily).where(AiUsageDaily.user_id == user_id, AiUsageDaily.day == day)
    ).all()
    for r in rows:
        if r.kind in out:
            out[r.kind] = int(r.count)
    return out


def reset_usage_for_user(db: Session, user_id: int, day: date | None = None) -> dict[str, int]:
    day = day or datetime.now(timezone.utc).date()
    rows = db.scalars(
        select(AiUsageDaily).where(AiUsageDaily.user_id == user_id, AiUsageDaily.day == day)
    ).all()
    for r in rows:
        db.delete(r)
    db.commit()
    return usage_for_user(db, user_id, day)


def increment_usage(db: Session, user_id: int, kind: str) -> int:
    """Increment and return new count. Caller enforces limits."""
    if kind not in QUOTA_KEYS:
        raise ValueError(f"unknown kind {kind}")
    day = datetime.now(timezone.utc).date()
    row = db.scalar(
        select(AiUsageDaily).where(
            AiUsageDaily.user_id == user_id,
            AiUsageDaily.day == day,
            AiUsageDaily.kind == kind,
        )
    )
    if not row:
        row = AiUsageDaily(user_id=user_id, day=day, kind=kind, count=0)
        db.add(row)
        db.flush()
    row.count = int(row.count or 0) + 1
    db.commit()
    return int(row.count)
