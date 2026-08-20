"""Authenticated data APIs (weights / settings / photos / trend)."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user
from app.models import Photo, User, UserSetting, Weight
from app.services import (
    get_user_settings,
    load_user_trend,
    photo_series,
    photo_to_dict,
    set_user_setting,
)
from trend import KCAL_PER_LB

router = APIRouter(tags=["data"])
settings = get_settings()


@router.get("/api/trend")
@router.get("/api/weights")
@router.get("/api/summary")
def trend(
    request_path: str = "",
    half_life_days: float | None = Query(None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    series, summ, half, sets = load_user_trend(db, user.id, half_life_days)
    # FastAPI doesn't give path easily without Request — return full trend payload
    return {
        "half_life_days": half,
        "kcal_per_lb": KCAL_PER_LB,
        "summary": summ,
        "series": series,
        "entries": series,
        "photo_series": photo_series(db, user.id),
        "settings": sets,
    }


@router.get("/api/settings")
def settings_get(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_user_settings(db, user.id)


class SettingsBody(BaseModel):
    half_life_days: float | None = None
    goal_weight: float | None = None
    height_in: float | None = None
    sex: str | None = None
    age: int | None = None
    athlete: bool | None = None


@router.put("/api/settings")
def settings_put(
    body: SettingsBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mapping = body.model_dump(exclude_unset=True)
    for k, v in mapping.items():
        if v is None and k in ("goal_weight", "height_in", "sex", "age"):
            set_user_setting(db, user.id, k, "")
        elif isinstance(v, bool):
            set_user_setting(db, user.id, k, "1" if v else "0")
        else:
            set_user_setting(db, user.id, k, str(v))
    db.commit()
    return {"settings": get_user_settings(db, user.id)}


class WeightBody(BaseModel):
    weight: float
    body_fat: float | None = None
    note: str | None = None
    logged_at: str | None = None
    date: str | None = None


@router.post("/api/weights")
def weights_create(
    body: WeightBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from datetime import datetime, timezone

    logged = body.logged_at
    if not logged:
        logged = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    d = body.date or logged[:10]
    row = Weight(
        user_id=user.id,
        date=d,
        logged_at=logged,
        weight=body.weight,
        body_fat=body.body_fat,
        note=body.note,
    )
    db.add(row)
    db.commit()
    series, summ, half, _ = load_user_trend(db, user.id)
    return {"ok": True, "id": row.id, "series": series, "summary": summ, "half_life_days": half}


@router.get("/api/photos")
def photos_list(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Photo).where(Photo.user_id == user.id).order_by(Photo.date.desc(), Photo.id.desc())
    ).all()
    return {"photos": [photo_to_dict(p) for p in rows]}


@router.get("/api/photos/series")
def photos_series(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"series": photo_series(db, user.id)}


@router.get("/api/photos/{pid}")
def photo_one(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    return {"photo": photo_to_dict(p)}


@router.get("/api/photos/{pid}/image")
def photo_image(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    path = settings.data_dir / "photos" / str(user.id) / p.filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path, media_type=p.mime or "image/jpeg")


@router.get("/api/photos/{pid}/projection")
def photo_projection(
    pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id or not p.projection_filename:
        raise HTTPException(status_code=404, detail="no projection")
    path = settings.data_dir / "photos" / str(user.id) / p.projection_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="file missing")
    return FileResponse(path, media_type=p.projection_mime or "image/jpeg")


@router.get("/api/coach/status")
def coach_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.quotas import get_quota_limits, usage_for_user

    limits = get_quota_limits(db)
    usage = usage_for_user(db, user.id)
    return {
        "ok": bool(settings.xai_api_key),
        "provider": "xai",
        "model": settings.xai_model,
        "configured": bool(settings.xai_api_key),
        "usage_today": usage,
        "limits": limits,
    }


@router.get("/api/vision/status")
def vision_status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.quotas import get_quota_limits, usage_for_user

    return {
        "ok": bool(settings.xai_api_key),
        "model": settings.xai_model,
        "configured": bool(settings.xai_api_key),
        "base_url": "https://api.x.ai/v1",
        "usage_today": usage_for_user(db, user.id),
        "limits": get_quota_limits(db),
    }


@router.get("/api/coach")
def coach_get(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    import json

    sets = get_user_settings(db, user.id)
    coach = None
    # last_coach_json may live in user settings from migration
    raw = None
    row = db.scalar(
        select(UserSetting).where(
            UserSetting.user_id == user.id, UserSetting.key == "last_coach_json"
        )
    )
    if row and row.value:
        try:
            coach = json.loads(row.value)
        except Exception:
            coach = None
    return {"coach": coach, "status": coach_status(user, db)}


class CoachBody(BaseModel):
    style: str | None = "pep"


@router.post("/api/coach")
def coach_post(
    body: CoachBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import json

    from app.coach_xai import generate_pep_xai
    from app.quotas import get_quota_limits, increment_usage, usage_for_user
    from app.config import get_settings as _gs

    style = (body.style or "pep").strip().lower()
    if style not in ("pep", "roast", "haiku", "brief"):
        style = "pep"

    limits = get_quota_limits(db)
    usage = usage_for_user(db, user.id)
    # Admins still count unless we want unlimited — plan said admin higher/unlimited
    is_admin = user.id in _gs().admin_ids
    if not is_admin and usage.get("coach", 0) >= limits.get("coach", 20):
        # Return last coach so the UI can keep showing it
        from fastapi.responses import JSONResponse

        last = None
        row = db.scalar(
            select(UserSetting).where(
                UserSetting.user_id == user.id, UserSetting.key == "last_coach_json"
            )
        )
        if row and row.value:
            try:
                last = json.loads(row.value)
            except Exception:
                last = None
        return JSONResponse(
            status_code=429,
            content={
                "error": (
                    f"Daily AI coach quota exceeded ({usage.get('coach', 0)}/{limits['coach']}). "
                    "Previous pep talk is unchanged — try again tomorrow."
                ),
                "detail": (
                    f"Daily AI coach quota exceeded ({usage.get('coach', 0)}/{limits['coach']}). "
                    "Previous pep talk is unchanged — try again tomorrow."
                ),
                "usage_today": usage,
                "limits": limits,
                "coach": last,
            },
        )

    series, summ, half, sets = load_user_trend(db, user.id)
    try:
        coach = generate_pep_xai(series, summ, sets, style=style)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    increment_usage(db, user.id, "coach")
    set_user_setting(db, user.id, "last_coach_json", json.dumps(coach))
    db.commit()
    return {
        "coach": coach,
        "status": coach_status(user, db),
        "usage_today": usage_for_user(db, user.id),
    }