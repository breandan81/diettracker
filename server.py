#!/usr/bin/env python3
"""τrend weight trend tracker — SQLite + stdlib HTTP server."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import traceback
from datetime import date, datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from coach import generate_pep, kobold_status
from photos import (
    create_photo,
    delete_photo,
    ensure_photos_schema,
    get_photo,
    list_photos,
    parse_multipart,
    photo_file_path,
    projection_file_path,
    reanalyze_photo,
    save_goal_projection,
    series_for_chart,
)
from trend import (
    DEFAULT_HALF_LIFE_DAYS,
    KCAL_PER_LB,
    compute_trend,
    parse_date,
    parse_datetime,
    summary,
)
from vision import xai_status

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("HACKDIET_DB", DATA_DIR / "weights.db"))
PORT = int(os.environ.get("PORT", "8510"))
HOST = os.environ.get("HOST", "0.0.0.0")

# Load secrets.env into process env early (XAI_API_KEY)
try:
    from vision import load_xai_credentials

    load_xai_credentials()
except Exception:
    pass

# last AI coach payload (in-memory; also mirrored to settings for reloads)
_LAST_COACH: Optional[dict] = None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS weights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            weight REAL NOT NULL,
            note TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_weights_date ON weights(date);

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    # migrate older DBs (columns added after first create)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(weights)").fetchall()}
    if "logged_at" not in cols:
        conn.execute("ALTER TABLE weights ADD COLUMN logged_at TEXT")
    if "body_fat" not in cols:
        conn.execute("ALTER TABLE weights ADD COLUMN body_fat REAL")
    conn.execute(
        """
        UPDATE weights
        SET logged_at = date || 'T12:00:00+00:00'
        WHERE logged_at IS NULL OR logged_at = ''
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_weights_logged_at ON weights(logged_at)"
    )
    defaults = {
        "half_life_days": str(DEFAULT_HALF_LIFE_DAYS),
        "unit": "lb",
        "goal_weight": "",
        "height_in": "",
        "sex": "",  # male | female
        "age": "",  # years
        "athlete": "0",  # 0|1 — Renpho athlete BF curve
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (k, v),
        )
    conn.commit()


DB = connect()
init_db(DB)
ensure_photos_schema(DB)


def _load_cached_coach() -> Optional[dict]:
    global _LAST_COACH
    if _LAST_COACH:
        return _LAST_COACH
    raw = get_setting("last_coach_json")
    if not raw:
        return None
    try:
        _LAST_COACH = json.loads(raw)
        return _LAST_COACH
    except Exception:
        return None


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    row = DB.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return row["value"]


