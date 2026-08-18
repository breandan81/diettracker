#!/usr/bin/env python3
"""Hacker's Diet style weight tracker — SQLite + stdlib HTTP server."""

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
from trend import (
    DEFAULT_HALF_LIFE_DAYS,
    KCAL_PER_LB,
    compute_trend,
    parse_date,
    summary,
)

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("HACKDIET_DB", DATA_DIR / "weights.db"))
PORT = int(os.environ.get("PORT", "8510"))
HOST = os.environ.get("HOST", "0.0.0.0")

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
            date TEXT NOT NULL,          -- YYYY-MM-DD
            weight REAL NOT NULL,       -- pounds
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
    # defaults
    defaults = {
        "half_life_days": str(DEFAULT_HALF_LIFE_DAYS),
        "unit": "lb",
        "goal_weight": "",
        "height_in": "",  # total inches; used for BMI
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
            (k, v),
        )
    conn.commit()


DB = connect()
init_db(DB)


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
    return out


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
        "SELECT id, date, weight, note, created_at, updated_at FROM weights ORDER BY date ASC, id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def load_trend(half_life: Optional[float] = None) -> tuple[list, dict, float]:
    if half_life is None:
        half_life = float(all_settings()["half_life_days"])
    rows = fetch_weights()
    samples = [(r["id"], r["date"], r["weight"], r["note"]) for r in rows]
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
                return self._json(200, {"ok": True, "service": "hackers-diet"})

            if path == "/api/coach/status":
                return self._json(200, kobold_status())

            if path == "/api/coach":
                # return last cached pep talk if any
                cached = _load_cached_coach()
                return self._json(200, {"coach": cached, "kobold": kobold_status()})

            if path == "/api/settings":
                s = all_settings()
                s["kcal_per_lb"] = KCAL_PER_LB
                return self._json(200, s)

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
            return self._err(404, "not found")
        except Exception as e:
            traceback.print_exc()
            return self._err(500, str(e))

    # --- mutations ---

    def _create_weight(self, data: dict) -> None:
        if "weight" not in data:
            return self._err(400, "weight required")
        try:
            weight = float(data["weight"])
        except (TypeError, ValueError):
            return self._err(400, "weight must be a number")
        if weight <= 0 or weight > 1000:
            return self._err(400, "weight out of range")

        d_raw = data.get("date") or date.today().isoformat()
        try:
            d = parse_date(d_raw).isoformat()
        except ValueError:
            return self._err(400, "invalid date (use YYYY-MM-DD)")

        note = data.get("note")
        if note is not None:
            note = str(note).strip() or None

        now = utc_now_iso()
        cur = DB.execute(
            """
            INSERT INTO weights(date, weight, note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (d, weight, note, now, now),
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
        note = row["note"]

        if "weight" in data and data["weight"] is not None:
            try:
                weight = float(data["weight"])
            except (TypeError, ValueError):
                return self._err(400, "weight must be a number")
            if weight <= 0 or weight > 1000:
                return self._err(400, "weight out of range")

        if "date" in data and data["date"]:
            try:
                d = parse_date(data["date"]).isoformat()
            except ValueError:
                return self._err(400, "invalid date")

        if "note" in data:
            note = data["note"]
            if note is not None:
                note = str(note).strip() or None

        now = utc_now_iso()
        DB.execute(
            "UPDATE weights SET date=?, weight=?, note=?, updated_at=? WHERE id=?",
            (d, weight, note, now, wid),
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
    print(f"Hacker's Diet tracker on http://{HOST}:{PORT}/  db={DB_PATH}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nshutting down", flush=True)
    finally:
        httpd.server_close()
        DB.close()


if __name__ == "__main__":
    main()
