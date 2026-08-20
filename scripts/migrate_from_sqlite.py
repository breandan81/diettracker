#!/usr/bin/env python3
"""Import single-user SQLite data into multi-user DB for one account.

Example:
  .venv/bin/python scripts/migrate_from_sqlite.py \\
    --sqlite ~/AIML/claude/hackers-diet/data/weights.db \\
    --photos-dir ~/AIML/claude/hackers-diet/data/photos \\
    --email breandan@local \\
    --password 'DevTrend123!'
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.auth_utils import hash_password  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.models import Photo, User, UserSetting, Weight  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", type=Path, required=True)
    ap.add_argument("--photos-dir", type=Path, required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--name", default="Breandan")
    ap.add_argument("--admin", action="store_true", help="Print reminder to set ADMIN_USER_IDS")
    args = ap.parse_args()

    if not args.sqlite.is_file():
        raise SystemExit(f"SQLite not found: {args.sqlite}")

    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    src = sqlite3.connect(str(args.sqlite))
    src.row_factory = sqlite3.Row

    email = args.email.lower().strip()
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            print(f"User {email} already exists id={user.id} — clearing their weights/photos/settings for re-import")
            for model in (Weight, Photo, UserSetting):
                for row in db.scalars(select(model).where(model.user_id == user.id)).all():
                    db.delete(row)
            db.commit()
            user.password_hash = hash_password(args.password)
            user.name = args.name
            user.email_verified = True
        else:
            user = User(
                email=email,
                password_hash=hash_password(args.password),
                name=args.name,
                email_verified=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"Created user id={user.id} email={email}")

        # settings
        for row in src.execute("SELECT key, value FROM settings").fetchall():
            db.add(UserSetting(user_id=user.id, key=row["key"], value=row["value"] or ""))
        db.commit()

        # weights
        n_w = 0
        for row in src.execute(
            "SELECT date, logged_at, weight, body_fat, note, created_at, updated_at FROM weights ORDER BY id"
        ).fetchall():
            db.add(
                Weight(
                    user_id=user.id,
                    date=row["date"],
                    logged_at=row["logged_at"],
                    weight=row["weight"],
                    body_fat=row["body_fat"],
                    note=row["note"],
                )
            )
            n_w += 1
        db.commit()

        # photos + files
        dest_dir = settings.data_dir / "photos" / str(user.id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        n_p = 0
        cols = {r[1] for r in src.execute("PRAGMA table_info(photos)").fetchall()}
        for row in src.execute("SELECT * FROM photos ORDER BY id").fetchall():
            filename = row["filename"]
            src_file = args.photos_dir / filename
            if src_file.is_file():
                shutil.copy2(src_file, dest_dir / filename)
            proj = row["projection_filename"] if "projection_filename" in cols else None
            if proj:
                pf = args.photos_dir / proj
                if pf.is_file():
                    shutil.copy2(pf, dest_dir / proj)
            db.add(
                Photo(
                    user_id=user.id,
                    date=row["date"],
                    filename=filename,
                    mime=row["mime"] if "mime" in cols else "image/jpeg",
                    note=row["note"] if "note" in cols else None,
                    analysis_json=row["analysis_json"] if "analysis_json" in cols else None,
                    bmi_point=row["bmi_point"] if "bmi_point" in cols else None,
                    bmi_low=row["bmi_low"] if "bmi_low" in cols else None,
                    bmi_high=row["bmi_high"] if "bmi_high" in cols else None,
                    bmi_confidence=row["bmi_confidence"] if "bmi_confidence" in cols else None,
                    appearance_score=row["appearance_score"] if "appearance_score" in cols else None,
                    appearance_justification=row["appearance_justification"]
                    if "appearance_justification" in cols
                    else None,
                    confidence_overall=row["confidence_overall"]
                    if "confidence_overall" in cols
                    else None,
                    model=row["model"] if "model" in cols else None,
                    projection_filename=proj,
                    projection_mime=row["projection_mime"] if "projection_mime" in cols else None,
                    projection_prompt=row["projection_prompt"] if "projection_prompt" in cols else None,
                    projection_model=row["projection_model"] if "projection_model" in cols else None,
                    projection_goal_lb=row["projection_goal_lb"] if "projection_goal_lb" in cols else None,
                    projection_created_at=row["projection_created_at"]
                    if "projection_created_at" in cols
                    else None,
                )
            )
            n_p += 1
        db.commit()

        print(f"Imported {n_w} weights, {n_p} photos for user_id={user.id}")
        print(f"Login: {email} / (password you passed)")
        print(f"Set ADMIN_USER_IDS={user.id} in secrets.env for admin access")
    finally:
        db.close()
        src.close()


if __name__ == "__main__":
    main()