def set_setting(key: str, value: str) -> None:
    DB.execute(
        """
        INSERT INTO settings(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )
    DB.commit()


def _float_or_none(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bmi_from_lb_in(weight_lb: float, height_in: float) -> dict:
    """US customary BMI + WHO-ish adult category + healthy weight band."""
    if height_in <= 0 or weight_lb <= 0:
        raise ValueError("height and weight must be positive")
    bmi = (weight_lb / (height_in * height_in)) * 703.0
    if bmi < 18.5:
        category = "Underweight"
        cat_key = "underweight"
    elif bmi < 25.0:
        category = "Normal"
        cat_key = "normal"
    elif bmi < 30.0:
        category = "Overweight"
        cat_key = "overweight"
    elif bmi < 35.0:
        category = "Obese I"
        cat_key = "obese"
    elif bmi < 40.0:
        category = "Obese II"
        cat_key = "obese"
    else:
        category = "Obese III"
        cat_key = "obese"
    # healthy BMI band 18.5–24.9 → weight range for this height
    healthy_low = 18.5 * (height_in * height_in) / 703.0
    healthy_high = 24.9 * (height_in * height_in) / 703.0
    ft = int(height_in // 12)
    inch = height_in - ft * 12
    return {
        "bmi": round(bmi, 1),
        "category": category,
        "category_key": cat_key,
        "height_in": height_in,
        "height_label": f"{ft}'{inch:.0f}\"" if abs(inch - round(inch)) < 0.05 else f"{ft}'{inch:.1f}\"",
        "healthy_weight_lb": {
            "low": round(healthy_low, 1),
            "high": round(healthy_high, 1),
        },
        "ranges": [
            {"key": "underweight", "label": "Underweight", "max": 18.5},
            {"key": "normal", "label": "Normal", "min": 18.5, "max": 25.0},
            {"key": "overweight", "label": "Overweight", "min": 25.0, "max": 30.0},
            {"key": "obese", "label": "Obese", "min": 30.0},
        ],
    }


def all_settings() -> dict:
    rows = DB.execute("SELECT key, value FROM settings").fetchall()
    out = {r["key"]: r["value"] for r in rows}
    # coerce known numeric
    try:
        out["half_life_days"] = float(out.get("half_life_days", DEFAULT_HALF_LIFE_DAYS))
    except (TypeError, ValueError):
        out["half_life_days"] = DEFAULT_HALF_LIFE_DAYS
    out["goal_weight"] = _float_or_none(out.get("goal_weight") or "")
    out["height_in"] = _float_or_none(out.get("height_in") or "")
    sex = (out.get("sex") or "").strip().lower()
    out["sex"] = sex if sex in ("male", "female") else None
    age = _float_or_none(out.get("age") or "")
    out["age"] = int(age) if age is not None else None
    ath = (out.get("athlete") or "0").strip().lower()
    out["athlete"] = ath in ("1", "true", "yes", "on")
    return out


def scale_profile_payload() -> dict:
    """Compact profile for ESP32 BLE → Renpho (QN Sex: Male=0, Female=1)."""
    s = all_settings()
    height_in = s.get("height_in")
    height_m = (float(height_in) * 0.0254) if height_in else None
    sex = s.get("sex")
    sex_code = {"male": 0, "female": 1}.get(sex) if sex else None
    ready = bool(
        height_m
        and height_m > 0
        and sex_code is not None
        and s.get("age") is not None
        and 5 <= int(s["age"]) <= 120
    )
    return {
        "ready": ready,
        "sex": sex,
        "sex_code": sex_code,
        "age": s.get("age"),
        "height_in": height_in,
        "height_m": round(height_m, 4) if height_m else None,
        "athlete": bool(s.get("athlete")),
        "algorithm": 4,  # Renpho default on-device BF algorithm
        "unit": s.get("unit") or "lb",
    }


def attach_bmi(summ: dict, settings: Optional[dict] = None) -> dict:
    """Add BMI fields to a summary dict using trend (fallback: latest) weight."""
    s = dict(summ)
    settings = settings if settings is not None else all_settings()
    height_in = settings.get("height_in")
    weight = s.get("trend")
    if weight is None:
        weight = s.get("latest_weight")
    if height_in and weight:
        try:
            s["bmi"] = bmi_from_lb_in(float(weight), float(height_in))
        except (TypeError, ValueError):
            s["bmi"] = None
    else:
        s["bmi"] = None
    return s


def fetch_weights() -> list:
    rows = DB.execute(
        """
        SELECT id, date, logged_at, weight, body_fat, note, created_at, updated_at
        FROM weights
        ORDER BY COALESCE(logged_at, date), id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def load_trend(half_life: Optional[float] = None) -> tuple[list, dict, float]:
    if half_life is None:
        half_life = float(all_settings()["half_life_days"])
    rows = fetch_weights()
    samples = []
    for r in rows:
        when = r.get("logged_at") or r.get("date")
        samples.append(
            (r["id"], when, r["weight"], r["note"], r.get("body_fat"))
        )
    points = compute_trend(samples, half_life_days=half_life)
    return [p.to_dict() for p in points], summary(points), half_life


def read_json(handler: "Handler") -> Any:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


class Handler(SimpleHTTPRequestHandler):
    # serve static from public/
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        # quieter access log
        sys_stderr = __import__("sys").stderr
        print(f"[{self.log_date_time_string()}] {args[0]}", file=sys_stderr)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, msg: str) -> None:
        self._json(code, {"error": msg})

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                return self._json(
                    200,
                    {
                        "ok": True,
                        "service": "trend",
                        "xai": xai_status(),
                        "kobold": kobold_status(),
                    },
                )

            if path == "/api/coach/status":
                return self._json(200, kobold_status())

            if path == "/api/vision/status":
                return self._json(200, xai_status())

            if path == "/api/coach":
                # return last cached pep talk if any
                cached = _load_cached_coach()
                return self._json(200, {"coach": cached, "kobold": kobold_status()})

            if path == "/api/settings":
                s = all_settings()
                s["kcal_per_lb"] = KCAL_PER_LB
                return self._json(200, s)

            if path == "/api/scale-profile":
                return self._json(200, scale_profile_payload())

            if path == "/api/photos":
                return self._json(200, {"photos": list_photos(DB)})

            if path == "/api/photos/series":
                return self._json(200, {"series": series_for_chart(DB)})

            m = re.fullmatch(r"/api/photos/(\d+)/image", path)
            if m:
                return self._serve_photo_image(int(m.group(1)))

            m = re.fullmatch(r"/api/photos/(\d+)/projection", path)
            if m:
                return self._serve_projection_image(int(m.group(1)))

            m = re.fullmatch(r"/api/photos/(\d+)", path)
            if m:
                photo = get_photo(DB, int(m.group(1)))
                if not photo:
                    return self._err(404, "not found")
                return self._json(200, {"photo": photo})

            if path in ("/api/weights", "/api/trend", "/api/summary"):
                half = None
                if "half_life_days" in qs:
                    half = float(qs["half_life_days"][0])
                series, summ, half_life = load_trend(half)
                summ = attach_bmi({**summ, "half_life_days": half_life})
                if path == "/api/weights":
                    # raw rows + trend fields merged
                    return self._json(200, {"half_life_days": half_life, "entries": series})
                if path == "/api/summary":
                    return self._json(200, summ)
                return self._json(
                    200,
                    {
                        "half_life_days": half_life,
                        "kcal_per_lb": KCAL_PER_LB,
                        "summary": summ,
                        "series": series,
                        "photo_series": series_for_chart(DB),
                    },
                )

            # static
            if path == "/":
                self.path = "/index.html"
            return super().do_GET()
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e))

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/weights":
                data = read_json(self)
                return self._create_weight(data)
            if parsed.path == "/api/coach":
                data = read_json(self)
                return self._generate_coach(data)
            if parsed.path == "/api/photos":
                return self._create_photo()
            m = re.fullmatch(r"/api/photos/(\d+)/analyze", parsed.path)
            if m:
                return self._reanalyze_photo(int(m.group(1)))
            m = re.fullmatch(r"/api/photos/(\d+)/project-goal", parsed.path)
            if m:
                data = {}
                # optional JSON body with goal override
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if length:
                        data = read_json(self)
                except Exception:
                    data = {}
                return self._project_goal(int(m.group(1)), data or {})
            return self._err(404, "not found")
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e))

    def do_PUT(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/settings":
                data = read_json(self)
                return self._update_settings(data)

            m = re.fullmatch(r"/api/weights/(\d+)", parsed.path)
            if m:
                data = read_json(self)
                return self._update_weight(int(m.group(1)), data)
            return self._err(404, "not found")
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e))

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            m = re.fullmatch(r"/api/weights/(\d+)", parsed.path)
            if m:
                return self._delete_weight(int(m.group(1)))
            m = re.fullmatch(r"/api/photos/(\d+)", parsed.path)
            if m:
                return self._delete_photo(int(m.group(1)))
            return self._err(404, "not found")
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e))

    # --- mutations ---

    def _parse_logged_at(self, data: dict) -> tuple[str, str]:
        """Return (date_yyyy_mm_dd, logged_at_iso_utc).

        Prefer an explicit client timestamp when provided (manual edits).
        Otherwise use this host's clock — ESP32 auto-logs omit logged_at on
        purpose so server time is authoritative.
        """
        raw = data.get("logged_at") or data.get("timestamp") or data.get("date")
        if not raw:
            dt = datetime.now().astimezone()  # server local tz with offset
        else:
            try:
                # bare YYYY-MM-DD from old clients → noon local for that day
                if isinstance(raw, str) and "T" not in raw and len(raw.strip()) == 10:
                    local = datetime.now().astimezone().tzinfo
                    dt = datetime.fromisoformat(raw.strip() + "T12:00:00").replace(tzinfo=local)
                else:
                    dt = parse_datetime(raw)
            except ValueError:
                raise ValueError("invalid logged_at/date")
        dt = dt.astimezone()  # normalize to server-local aware
        return dt.date().isoformat(), dt.replace(microsecond=0).isoformat()

    def _create_weight(self, data: dict) -> None:
        if "weight" not in data:
            return self._err(400, "weight required")
        try:
            weight = float(data["weight"])
        except (TypeError, ValueError):
            return self._err(400, "weight must be a number")
        if weight <= 0 or weight > 1000:
            return self._err(400, "weight out of range")

        try:
            d, logged_at = self._parse_logged_at(data)
        except ValueError as e:
            return self._err(400, str(e))

        body_fat = None
        if data.get("body_fat") is not None and data.get("body_fat") != "":
            try:
                body_fat = float(data["body_fat"])
            except (TypeError, ValueError):
                return self._err(400, "body_fat must be a number (percent)")
            if body_fat < 0 or body_fat > 80:
                return self._err(400, "body_fat out of range")

        note = data.get("note")
        if note is not None:
            note = str(note).strip() or None

        now = utc_now_iso()

        # Dedupe auto-scale spam: same renpho weight (+BF) within 2 minutes
        if note == "renpho-ble":
            row = DB.execute(
                """
                SELECT id, date, logged_at, weight, body_fat, note
                FROM weights
                WHERE note = 'renpho-ble'
                ORDER BY COALESCE(logged_at, created_at) DESC, id DESC
                LIMIT 1
                """
            ).fetchone()
            if row is not None:
                try:
                    prev_at = parse_datetime(row["logged_at"] or row["date"])
                    cur_at = parse_datetime(logged_at)
                    age_s = abs((cur_at - prev_at).total_seconds())
                except Exception:
                    age_s = 9999
                same_w = abs(float(row["weight"]) - weight) < 0.08
                prev_bf = row["body_fat"]
                if body_fat is None and prev_bf is None:
                    same_bf = True
                elif body_fat is None or prev_bf is None:
                    same_bf = False
                else:
                    same_bf = abs(float(prev_bf) - float(body_fat)) < 0.15
                if age_s < 120 and same_w and same_bf:
                    series, summ, half = load_trend()
                    entry = next((e for e in series if e["id"] == row["id"]), None)
                    return self._json(
                        200,
                        {
                            "entry": entry,
                            "deduped": True,
                            "summary": attach_bmi({**summ, "half_life_days": half}),
                            "series": series,
                        },
                    )

        cur = DB.execute(
            """
            INSERT INTO weights(date, logged_at, weight, body_fat, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (d, logged_at, weight, body_fat, note, now, now),
        )
        DB.commit()
        rid = cur.lastrowid
        series, summ, half = load_trend()
        entry = next((e for e in series if e["id"] == rid), None)
        return self._json(
            201,
            {
                "entry": entry,
                "summary": attach_bmi({**summ, "half_life_days": half}),
                "series": series,
            },
        )

    def _update_weight(self, wid: int, data: dict) -> None:
        row = DB.execute("SELECT * FROM weights WHERE id = ?", (wid,)).fetchone()
        if not row:
            return self._err(404, "not found")

        weight = row["weight"]
        d = row["date"]
        logged_at = row["logged_at"] or (d + "T12:00:00+00:00")
        note = row["note"]
        body_fat = row["body_fat"] if "body_fat" in row.keys() else None

        if "weight" in data and data["weight"] is not None:
            try:
                weight = float(data["weight"])
            except (TypeError, ValueError):
                return self._err(400, "weight must be a number")
            if weight <= 0 or weight > 1000:
                return self._err(400, "weight out of range")

        if data.get("logged_at") or data.get("timestamp") or data.get("date"):
            try:
                d, logged_at = self._parse_logged_at(data)
            except ValueError as e:
                return self._err(400, str(e))

        if "body_fat" in data:
            if data["body_fat"] is None or data["body_fat"] == "":
                body_fat = None
            else:
                try:
                    body_fat = float(data["body_fat"])
                except (TypeError, ValueError):
                    return self._err(400, "body_fat must be a number")

        if "note" in data:
            note = data["note"]
            if note is not None:
                note = str(note).strip() or None

        now = utc_now_iso()
        DB.execute(
            """
            UPDATE weights
            SET date=?, logged_at=?, weight=?, body_fat=?, note=?, updated_at=?
            WHERE id=?
            """,
            (d, logged_at, weight, body_fat, note, now, wid),
        )
        DB.commit()
        series, summ, half = load_trend()
        entry = next((e for e in series if e["id"] == wid), None)
        return self._json(
            200,
            {
                "entry": entry,
                "summary": attach_bmi({**summ, "half_life_days": half}),
                "series": series,
            },
        )

    def _delete_weight(self, wid: int) -> None:
        cur = DB.execute("DELETE FROM weights WHERE id = ?", (wid,))
        DB.commit()
        if cur.rowcount == 0:
            return self._err(404, "not found")
        series, summ, half = load_trend()
        return self._json(
            200,
            {
                "deleted": wid,
                "summary": attach_bmi({**summ, "half_life_days": half}),
                "series": series,
            },
        )

    def _serve_binary_image(self, path: Path, mime: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "private, max-age=86400")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _serve_photo_image(self, pid: int) -> None:
        try:
            path, mime = photo_file_path(DB, DATA_DIR, pid)
        except KeyError:
            return self._err(404, "not found")
        except FileNotFoundError:
            return self._err(404, "image missing on disk")
        return self._serve_binary_image(path, mime)

    def _serve_projection_image(self, pid: int) -> None:
        try:
            path, mime = projection_file_path(DB, DATA_DIR, pid)
        except KeyError:
            return self._err(404, "no projection yet")
        except FileNotFoundError:
            return self._err(404, "projection missing on disk")
        return self._serve_binary_image(path, mime)

    def _profile_sex_age(self, settings: Optional[dict] = None) -> tuple:
        s = settings if settings is not None else all_settings()
        sex = s.get("sex") or None
        age = s.get("age")
        try:
            age_i = int(float(age)) if age not in (None, "") else None
        except (TypeError, ValueError):
            age_i = None
        return sex, age_i

    def _project_goal(self, pid: int, data: dict) -> None:
        settings = all_settings()
        goal = data.get("goal_weight", settings.get("goal_weight"))
        try:
            goal_lb = float(goal) if goal not in (None, "") else None
        except (TypeError, ValueError):
            return self._err(400, "goal_weight must be a number")
        if goal_lb is None:
            return self._err(400, "set a goal weight in Settings first")

        series, summ, half = load_trend()
        summ = attach_bmi({**summ, "half_life_days": half}, settings)
        current_lb = summ.get("trend") or summ.get("latest_weight")
        current_bmi = (summ.get("bmi") or {}).get("bmi")
        height_in = settings.get("height_in")
        goal_bmi = None
        if height_in and goal_lb:
            try:
                goal_bmi = round(
                    (703.0 * float(goal_lb)) / (float(height_in) ** 2), 1
                )
            except Exception:
                goal_bmi = None
        sex, age_i = self._profile_sex_age(settings)

        try:
            photo = save_goal_projection(
                DB,
                DATA_DIR,
                pid,
                goal_lb=goal_lb,
                current_lb=float(current_lb) if current_lb is not None else None,
                current_bmi=float(current_bmi) if current_bmi is not None else None,
                goal_bmi=goal_bmi,
                now_iso=utc_now_iso(),
                sex=sex,
                age=age_i,
            )
        except KeyError:
            return self._err(404, "not found")
        except FileNotFoundError:
            return self._err(404, "image missing on disk")
        except Exception as e:
            traceback.print_exc()
            return self._err(502, str(e))

        return self._json(
            200,
            {
                "photo": photo,
                "photos": list_photos(DB),
                "goal_lb": goal_lb,
                "goal_bmi": goal_bmi,
            },
        )

    def _create_photo(self) -> None:
        ctype = self.headers.get("Content-Type", "")
        analyze = True
        note = None
        taken = date.today().isoformat()
        image_bytes = None
        mime = "image/jpeg"

        if ctype.startswith("multipart/form-data"):
            parts = parse_multipart(self)
            fields = parts["fields"]
            image_bytes = parts["file_bytes"]
            mime = parts.get("mime") or mime
            if fields.get("date"):
                taken = fields["date"]
            if fields.get("note") is not None:
                note = fields.get("note") or None
            if fields.get("analyze") in ("0", "false", "False"):
                analyze = False
        else:
            data = read_json(self)
            taken = data.get("date") or taken
            note = data.get("note")
            analyze = data.get("analyze", True)
            b64 = data.get("image_base64") or data.get("image")
            if not b64:
                return self._err(400, "image required (multipart file or image_base64)")
            if "," in b64 and b64.strip().startswith("data:"):
                header, b64 = b64.split(",", 1)
                if "image/png" in header:
                    mime = "image/png"
            import base64

            try:
                image_bytes = base64.b64decode(b64)
            except Exception:
                return self._err(400, "invalid base64 image")
            if data.get("mime"):
                mime = data["mime"]

        if not image_bytes:
            return self._err(400, "empty image")
        if len(image_bytes) > 18 * 1024 * 1024:
            return self._err(400, "image too large (max ~18MB)")

        sex, age_i = self._profile_sex_age()
        try:
            photo = create_photo(
                DB,
                DATA_DIR,
                image_bytes=image_bytes,
                mime=mime,
                taken_date=taken,
                note=note,
                now_iso=utc_now_iso(),
                analyze=bool(analyze),
                sex=sex,
                age=age_i,
            )
        except Exception as e:
            traceback.print_exc()
            return self._err(502, str(e))

        return self._json(
            201,
            {
                "photo": photo,
                "photos": list_photos(DB),
                "photo_series": series_for_chart(DB),
                "xai": xai_status(),
            },
        )

    def _reanalyze_photo(self, pid: int) -> None:
        sex, age_i = self._profile_sex_age()
        try:
            photo = reanalyze_photo(
                DB, DATA_DIR, pid, utc_now_iso(), sex=sex, age=age_i
            )
        except KeyError:
            return self._err(404, "not found")
        except FileNotFoundError:
            return self._err(404, "image missing on disk")
        except Exception as e:
            traceback.print_exc()
            return self._err(502, str(e))
        return self._json(
            200,
            {
                "photo": photo,
                "photos": list_photos(DB),
                "photo_series": series_for_chart(DB),
            },
        )

    def _delete_photo(self, pid: int) -> None:
        try:
            delete_photo(DB, DATA_DIR, pid)
        except KeyError:
            return self._err(404, "not found")
        return self._json(
            200,
            {
                "deleted": pid,
                "photos": list_photos(DB),
                "photo_series": series_for_chart(DB),
            },
        )

    def _generate_coach(self, data: dict) -> None:
        global _LAST_COACH
        style = str((data or {}).get("style") or "pep").lower()
        if style not in ("pep", "roast", "haiku", "brief"):
            style = "pep"
        series, summ, half = load_trend()
        summ = {**summ, "half_life_days": half}
        settings = all_settings()
        try:
            coach = generate_pep(series, summ, settings, style=style)
        except Exception as e:
            traceback.print_exc()
            return self._err(502, str(e))
        coach["generated_at"] = utc_now_iso()
        _LAST_COACH = coach
        # persist lightweight cache for reloads
        try:
            set_setting(
                "last_coach_json",
                json.dumps(
                    {
                        "title": coach.get("title"),
                        "message": coach.get("message"),
                        "toast": coach.get("toast"),
                        "badge": coach.get("badge"),
                        "mood": coach.get("mood"),
                        "style": coach.get("style"),
                        "model": coach.get("model"),
                        "generated_at": coach.get("generated_at"),
                    }
                ),
            )
        except Exception:
            pass
        return self._json(
            200,
            {
                "coach": coach,
                "summary": summ,
                "kobold": kobold_status(),
            },
        )

    def _update_settings(self, data: dict) -> None:
        if "half_life_days" in data and data["half_life_days"] is not None:
            try:
                hl = float(data["half_life_days"])
            except (TypeError, ValueError):
                return self._err(400, "half_life_days must be a number")
            if hl <= 0 or hl > 365:
                return self._err(400, "half_life_days out of range")
            set_setting("half_life_days", str(hl))

        if "goal_weight" in data:
            gw = data["goal_weight"]
            if gw is None or gw == "":
                set_setting("goal_weight", "")
            else:
                try:
                    gwf = float(gw)
                except (TypeError, ValueError):
                    return self._err(400, "goal_weight must be a number")
                set_setting("goal_weight", str(gwf))

        if "height_in" in data:
            hi = data["height_in"]
            if hi is None or hi == "":
                set_setting("height_in", "")
            else:
                try:
                    hif = float(hi)
                except (TypeError, ValueError):
                    return self._err(400, "height_in must be a number (total inches)")
                if hif < 36 or hif > 96:
                    return self._err(400, "height_in out of range (36–96 in)")
                set_setting("height_in", str(hif))

        if "sex" in data:
            sex = data["sex"]
            if sex is None or sex == "":
                set_setting("sex", "")
            else:
                sex_s = str(sex).strip().lower()
                if sex_s not in ("male", "female"):
                    return self._err(400, "sex must be male or female")
                set_setting("sex", sex_s)

        if "age" in data:
            age = data["age"]
            if age is None or age == "":
                set_setting("age", "")
            else:
                try:
                    age_i = int(float(age))
                except (TypeError, ValueError):
                    return self._err(400, "age must be an integer")
                if age_i < 5 or age_i > 120:
                    return self._err(400, "age out of range")
                set_setting("age", str(age_i))

        if "athlete" in data:
            ath = data["athlete"]
            if isinstance(ath, bool):
                set_setting("athlete", "1" if ath else "0")
            elif ath in (None, ""):
                set_setting("athlete", "0")
            else:
                set_setting(
                    "athlete",
                    "1" if str(ath).strip().lower() in ("1", "true", "yes", "on") else "0",
                )

        if "unit" in data and data["unit"]:
            unit = str(data["unit"]).lower()
            if unit not in ("lb", "kg"):
                return self._err(400, "unit must be lb or kg")
            set_setting("unit", unit)

        s = all_settings()
        s["kcal_per_lb"] = KCAL_PER_LB
        series, summ, half = load_trend()
        return self._json(
            200,
            {
                "settings": s,
                "summary": attach_bmi({**summ, "half_life_days": half}, s),
                "series": series,
            },
        )


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"τrend tracker on http://{HOST}:{PORT}/  db={DB_PATH}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        httpd.server_close()
        DB.close()


if __name__ == "__main__":
    main()
