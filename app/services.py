"""Domain helpers: settings + trend for a user."""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

# Reuse single-user trend math from repo root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trend import (  # noqa: E402
    DEFAULT_HALF_LIFE_DAYS,
    KCAL_PER_LB,
    compute_trend,
    summary as trend_summary,
)

from app.models import Photo, User, UserSetting, Weight

DEFAULTS = {
    "half_life_days": str(DEFAULT_HALF_LIFE_DAYS),
    "unit": "lb",
    "goal_weight": "",
    "height_in": "",
    "sex": "",
    "age": "",
    "athlete": "0",
}


def get_user_settings(db: Session, user_id: int) -> dict:
    rows = db.scalars(select(UserSetting).where(UserSetting.user_id == user_id)).all()
    out = dict(DEFAULTS)
    for r in rows:
        out[r.key] = r.value
    # typed convenience
    try:
        out["half_life_days"] = float(out.get("half_life_days") or DEFAULT_HALF_LIFE_DAYS)
    except ValueError:
        out["half_life_days"] = DEFAULT_HALF_LIFE_DAYS
    for k in ("goal_weight", "height_in", "age"):
        v = out.get(k) or ""
        if v == "":
            out[k] = None
        else:
            try:
                out[k] = float(v) if k != "age" else int(float(v))
            except ValueError:
                out[k] = None
    ath = str(out.get("athlete") or "0").lower()
    out["athlete"] = ath in ("1", "true", "yes", "on")
    out["kcal_per_lb"] = KCAL_PER_LB
    return out


def set_user_setting(db: Session, user_id: int, key: str, value: str) -> None:
    row = db.scalar(
        select(UserSetting).where(UserSetting.user_id == user_id, UserSetting.key == key)
    )
    if row:
        row.value = value
    else:
        db.add(UserSetting(user_id=user_id, key=key, value=value))


def load_user_trend(db: Session, user_id: int, half_life: float | None = None):
    settings = get_user_settings(db, user_id)
    if half_life is None:
        half_life = float(settings["half_life_days"])
    rows = db.scalars(
        select(Weight).where(Weight.user_id == user_id).order_by(Weight.logged_at, Weight.id)
    ).all()
    samples = []
    for r in rows:
        when = r.logged_at or (r.date + "T12:00:00+00:00")
        samples.append((r.id, when, r.weight, r.note, r.body_fat))
    points = compute_trend(samples, half_life_days=half_life)
    series = [p.to_dict() for p in points]
    summ = trend_summary(points)
    # BMI attach
    height_in = settings.get("height_in")
    weight = summ.get("trend") or summ.get("latest_weight")
    if height_in and weight:
        try:
            hi = float(height_in)
            bmi = (float(weight) / (hi * hi)) * 703.0
            summ["bmi"] = {"bmi": round(bmi, 1), "height_in": hi}
        except Exception:
            summ["bmi"] = None
    else:
        summ["bmi"] = None
    return series, summ, half_life, settings


def photo_series(db: Session, user_id: int) -> list[dict]:
    rows = db.scalars(
        select(Photo)
        .where(Photo.user_id == user_id)
        .order_by(Photo.date, Photo.id)
    ).all()
    out = []
    for r in rows:
        if r.appearance_score is None and r.bmi_point is None:
            continue
        out.append(
            {
                "id": r.id,
                "date": r.date,
                "bmi_point": r.bmi_point,
                "bmi_low": r.bmi_low,
                "bmi_high": r.bmi_high,
                "appearance_score": r.appearance_score,
            }
        )
    return out


def photo_to_dict(p: Photo) -> dict:
    return {
        "id": p.id,
        "date": p.date,
        "note": p.note,
        "mime": p.mime,
        "bmi_point": p.bmi_point,
        "bmi_low": p.bmi_low,
        "bmi_high": p.bmi_high,
        "bmi_confidence": p.bmi_confidence,
        "appearance_score": p.appearance_score,
        "appearance_justification": p.appearance_justification,
        "confidence_overall": p.confidence_overall,
        "model": p.model,
        "has_projection": bool(p.projection_filename),
        "projection_url": f"/api/photos/{p.id}/projection" if p.projection_filename else None,
        "image_url": f"/api/photos/{p.id}/image",
        "projection_goal_lb": p.projection_goal_lb,
        "projection_model": p.projection_model,
    }
