"""Authenticated data APIs (weights / settings / photos / trend)."""

from __future__ import annotations

from pathlib import Path

from datetime import date as date_cls

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_utils import hash_token, new_ingest_token
from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, get_user_session_or_ingest
from app.export_import import build_export_zip, import_export_zip
from app.models import IngestToken, Photo, User, UserSetting, Weight
from app.services import (
    get_user_settings,
    load_user_trend,
    photo_series,
    photo_to_dict,
    scale_profile_payload,
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
    user: User = Depends(get_user_session_or_ingest),
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


class WeightUpdateBody(BaseModel):
    weight: float | None = None
    body_fat: float | None = None
    note: str | None = None
    logged_at: str | None = None
    date: str | None = None


@router.put("/api/weights/{wid}")
def weights_update(
    wid: int,
    body: WeightUpdateBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(Weight, wid)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")

    data = body.model_dump(exclude_unset=True)
    if "weight" in data and data["weight"] is not None:
        w = float(data["weight"])
        if w <= 0 or w > 1000:
            raise HTTPException(status_code=400, detail="weight out of range")
        row.weight = w
    if "body_fat" in data:
        bf = data["body_fat"]
        row.body_fat = float(bf) if bf is not None and bf != "" else None
    if "note" in data:
        note = data["note"]
        row.note = (str(note)[:500] if note is not None else None) or None
    if data.get("logged_at") or data.get("date"):
        logged = (data.get("logged_at") or "").strip() or None
        if logged:
            row.logged_at = logged
            row.date = (data.get("date") or logged[:10])[:10]
        elif data.get("date"):
            row.date = str(data["date"])[:10]
            if not row.logged_at:
                row.logged_at = row.date + "T12:00:00+00:00"

    db.commit()
    series, summ, half, _ = load_user_trend(db, user.id)
    return {"ok": True, "id": row.id, "series": series, "summary": summ, "half_life_days": half}


@router.delete("/api/weights/{wid}")
def weights_delete(
    wid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(Weight, wid)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    db.delete(row)
    db.commit()
    series, summ, half, _ = load_user_trend(db, user.id)
    return {
        "ok": True,
        "deleted": wid,
        "series": series,
        "summary": summ,
        "half_life_days": half,
    }


@router.get("/api/scale-profile")
def scale_profile(
    user: User = Depends(get_user_session_or_ingest),
    db: Session = Depends(get_db),
):
    """Height/sex/age/athlete for ESP32 → Renpho on-device body fat."""
    return scale_profile_payload(db, user.id)


@router.get("/api/export.zip")
def export_zip(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download full account backup (settings, weights, photos + analysis) as ZIP."""
    import io

    data = build_export_zip(db, user, settings.data_dir)
    fname = f"trend-export-{date_cls.today().isoformat()}.zip"
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("/api/import")
async def import_zip(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Import a trend-export ZIP into the current account (upsert; no duplicates)."""
    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="file required (ZIP upload)")
    raw = await file.read()  # type: ignore[union-attr]
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > 200 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file too large (max 200MB)")
    try:
        summary = import_export_zip(db, user, settings.data_dir, raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    series, summ, half, _ = load_user_trend(db, user.id)
    return {
        "ok": True,
        "import": summary,
        "series": series,
        "summary": summ,
        "half_life_days": half,
        "settings": get_user_settings(db, user.id),
        "photo_series": photo_series(db, user.id),
    }


class IngestTokenCreate(BaseModel):
    label: str | None = Field(None, max_length=100)


@router.get("/api/ingest-tokens")
def ingest_tokens_list(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.scalars(
        select(IngestToken)
        .where(IngestToken.user_id == user.id, IngestToken.revoked.is_(False))
        .order_by(IngestToken.id.desc())
    ).all()
    return {
        "tokens": [
            {
                "id": t.id,
                "label": t.label,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "last_used_at": t.last_used_at.isoformat() if t.last_used_at else None,
            }
            for t in rows
        ]
    }


@router.post("/api/ingest-tokens")
def ingest_tokens_create(
    body: IngestTokenCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an ESP/automation token. Raw secret returned once."""
    existing = db.scalars(
        select(IngestToken).where(
            IngestToken.user_id == user.id, IngestToken.revoked.is_(False)
        )
    ).all()
    if len(existing) >= 5:
        raise HTTPException(status_code=400, detail="At most 5 active ingest tokens")

    raw = new_ingest_token()
    row = IngestToken(
        user_id=user.id,
        token_hash=hash_token(raw),
        label=(body.label or "").strip()[:100] or "ESP32",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "id": row.id,
        "label": row.label,
        "token": raw,
        "hint": "Copy now — it will not be shown again. Put it in ESP config.h as INGEST_TOKEN.",
    }


@router.delete("/api/ingest-tokens/{tid}")
def ingest_tokens_revoke(
    tid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = db.get(IngestToken, tid)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    row.revoked = True
    db.commit()
    return {"ok": True}


@router.get("/api/photos")
def photos_list(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.photos_access import photos_feature_enabled, user_can_upload_photos

    rows = db.scalars(
        select(Photo).where(Photo.user_id == user.id).order_by(Photo.date.desc(), Photo.id.desc())
    ).all()
    return {
        "photos": [photo_to_dict(p) for p in rows],
        "photos_allowed": user_can_upload_photos(db, user),
        "photos_feature_enabled": photos_feature_enabled(db),
    }


@router.post("/api/photos")
async def photos_create(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upload a progress photo. Analyzes with Grok by default (opt out with analyze=0)."""
    import uuid
    from datetime import date as date_cls

    from app.photos_access import require_photos_allowed

    require_photos_allowed(db, user)

    form = await request.form()
    file = form.get("file")
    if file is None or not hasattr(file, "read"):
        raise HTTPException(status_code=400, detail="file required")
    raw = await file.read()  # type: ignore[union-attr]
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")
    if len(raw) > 12 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file too large (max 12MB)")

    mime = getattr(file, "content_type", None) or "image/jpeg"
    if mime not in ("image/jpeg", "image/jpg", "image/png"):
        raise HTTPException(status_code=400, detail="only jpeg/png supported")

    d = str(form.get("date") or date_cls.today().isoformat())[:10]
    note = str(form.get("note") or "")[:200] or None
    # Default ON — only skip when client explicitly sends 0/false/off
    analyze_raw = str(form.get("analyze") if form.get("analyze") is not None else "1").strip().lower()
    want_analyze = analyze_raw not in ("0", "false", "no", "off")
    ext = ".png" if "png" in mime else ".jpg"
    fname = f"{d}_{uuid.uuid4().hex[:10]}{ext}"
    dest_dir = settings.data_dir / "photos" / str(user.id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / fname
    dest_path.write_bytes(raw)

    photo = Photo(
        user_id=user.id,
        date=d,
        filename=fname,
        mime=mime,
        note=note,
    )
    db.add(photo)
    db.commit()
    db.refresh(photo)

    analyze_note = "Stored privately."
    if want_analyze:
        try:
            _analyze_photo_row(db, user, photo)
            analyze_note = "Stored and analyzed with Grok."
        except HTTPException as e:
            analyze_note = f"Stored, but analyze skipped: {e.detail}"
        except Exception as e:
            analyze_note = f"Stored, but analyze failed: {e}"

    return {
        "ok": True,
        "photo": photo_to_dict(photo),
        "photos": [
            photo_to_dict(p)
            for p in db.scalars(
                select(Photo)
                .where(Photo.user_id == user.id)
                .order_by(Photo.date.desc(), Photo.id.desc())
            ).all()
        ],
        "photo_series": photo_series(db, user.id),
        "note": analyze_note,
    }


def _user_photos_payload(db: Session, user_id: int) -> dict:
    rows = db.scalars(
        select(Photo)
        .where(Photo.user_id == user_id)
        .order_by(Photo.date.desc(), Photo.id.desc())
    ).all()
    return {
        "photos": [photo_to_dict(p) for p in rows],
        "photo_series": photo_series(db, user_id),
    }


def _analyze_photo_row(db: Session, user: User, photo: Photo) -> Photo:
    """Run Grok vision on an existing photo row; enforces vision quota."""
    import json
    from datetime import datetime, timezone

    from app.photos_access import require_photos_allowed
    from app.quotas import get_quota_limits, increment_usage, usage_for_user
    from vision import analyze_image_file

    require_photos_allowed(db, user)

    if not settings.xai_api_key:
        raise HTTPException(status_code=503, detail="XAI_API_KEY not configured")

    limits = get_quota_limits(db)
    usage = usage_for_user(db, user.id)
    is_admin = user.id in settings.admin_ids
    if not is_admin and usage.get("vision", 0) >= limits.get("vision", 10):
        raise HTTPException(
            status_code=429,
            detail=f"Daily vision quota exceeded ({usage.get('vision', 0)}/{limits['vision']})",
        )

    path = settings.data_dir / "photos" / str(user.id) / photo.filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="photo file missing on server")

    from app.services import get_user_settings

    us = get_user_settings(db, user.id)
    sex = us.get("sex") or None
    age = us.get("age")
    try:
        age_i = int(age) if age is not None else None
    except (TypeError, ValueError):
        age_i = None
    analysis = analyze_image_file(
        path, photo.mime or "image/jpeg", sex=sex, age=age_i
    )
    meta = analysis.pop("_meta", {}) if isinstance(analysis, dict) else {}
    bmi = (analysis.get("bmi_estimate") or {}) if isinstance(analysis, dict) else {}
    app = (analysis.get("appearance_rating") or {}) if isinstance(analysis, dict) else {}

    photo.analysis_json = json.dumps(analysis)
    photo.bmi_point = bmi.get("point")
    photo.bmi_low = bmi.get("range_low")
    photo.bmi_high = bmi.get("range_high")
    photo.bmi_confidence = bmi.get("confidence")
    photo.appearance_score = app.get("score")
    photo.appearance_justification = app.get("justification")
    photo.confidence_overall = analysis.get("confidence_overall") if isinstance(analysis, dict) else None
    photo.model = meta.get("model")
    photo.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(photo)
    increment_usage(db, user.id, "vision")
    return photo


@router.get("/api/photos/series")
def photos_series(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return {"series": photo_series(db, user.id)}


@router.get("/api/photos/{pid}")
def photo_one(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    return {"photo": photo_to_dict(p)}


@router.delete("/api/photos/{pid}")
def photos_delete(
    pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    from app.photos_access import require_photos_allowed

    require_photos_allowed(db, user)
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    dest = settings.data_dir / "photos" / str(user.id)
    for name in (p.filename, p.projection_filename):
        if name:
            path = dest / name
            if path.is_file():
                path.unlink()
    db.delete(p)
    db.commit()
    return {"ok": True, "deleted": pid, **_user_photos_payload(db, user.id)}


@router.post("/api/photos/{pid}/analyze")
def photos_analyze(
    pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    _analyze_photo_row(db, user, p)
    return {"ok": True, "photo": photo_to_dict(p), **_user_photos_payload(db, user.id)}


@router.post("/api/photos/{pid}/project-goal")
def photos_project_goal(
    pid: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Grok Imagine preview at goal weight (no vision re-rate of the result)."""
    from datetime import datetime, timezone

    from app.photos_access import require_photos_allowed
    from app.quotas import get_quota_limits, increment_usage, usage_for_user
    from app.services import get_user_settings
    from imagine import edit_image_to_goal

    require_photos_allowed(db, user)
    p = db.get(Photo, pid)
    if not p or p.user_id != user.id:
        raise HTTPException(status_code=404, detail="not found")
    if not settings.xai_api_key:
        raise HTTPException(status_code=503, detail="XAI_API_KEY not configured")

    limits = get_quota_limits(db)
    usage = usage_for_user(db, user.id)
    is_admin = user.id in settings.admin_ids
    if not is_admin and usage.get("imagine", 0) >= limits.get("imagine", 5):
        raise HTTPException(
            status_code=429,
            detail=f"Daily Imagine quota exceeded ({usage.get('imagine', 0)}/{limits['imagine']})",
        )

    src = settings.data_dir / "photos" / str(user.id) / p.filename
    if not src.is_file():
        raise HTTPException(status_code=404, detail="photo file missing on server")

    sets = get_user_settings(db, user.id)
    goal_lb = sets.get("goal_weight")
    if goal_lb is None:
        raise HTTPException(status_code=400, detail="Set a goal weight in Settings first")
    height_in = sets.get("height_in")
    series, summ, _, _ = load_user_trend(db, user.id)
    current_lb = summ.get("trend") or summ.get("latest_weight")
    current_bmi = None
    goal_bmi = None
    if height_in and current_lb:
        try:
            hi = float(height_in)
            current_bmi = round((float(current_lb) / (hi * hi)) * 703.0, 1)
            goal_bmi = round((float(goal_lb) / (hi * hi)) * 703.0, 1)
        except Exception:
            pass

    notes = None
    if p.analysis_json:
        try:
            import json

            analysis = json.loads(p.analysis_json)
            obs = analysis.get("observations") or {}
            bits = [
                obs.get("overall_build"),
                obs.get("midsection"),
                (analysis.get("appearance_rating") or {}).get("justification"),
            ]
            notes = "; ".join(b for b in bits if b)
        except Exception:
            notes = None

    sex = sets.get("sex") or None
    age = sets.get("age")
    try:
        age_i = int(age) if age is not None else None
    except (TypeError, ValueError):
        age_i = None

    result = edit_image_to_goal(
        src.read_bytes(),
        p.mime or "image/jpeg",
        current_lb=float(current_lb) if current_lb is not None else None,
        goal_lb=float(goal_lb),
        current_bmi=current_bmi,
        goal_bmi=goal_bmi,
        appearance_notes=notes,
        sex=sex,
        age=age_i,
    )

    dest = settings.data_dir / "photos" / str(user.id)
    if p.projection_filename:
        old = dest / p.projection_filename
        if old.is_file():
            old.unlink()
    suffix = ".png" if "png" in (result.get("mime") or "") else ".jpg"
    fname = f"{p.date}_{p.id}_goal{suffix}"
    (dest / fname).write_bytes(result["bytes"])
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    p.projection_filename = fname
    p.projection_mime = result.get("mime") or "image/jpeg"
    p.projection_prompt = result.get("prompt")
    p.projection_model = result.get("model")
    p.projection_goal_lb = float(goal_lb)
    p.projection_created_at = now
    db.commit()
    db.refresh(p)
    increment_usage(db, user.id, "imagine")
    return {"ok": True, "photo": photo_to_dict(p), **_user_photos_payload(db, user.id)}


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