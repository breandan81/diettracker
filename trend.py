"""Time-aware exponential smoothing for irregular weight samples.

Uses real elapsed time between weigh-ins (fractional days from timestamps),
not calendar-day counts — so automatic logging at 7am vs 9pm is handled
correctly.

    decay = 0.5 ** (dt_days / H)
    alpha = 1 - decay
    trend_t = alpha * weight_t + decay * trend_{t-1}

Body fat (when present) is smoothed with the same half-life.

    kcal/day ≈ rate_lb_per_day * 3500
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import List, Optional, Sequence, Tuple, Union

KCAL_PER_LB = 3500.0
DEFAULT_HALF_LIFE_DAYS = 7.0

DateTimeLike = Union[date, datetime, str]


def parse_datetime(d: DateTimeLike) -> datetime:
    """Parse to timezone-aware UTC datetime (naive treated as local→UTC best-effort)."""
    if isinstance(d, datetime):
        dt = d
    elif isinstance(d, date):
        dt = datetime(d.year, d.month, d.day, 12, 0, 0)
    else:
        s = str(d).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        # date only
        if "T" not in s and len(s) >= 10:
            dt = datetime.fromisoformat(s[:10] + "T12:00:00")
        else:
            dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        # assume UTC if no tz — ESP32/server should send offset when possible
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_date(d: DateTimeLike) -> date:
    return parse_datetime(d).date()


def days_between_dt(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 86400.0)


def time_alpha(dt_days: float, half_life_days: float) -> Tuple[float, float]:
    """Return (alpha, decay) for a gap of dt_days given half-life H.

    Very small gaps (< ~1 minute) treated as re-weigh: alpha=1.
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    if dt_days <= (1.0 / 1440.0):  # <= 1 minute
        return 1.0, 0.0
    decay = math.pow(0.5, dt_days / half_life_days)
    if decay < 1e-12:
        decay = 0.0
    if decay > 1.0:
        decay = 1.0
    return 1.0 - decay, decay


@dataclass
class Point:
    id: Optional[int]
    logged_at: datetime
    weight: float
    note: Optional[str] = None
    body_fat: Optional[float] = None
    # filled by compute_trend
    trend: Optional[float] = None
    body_fat_trend: Optional[float] = None
    rate_lb_per_day: Optional[float] = None
    rate_lb_per_week: Optional[float] = None
    kcal_per_day: Optional[float] = None
    gap_days: Optional[float] = None
    alpha: Optional[float] = None

    @property
    def d(self) -> date:
        return self.logged_at.date()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.d.isoformat(),
            "logged_at": self.logged_at.isoformat(),
            "weight": self.weight,
            "note": self.note,
            "body_fat": self.body_fat,
            "trend": self.trend,
            "body_fat_trend": self.body_fat_trend,
            "rate_lb_per_day": self.rate_lb_per_day,
            "rate_lb_per_week": self.rate_lb_per_week,
            "kcal_per_day": self.kcal_per_day,
            "gap_days": self.gap_days,
            "alpha": self.alpha,
        }


def compute_trend(
    samples: Sequence[Tuple],
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> List[Point]:
    """Compute trend series.

    Each sample: (id|None, datetime_like, weight, note?, body_fat?)
    """
    pts: List[Point] = []
    for s in samples:
        sid = s[0]
        logged = parse_datetime(s[1])
        w = float(s[2])
        note = s[3] if len(s) > 3 else None
        bf = s[4] if len(s) > 4 else None
        if bf is not None and bf != "":
            try:
                bf = float(bf)
            except (TypeError, ValueError):
                bf = None
        else:
            bf = None
        pts.append(Point(id=sid, logged_at=logged, weight=w, note=note, body_fat=bf))

    pts.sort(key=lambda p: (p.logged_at, p.id if p.id is not None else 0))
    if not pts:
        return []

    prev_trend: Optional[float] = None
    prev_bf_trend: Optional[float] = None
    prev_at: Optional[datetime] = None
    prev_rate: Optional[float] = None

    for p in pts:
        if prev_trend is None or prev_at is None:
            p.trend = p.weight
            p.body_fat_trend = p.body_fat
            p.gap_days = 0.0
            p.alpha = 1.0
            p.rate_lb_per_day = 0.0
            p.rate_lb_per_week = 0.0
            p.kcal_per_day = 0.0
            prev_trend = p.trend
            prev_bf_trend = p.body_fat_trend
            prev_at = p.logged_at
            prev_rate = 0.0
            continue

        dt = days_between_dt(prev_at, p.logged_at)
        alpha, decay = time_alpha(dt, half_life_days)
        trend = alpha * p.weight + decay * prev_trend
        p.trend = trend
        p.gap_days = round(dt, 4)
        p.alpha = alpha

        if p.body_fat is not None:
            if prev_bf_trend is None:
                p.body_fat_trend = p.body_fat
            else:
                p.body_fat_trend = alpha * p.body_fat + decay * prev_bf_trend
        else:
            # carry forward smoothed BF when this sample has no reading
            p.body_fat_trend = prev_bf_trend

        if dt > (1.0 / 1440.0):
            inst_rate = (trend - prev_trend) / dt
            r_alpha, r_decay = time_alpha(dt, half_life_days)
            rate = inst_rate if prev_rate is None else (r_alpha * inst_rate + r_decay * prev_rate)
        else:
            rate = prev_rate if prev_rate is not None else 0.0

        p.rate_lb_per_day = rate
        p.rate_lb_per_week = rate * 7.0
        p.kcal_per_day = rate * KCAL_PER_LB

        prev_trend = trend
        if p.body_fat_trend is not None:
            prev_bf_trend = p.body_fat_trend
        prev_at = p.logged_at
        prev_rate = rate

    return pts


def summary(points: Sequence[Point]) -> dict:
    if not points:
        return {
            "count": 0,
            "latest_date": None,
            "latest_logged_at": None,
            "latest_weight": None,
            "latest_body_fat": None,
            "trend": None,
            "body_fat_trend": None,
            "rate_lb_per_day": None,
            "rate_lb_per_week": None,
            "kcal_per_day": None,
            "half_life_days": None,
        }
    last = points[-1]
    first = points[0]
    span_days = days_between_dt(first.logged_at, last.logged_at)
    return {
        "count": len(points),
        "first_date": first.d.isoformat(),
        "first_logged_at": first.logged_at.isoformat(),
        "latest_date": last.d.isoformat(),
        "latest_logged_at": last.logged_at.isoformat(),
        "latest_weight": last.weight,
        "latest_body_fat": last.body_fat,
        "trend": last.trend,
        "body_fat_trend": last.body_fat_trend,
        "rate_lb_per_day": last.rate_lb_per_day,
        "rate_lb_per_week": last.rate_lb_per_week,
        "kcal_per_day": last.kcal_per_day,
        "span_days": span_days,
        "net_trend_change": (
            (last.trend - first.trend)
            if last.trend is not None and first.trend is not None
            else None
        ),
    }
