"""User data ZIP export / import (trend-export v1).

Layout:
  trend-export/
    manifest.json
    data.json
    photos/<sha256>.<ext>
    photos/<sha256>_proj.<ext>   # optional projections
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Photo, User, UserSetting, Weight
from app.services import set_user_setting

FORMAT_NAME = "trend-export"
FORMAT_VERSION = 1

# Settings keys we never round-trip (secrets / ephemeral AI caches can stay)
SKIP_SETTING_KEYS = frozenset(
    {
        # nothing critical yet; last_coach_json is useful to keep
    }
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ext_for_mime(mime: str | None, fallback: str = "jpg") -> str:
    m = (mime or "").lower()
    if "png" in m:
        return "png"
    if "webp" in m:
        return "webp"
    if "jpeg" in m or "jpg" in m:
        return "jpg"
    return fallback


def _photo_path(data_dir: Path, user_id: int, filename: str) -> Path:
    return data_dir / "photos" / str(user_id) / filename


def build_export_zip(db: Session, user: User, data_dir: Path) -> bytes:
    """Build a trend-export ZIP in memory for one user."""
    settings_rows = db.scalars(
        select(UserSetting).where(UserSetting.user_id == user.id)
    ).all()
    settings: dict[str, str] = {}
    for r in settings_rows:
        if r.key in SKIP_SETTING_KEYS:
            continue
        settings[r.key] = r.value or ""

    weights_rows = db.scalars(
        select(Weight).where(Weight.user_id == user.id).order_by(Weight.logged_at, Weight.id)
    ).all()
    weights = [
        {
            "date": w.date,
            "logged_at": w.logged_at,
            "weight": w.weight,
            "body_fat": w.body_fat,
            "note": w.note,
        }
        for w in weights_rows
    ]

    photos_rows = db.scalars(
        select(Photo).where(Photo.user_id == user.id).order_by(Photo.date, Photo.id)
    ).all()

    buf = io.BytesIO()
    photos_meta: list[dict[str, Any]] = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in photos_rows:
            entry: dict[str, Any] = {
                "date": p.date,
                "note": p.note,
                "mime": p.mime,
                "analysis_json": p.analysis_json,
                "bmi_point": p.bmi_point,
                "bmi_low": p.bmi_low,
                "bmi_high": p.bmi_high,
                "bmi_confidence": p.bmi_confidence,
                "appearance_score": p.appearance_score,
                "appearance_justification": p.appearance_justification,
                "confidence_overall": p.confidence_overall,
                "model": p.model,
                "projection_prompt": p.projection_prompt,
                "projection_model": p.projection_model,
                "projection_goal_lb": p.projection_goal_lb,
                "projection_created_at": p.projection_created_at,
                "projection_mime": p.projection_mime,
            }
            src = _photo_path(data_dir, user.id, p.filename)
            if src.is_file():
                raw = src.read_bytes()
                digest = _sha256_bytes(raw)
                ext = _ext_for_mime(p.mime, src.suffix.lstrip(".") or "jpg")
                arc = f"trend-export/photos/{digest}.{ext}"
                zf.writestr(arc, raw)
                entry["file_sha256"] = digest
                entry["file_ext"] = ext
            else:
                entry["file_sha256"] = None
                entry["file_ext"] = None
                entry["file_missing"] = True

            if p.projection_filename:
                proj = _photo_path(data_dir, user.id, p.projection_filename)
                if proj.is_file():
                    praw = proj.read_bytes()
                    pdigest = _sha256_bytes(praw)
                    pext = _ext_for_mime(p.projection_mime, proj.suffix.lstrip(".") or "jpg")
                    parc = f"trend-export/photos/{pdigest}_proj.{pext}"
                    zf.writestr(parc, praw)
                    entry["projection_sha256"] = pdigest
                    entry["projection_ext"] = pext

            photos_meta.append(entry)

        data = {
            "settings": settings,
            "weights": weights,
            "photos": photos_meta,
        }
        manifest = {
            "format": FORMAT_NAME,
            "version": FORMAT_VERSION,
            "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "app": "τrend",
            "user_email": user.email,
            "counts": {
                "settings": len(settings),
                "weights": len(weights),
                "photos": len(photos_meta),
            },
        }
        zf.writestr(
            "trend-export/manifest.json",
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        )
        zf.writestr(
            "trend-export/data.json",
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        )

    return buf.getvalue()


def _find_weight(
    db: Session, user_id: int, item: dict[str, Any]
) -> Weight | None:
    logged = (item.get("logged_at") or "").strip() or None
    if logged:
        return db.scalar(
            select(Weight).where(Weight.user_id == user_id, Weight.logged_at == logged)
        )
    # Fallback composite key
    d = str(item.get("date") or "")[:10]
    try:
        w = float(item["weight"])
    except (KeyError, TypeError, ValueError):
        return None
    note = item.get("note")
    bf = item.get("body_fat")
    q = select(Weight).where(
        Weight.user_id == user_id,
        Weight.date == d,
        Weight.weight == w,
    )
    rows = list(db.scalars(q).all())
    for r in rows:
        if (r.note or None) == (note or None) and (r.body_fat == bf or (r.body_fat is None and bf is None)):
            return r
    # If multiple same date+weight with null notes, pick first
    return rows[0] if len(rows) == 1 else None


def _find_photo_by_sha(db: Session, user_id: int, data_dir: Path, sha: str) -> Photo | None:
    rows = db.scalars(select(Photo).where(Photo.user_id == user_id)).all()
    for p in rows:
        path = _photo_path(data_dir, user_id, p.filename)
        if not path.is_file():
            continue
        if _sha256_bytes(path.read_bytes()) == sha:
            return p
    return None


def _read_zip_member(zf: zipfile.ZipFile, names: list[str], *candidates: str) -> bytes | None:
    for c in candidates:
        if c in names:
            return zf.read(c)
    # Allow missing trend-export/ prefix
    for n in names:
        for c in candidates:
            if n.endswith("/" + c.split("/", 1)[-1]) or n == c.split("/", 1)[-1]:
                return zf.read(n)
    return None


def import_export_zip(
    db: Session, user: User, data_dir: Path, zip_bytes: bytes
) -> dict[str, Any]:
    """Upsert export ZIP into the current user. Returns a summary dict."""
    summary: dict[str, Any] = {
        "settings_upserted": 0,
        "weights_inserted": 0,
        "weights_updated": 0,
        "weights_skipped": 0,
        "photos_inserted": 0,
        "photos_updated": 0,
        "photos_skipped": 0,
        "errors": [],
    }

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    except zipfile.BadZipFile as e:
        raise ValueError("Not a valid ZIP file") from e

    with zf:
        names = zf.namelist()
        man_raw = _read_zip_member(zf, names, "trend-export/manifest.json", "manifest.json")
        data_raw = _read_zip_member(zf, names, "trend-export/data.json", "data.json")
        if not man_raw or not data_raw:
            raise ValueError("ZIP missing manifest.json or data.json")

        try:
            manifest = json.loads(man_raw.decode("utf-8"))
            data = json.loads(data_raw.decode("utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in export: {e}") from e

        if manifest.get("format") != FORMAT_NAME:
            raise ValueError(f"Unsupported format: {manifest.get('format')!r}")
        ver = int(manifest.get("version") or 0)
        if ver != FORMAT_VERSION:
            raise ValueError(f"Unsupported export version: {ver}")

        # Settings
        settings = data.get("settings") or {}
        if isinstance(settings, dict):
            for k, v in settings.items():
                if not isinstance(k, str) or k in SKIP_SETTING_KEYS:
                    continue
                set_user_setting(db, user.id, k, "" if v is None else str(v))
                summary["settings_upserted"] += 1

        # Weights
        for item in data.get("weights") or []:
            if not isinstance(item, dict):
                continue
            try:
                weight_val = float(item["weight"])
            except (KeyError, TypeError, ValueError):
                summary["errors"].append("weight row missing/invalid weight")
                continue
            logged = (item.get("logged_at") or "").strip() or None
            d = str(item.get("date") or (logged[:10] if logged else ""))[:10]
            if not d:
                summary["errors"].append("weight row missing date")
                continue
            bf = item.get("body_fat")
            try:
                bf_f = float(bf) if bf is not None and bf != "" else None
            except (TypeError, ValueError):
                bf_f = None
            note = item.get("note")
            if note is not None:
                note = str(note)[:500] or None

            existing = _find_weight(db, user.id, item)
            if existing:
                changed = False
                if existing.weight != weight_val:
                    existing.weight = weight_val
                    changed = True
                if existing.body_fat != bf_f:
                    existing.body_fat = bf_f
                    changed = True
                if (existing.note or None) != note:
                    existing.note = note
                    changed = True
                if logged and existing.logged_at != logged:
                    existing.logged_at = logged
                    changed = True
                if existing.date != d:
                    existing.date = d
                    changed = True
                if changed:
                    summary["weights_updated"] += 1
                else:
                    summary["weights_skipped"] += 1
            else:
                db.add(
                    Weight(
                        user_id=user.id,
                        date=d,
                        logged_at=logged,
                        weight=weight_val,
                        body_fat=bf_f,
                        note=note,
                    )
                )
                summary["weights_inserted"] += 1

        db.flush()

        # Photos
        dest_dir = data_dir / "photos" / str(user.id)
        dest_dir.mkdir(parents=True, exist_ok=True)

        for item in data.get("photos") or []:
            if not isinstance(item, dict):
                continue
            sha = item.get("file_sha256")
            if not sha or not isinstance(sha, str) or len(sha) < 16:
                summary["errors"].append("photo missing file_sha256; skipped")
                continue
            ext = (item.get("file_ext") or "jpg").lstrip(".")
            # Locate bytes in zip
            candidates = [
                f"trend-export/photos/{sha}.{ext}",
                f"photos/{sha}.{ext}",
            ]
            raw = _read_zip_member(zf, names, *candidates)
            if raw is None:
                # try any matching prefix
                for n in names:
                    base = n.rsplit("/", 1)[-1]
                    if base.startswith(sha) and "_proj" not in base:
                        raw = zf.read(n)
                        break
            if raw is None:
                summary["errors"].append(f"photo file {sha[:12]}… missing in ZIP")
                continue

            digest = _sha256_bytes(raw)
            if digest != sha:
                summary["errors"].append(f"photo sha mismatch for {sha[:12]}…")
                continue

            d = str(item.get("date") or "")[:10] or datetime.now(timezone.utc).date().isoformat()
            existing = _find_photo_by_sha(db, user.id, data_dir, sha)

            # Projection bytes (optional)
            proj_sha = item.get("projection_sha256")
            proj_ext = (item.get("projection_ext") or "jpg").lstrip(".")
            proj_raw = None
            if proj_sha:
                proj_raw = _read_zip_member(
                    zf,
                    names,
                    f"trend-export/photos/{proj_sha}_proj.{proj_ext}",
                    f"photos/{proj_sha}_proj.{proj_ext}",
                )
                if proj_raw is None:
                    for n in names:
                        base = n.rsplit("/", 1)[-1]
                        if base.startswith(proj_sha) and "_proj" in base:
                            proj_raw = zf.read(n)
                            break

            def _apply_meta(photo: Photo) -> bool:
                changed = False
                fields = {
                    "date": d,
                    "note": (str(item["note"])[:200] if item.get("note") is not None else None),
                    "mime": item.get("mime") or photo.mime or "image/jpeg",
                    "analysis_json": item.get("analysis_json"),
                    "bmi_point": item.get("bmi_point"),
                    "bmi_low": item.get("bmi_low"),
                    "bmi_high": item.get("bmi_high"),
                    "bmi_confidence": item.get("bmi_confidence"),
                    "appearance_score": item.get("appearance_score"),
                    "appearance_justification": item.get("appearance_justification"),
                    "confidence_overall": item.get("confidence_overall"),
                    "model": item.get("model"),
                    "projection_prompt": item.get("projection_prompt"),
                    "projection_model": item.get("projection_model"),
                    "projection_goal_lb": item.get("projection_goal_lb"),
                    "projection_created_at": item.get("projection_created_at"),
                    "projection_mime": item.get("projection_mime"),
                }
                for k, v in fields.items():
                    if getattr(photo, k) != v:
                        setattr(photo, k, v)
                        changed = True
                return changed

            if existing:
                changed = _apply_meta(existing)
                if proj_raw is not None:
                    pext = proj_ext
                    pname = f"{d}_{proj_sha[:10]}_goal.{pext}"
                    (dest_dir / pname).write_bytes(proj_raw)
                    if existing.projection_filename != pname:
                        existing.projection_filename = pname
                        changed = True
                    if not existing.projection_mime:
                        existing.projection_mime = item.get("projection_mime") or f"image/{pext}"
                        changed = True
                if changed:
                    summary["photos_updated"] += 1
                else:
                    summary["photos_skipped"] += 1
            else:
                fname = f"{d}_{sha[:10]}.{ext}"
                (dest_dir / fname).write_bytes(raw)
                photo = Photo(
                    user_id=user.id,
                    date=d,
                    filename=fname,
                    mime=item.get("mime") or f"image/{ext}",
                )
                _apply_meta(photo)
                if proj_raw is not None:
                    pname = f"{d}_{str(proj_sha)[:10]}_goal.{proj_ext}"
                    (dest_dir / pname).write_bytes(proj_raw)
                    photo.projection_filename = pname
                    photo.projection_mime = item.get("projection_mime") or f"image/{proj_ext}"
                db.add(photo)
                summary["photos_inserted"] += 1

        db.commit()

    return summary
