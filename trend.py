"""Time-aware exponential smoothing for irregular weight samples.

Classic EMA assumes one sample per day. Real life is gappy (every other day,
vacations, etc.). We parameterize smoothing by half-life H (days): after H days
without an observation, the previous trend retains 50% weight and the new
weigh-in takes the other 50%.

    decay = 0.5 ** (dt_days / H)
    alpha = 1 - decay
    trend_t = alpha * weight_t + decay * trend_{t-1}

Rate (lb/day) is the successive change in trend divided by dt, then smoothed
with the same half-life so a single noisy gap doesn't spike the calorie estimate.

    kcal/day ≈ rate_lb_per_day * 3500   (≈ energy density of body fat)

Positive rate / kcal → surplus (gaining). Negative → deficit (losing).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, List, Optional, Sequence, Tuple, Union

KCAL_PER_LB = 3500.0
DEFAULT_HALF_LIFE_DAYS = 7.0


DateLike = Union[date, datetime, str]


def parse_date(d: DateLike) -> date:
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    # accept YYYY-MM-DD or full ISO
    if "T" in s:
        s = s.split("T", 1)[0]
    return date.fromisoformat(s[:10])


def days_between(a: date, b: date) -> float:
    return float((b - a).days)


@dataclass
class Point:
    id: Optional[int]
    d: date
    weight: float
    note: Optional[str] = None
    # filled by compute_trend
    trend: Optional[float] = None
    rate_lb_per_day: Optional[float] = None
    rate_lb_per_week: Optional[float] = None
    kcal_per_day: Optional[float] = None
    gap_days: Optional[float] = None
    alpha: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "date": self.d.isoformat(),
            "weight": self.weight,
            "note": self.note,
            "trend": self.trend,
            "rate_lb_per_day": self.rate_lb_per_day,
            "rate_lb_per_week": self.rate_lb_per_week,
            "kcal_per_day": self.kcal_per_day,
            "gap_days": self.gap_days,
            "alpha": self.alpha,
        }


def time_alpha(dt_days: float, half_life_days: float) -> Tuple[float, float]:
    """Return (alpha, decay) for a gap of dt_days given half-life H.

    alpha is the weight on the new observation; decay on the previous trend.
    For dt <= 0 we treat as a same-day re-weigh: full replace (alpha=1).
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    if dt_days <= 0:
        return 1.0, 0.0
    decay = math.pow(0.5, dt_days / half_life_days)
    # clamp numerical noise
    if decay < 1e-12:
        decay = 0.0
    if decay > 1.0:
        decay = 1.0
    return 1.0 - decay, decay


def compute_trend(
    samples: Sequence[Tuple],  # (id|None, date_like, weight, note?)
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> List[Point]:
    """Compute trend, rate, and kcal series for chronological samples.

    Samples may be irregularly spaced. Order is sorted by date; if multiple
    entries share a date, later list order wins as a same-day update (alpha=1
    relative to previous same-day or gap=0).
    """
    pts: List[Point] = []
    for s in samples:
        sid = s[0]
        d = parse_date(s[1])
        w = float(s[2])
        note = s[3] if len(s) > 3 else None
        pts.append(Point(id=sid, d=d, weight=w, note=note))

    pts.sort(key=lambda p: (p.d, p.id if p.id is not None else 0))

    if not pts:
        return []

    prev_trend: Optional[float] = None
    prev_date: Optional[date] = None
    prev_rate: Optional[float] = None

    for p in pts:
        if prev_trend is None or prev_date is None:
            p.trend = p.weight
            p.gap_days = 0.0
            p.alpha = 1.0
            p.rate_lb_per_day = 0.0
            p.rate_lb_per_week = 0.0
            p.kcal_per_day = 0.0
            prev_trend = p.trend
            prev_date = p.d
            prev_rate = 0.0
            continue

        dt = days_between(prev_date, p.d)
        alpha, decay = time_alpha(dt, half_life_days)
        trend = alpha * p.weight + decay * prev_trend
        p.trend = trend
        p.gap_days = dt
        p.alpha = alpha

        # Instantaneous trend slope over the gap. Same-day re-weigh: no new
        # slope info — keep previous smoothed rate.
        if dt > 0:
            inst_rate = (trend - prev_trend) / dt
            r_alpha, r_decay = time_alpha(dt, half_life_days)
            if prev_rate is None:
                rate = inst_rate
            else:
                rate = r_alpha * inst_rate + r_decay * prev_rate
        else:
            rate = prev_rate if prev_rate is not None else 0.0

        p.rate_lb_per_day = rate
        p.rate_lb_per_week = rate * 7.0
        p.kcal_per_day = rate * KCAL_PER_LB

        prev_trend = trend
        prev_date = p.d
        prev_rate = rate

    return pts


def summary(points: Sequence[Point]) -> dict:
    """Latest-state summary for the UI header cards."""
    if not points:
        return {
            "count": 0,
            "latest_date": None,
            "latest_weight": None,
            "trend": None,
            "rate_lb_per_day": None,
            "rate_lb_per_week": None,
            "kcal_per_day": None,
            "half_life_days": None,
        }
    last = points[-1]
    first = points[0]
    span_days = days_between(first.d, last.d)
    return {
        "count": len(points),
        "first_date": first.d.isoformat(),
        "latest_date": last.d.isoformat(),
        "latest_weight": last.weight,
        "trend": last.trend,
        "rate_lb_per_day": last.rate_lb_per_day,
        "rate_lb_per_week": last.rate_lb_per_week,
        "kcal_per_day": last.kcal_per_day,
        "span_days": span_days,
        "net_trend_change": (last.trend - first.trend) if last.trend is not None and first.trend is not None else None,
    }
