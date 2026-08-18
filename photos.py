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
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_photos_date ON photos(date);
        """
    )
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


def delete_photo(conn: sqlite3.Connection, data_dir: Path, pid: int) -> None:
    row = conn.execute("SELECT filename FROM photos WHERE id = ?", (pid,)).fetchone()
    if not row:
        raise KeyError("not found")
    conn.execute("DELETE FROM photos WHERE id = ?", (pid,))
    conn.commit()
    path = photos_dir(data_dir) / row["filename"]
    if path.is_file():
        path.unlink()


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
