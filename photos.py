"""Photo progress log + Grok vision analysis persistence."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Optional

from trend import parse_date
from vision import analyze_image_file

PHOTOS_DIR_NAME = "photos"


def ensure_photos_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            filename TEXT NOT NULL,
            mime TEXT NOT NULL,
            note TEXT,
            analysis_json TEXT,
            bmi_point REAL,
            bmi_low REAL,
            bmi_high REAL,
            bmi_confidence TEXT,
            appearance_score REAL,
            appearance_justification TEXT,
            confidence_overall TEXT,
            model TEXT,
            projection_filename TEXT,
            projection_mime TEXT,
            projection_prompt TEXT,
            projection_model TEXT,
            projection_goal_lb REAL,
            projection_created_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(date);
        """
    )
    # migrate older DBs
    cols = {r[1] for r in conn.execute("PRAGMA table_info(photos)").fetchall()}
    migrations = {
        "projection_filename": "TEXT",
        "projection_mime": "TEXT",
        "projection_prompt": "TEXT",
        "projection_model": "TEXT",
        "projection_goal_lb": "REAL",
        "projection_created_at": "TEXT",
        "projection_analysis_json": "TEXT",
        "projection_bmi_point": "REAL",
        "projection_bmi_low": "REAL",
        "projection_bmi_high": "REAL",
        "projection_appearance_score": "REAL",
        "projection_appearance_justification": "TEXT",
        "projection_confidence_overall": "TEXT",
        "projection_analysis_model": "TEXT",
    }
    for col, typ in migrations.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE photos ADD COLUMN {col} {typ}")
    conn.commit()


def photos_dir(data_dir: Path) -> Path:
    d = data_dir / PHOTOS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def row_to_dict(row: sqlite3.Row, include_analysis: bool = True) -> dict:
    d = dict(row)
    if include_analysis and d.get("analysis_json"):
        try:
            d["analysis"] = json.loads(d["analysis_json"])
        except Exception:
            d["analysis"] = None
    else:
        d["analysis"] = None
    d.pop("analysis_json", None)
    d["image_url"] = f"/api/photos/{d['id']}/image"
    d["has_projection"] = bool(d.get("projection_filename"))
    d["projection_url"] = (
        f"/api/photos/{d['id']}/projection" if d.get("projection_filename") else None
    )
    if d.get("projection_analysis_json"):
        try:
            d["projection_analysis"] = json.loads(d["projection_analysis_json"])
        except Exception:
            d["projection_analysis"] = None
    else:
        d["projection_analysis"] = None
    d.pop("projection_analysis_json", None)
    return d


def list_photos(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT * FROM photos ORDER BY date ASC, id ASC"
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def get_photo(conn: sqlite3.Connection, pid: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
    return row_to_dict(row) if row else None


def series_for_chart(conn: sqlite3.Connection) -> list:
    """Compact series for the visual BMI / appearance chart."""
    rows = conn.execute(
        """
        SELECT id, date, bmi_point, bmi_low, bmi_high, appearance_score,
               confidence_overall
        FROM photos
        WHERE appearance_score IS NOT NULL OR bmi_point IS NOT NULL
        ORDER BY date ASC, id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def create_photo(
    conn: sqlite3.Connection,
    data_dir: Path,
    *,
    image_bytes: bytes,
    mime: str,
    taken_date: str,
    note: Optional[str],
    now_iso: str,
    analyze: bool = True,
) -> dict:
    d = parse_date(taken_date).isoformat()
    ext = {".jpg", ".jpeg", ".png"}
    if mime in ("image/jpeg", "image/jpg"):
        suffix = ".jpg"
        mime = "image/jpeg"
    elif mime == "image/png":
        suffix = ".png"
    else:
        # sniff
        if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
            suffix, mime = ".png", "image/png"
        else:
            suffix, mime = ".jpg", "image/jpeg"

    fname = f"{d}_{uuid.uuid4().hex[:10]}{suffix}"
    path = photos_dir(data_dir) / fname
    path.write_bytes(image_bytes)

    analysis = None
    fields: dict[str, Any] = {
        "analysis_json": None,
        "bmi_point": None,
        "bmi_low": None,
        "bmi_high": None,
        "bmi_confidence": None,
        "appearance_score": None,
        "appearance_justification": None,
        "confidence_overall": None,
        "model": None,
    }
    if analyze:
        analysis = analyze_image_file(path, mime)
        meta = analysis.pop("_meta", {})
        fields["analysis_json"] = json.dumps(analysis)
        bmi = analysis.get("bmi_estimate") or {}
        app = analysis.get("appearance_rating") or {}
        fields.update(
            {
                "bmi_point": bmi.get("point"),
                "bmi_low": bmi.get("range_low"),
                "bmi_high": bmi.get("range_high"),
                "bmi_confidence": bmi.get("confidence"),
                "appearance_score": app.get("score"),
                "appearance_justification": app.get("justification"),
                "confidence_overall": analysis.get("confidence_overall"),
                "model": meta.get("model"),
            }
        )

    cur = conn.execute(
        """
        INSERT INTO photos(
            date, filename, mime, note, analysis_json,
            bmi_point, bmi_low, bmi_high, bmi_confidence,
            appearance_score, appearance_justification, confidence_overall,
            model, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            d,
            fname,
            mime,
            note,
            fields["analysis_json"],
            fields["bmi_point"],
            fields["bmi_low"],
            fields["bmi_high"],
            fields["bmi_confidence"],
            fields["appearance_score"],
            fields["appearance_justification"],
            fields["confidence_overall"],
            fields["model"],
            now_iso,
            now_iso,
        ),
    )
    conn.commit()
    return get_photo(conn, int(cur.lastrowid))  # type: ignore[arg-type]


def reanalyze_photo(conn: sqlite3.Connection, data_dir: Path, pid: int, now_iso: str) -> dict:
    row = conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise KeyError("not found")
    path = photos_dir(data_dir) / row["filename"]
    if not path.is_file():
        raise FileNotFoundError(row["filename"])
    analysis = analyze_image_file(path, row["mime"])
    meta = analysis.pop("_meta", {})
    bmi = analysis.get("bmi_estimate") or {}
    app = analysis.get("appearance_rating") or {}
    conn.execute(
        """
        UPDATE photos SET
            analysis_json=?,
            bmi_point=?, bmi_low=?, bmi_high=?, bmi_confidence=?,
            appearance_score=?, appearance_justification=?, confidence_overall=?,
            model=?, updated_at=?
        WHERE id=?
        """,
        (
            json.dumps(analysis),
            bmi.get("point"),
            bmi.get("range_low"),
            bmi.get("range_high"),
            bmi.get("confidence"),
            app.get("score"),
            app.get("justification"),
            analysis.get("confidence_overall"),
            meta.get("model"),
            now_iso,
            pid,
        ),
    )
    conn.commit()
    return get_photo(conn, pid)  # type: ignore[return-value]


def photo_file_path(conn: sqlite3.Connection, data_dir: Path, pid: int) -> tuple[Path, str]:
    row = conn.execute(
        "SELECT filename, mime FROM photos WHERE id = ?", (pid,)
    ).fetchone()
    if not row:
        raise KeyError("not found")
    path = photos_dir(data_dir) / row["filename"]
    if not path.is_file():
        raise FileNotFoundError(row["filename"])
    return path, row["mime"]


def projection_file_path(
    conn: sqlite3.Connection, data_dir: Path, pid: int
) -> tuple[Path, str]:
    row = conn.execute(
        "SELECT projection_filename, projection_mime FROM photos WHERE id = ?",
        (pid,),
    ).fetchone()
    if not row or not row["projection_filename"]:
        raise KeyError("no projection")
    path = photos_dir(data_dir) / row["projection_filename"]
    if not path.is_file():
        raise FileNotFoundError(row["projection_filename"])
    return path, row["projection_mime"] or "image/jpeg"


def save_goal_projection(
    conn: sqlite3.Connection,
    data_dir: Path,
    pid: int,
    *,
    goal_lb: float,
    current_lb: Optional[float],
    current_bmi: Optional[float],
    goal_bmi: Optional[float],
    now_iso: str,
) -> dict:
    """Run Imagine edit and store projection next to the source photo."""
    from imagine import edit_image_to_goal

    row = conn.execute("SELECT * FROM photos WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise KeyError("not found")
    src = photos_dir(data_dir) / row["filename"]
    if not src.is_file():
        raise FileNotFoundError(row["filename"])

    notes = None
    if row["analysis_json"]:
        try:
            analysis = json.loads(row["analysis_json"])
            obs = analysis.get("observations") or {}
            bits = [
                obs.get("overall_build"),
                obs.get("midsection"),
                analysis.get("appearance_rating", {}).get("justification"),
            ]
            notes = "; ".join(b for b in bits if b)
        except Exception:
            notes = None

    result = edit_image_to_goal(
        src.read_bytes(),
        row["mime"] or "image/jpeg",
        current_lb=current_lb,
        goal_lb=goal_lb,
        current_bmi=current_bmi,
        goal_bmi=goal_bmi,
        appearance_notes=notes,
    )

    # remove prior projection file if any
    if row["projection_filename"]:
        old = photos_dir(data_dir) / row["projection_filename"]
        if old.is_file():
            old.unlink()

    suffix = ".png" if "png" in (result["mime"] or "") else ".jpg"
    fname = f"{row['date']}_{pid}_goal{suffix}"
    out_path = photos_dir(data_dir) / fname
    out_path.write_bytes(result["bytes"])

    # Re-rate the projected image with the same vision analyzer
    proj_analysis_json = None
    proj_bmi_point = proj_bmi_low = proj_bmi_high = None
    proj_score = None
    proj_just = None
    proj_conf = None
    proj_analysis_model = None
    try:
        analysis = analyze_image_file(out_path, result["mime"])
        meta = analysis.pop("_meta", {})
        proj_analysis_json = json.dumps(analysis)
        bmi = analysis.get("bmi_estimate") or {}
        app = analysis.get("appearance_rating") or {}
        proj_bmi_point = bmi.get("point")
        proj_bmi_low = bmi.get("range_low")
        proj_bmi_high = bmi.get("range_high")
        proj_score = app.get("score")
        proj_just = app.get("justification")
        proj_conf = analysis.get("confidence_overall")
        proj_analysis_model = meta.get("model")
    except Exception as e:
        # Keep the Imagine result even if re-rate fails; surface error in prompt note
        proj_just = f"(projection saved; re-rate failed: {e})"

    conn.execute(
        """
        UPDATE photos SET
            projection_filename=?,
            projection_mime=?,
            projection_prompt=?,
            projection_model=?,
            projection_goal_lb=?,
            projection_created_at=?,
            projection_analysis_json=?,
            projection_bmi_point=?,
            projection_bmi_low=?,
            projection_bmi_high=?,
            projection_appearance_score=?,
            projection_appearance_justification=?,
            projection_confidence_overall=?,
            projection_analysis_model=?,
            updated_at=?
        WHERE id=?
        """,
        (
            fname,
            result["mime"],
            result["prompt"],
            result["model"],
            goal_lb,
            now_iso,
            proj_analysis_json,
            proj_bmi_point,
            proj_bmi_low,
            proj_bmi_high,
            proj_score,
            proj_just,
            proj_conf,
            proj_analysis_model,
            now_iso,
            pid,
        ),
    )
    conn.commit()
    return get_photo(conn, pid)  # type: ignore[return-value]


def delete_photo(conn: sqlite3.Connection, data_dir: Path, pid: int) -> None:
    row = conn.execute(
        "SELECT filename, projection_filename FROM photos WHERE id = ?", (pid,)
    ).fetchone()
    if not row:
        raise KeyError("not found")
    conn.execute("DELETE FROM photos WHERE id = ?", (pid,))
    conn.commit()
    for key in ("filename", "projection_filename"):
        name = row[key]
        if name:
            path = photos_dir(data_dir) / name
            if path.is_file():
                path.unlink()


def parse_multipart(handler) -> dict:
    """Parse multipart/form-data into {fields, file_bytes, filename, mime}."""
    import cgi
    from io import BytesIO

    ctype = handler.headers.get("Content-Type", "")
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length)
    environ = {
        "REQUEST_METHOD": "POST",
        "CONTENT_TYPE": ctype,
        "CONTENT_LENGTH": str(length),
    }
    fs = cgi.FieldStorage(fp=BytesIO(raw), headers=handler.headers, environ=environ)
    fields: dict[str, str] = {}
    file_bytes = None
    filename = None
    mime = "image/jpeg"
    for key in fs.keys():
        item = fs[key]
        if getattr(item, "filename", None):
            file_bytes = item.file.read()
            filename = item.filename
            mime = item.type or mime
        else:
            fields[key] = item.value if hasattr(item, "value") else str(item)
    return {
        "fields": fields,
        "file_bytes": file_bytes,
        "filename": filename,
        "mime": mime,
    }
