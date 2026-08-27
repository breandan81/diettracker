"""Time-aware exponential smoothing for irregular weight samples.

Uses real elapsed time between weigh-ins (fractional days from timestamps),
not calendar-day counts — so automatic logging at 7am vs 9pm is handled
correctly.

    decay = 0.5 ** (dt_days / H)
    alpha = 1 - decay
    trend_t = alpha * weight_t + decay * trend_{t-1}

Body fat (when present) is smoothed with the same half-life.

    kcal/day ≈ rate_lb_per_day * 3500

Rate is the trend line differenced over RATE_LOOKBACK_DAYS, interpolating
the trend at the lookback instant since weigh-ins are irregular. The trend
is already smoothed, so no second filter is applied to the slope — doing so
only adds lag. When the series is shorter than the lookback the rate is
measured over whatever span exists and flagged `rate_provisional`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from bisect import bisect_right
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple, Union

KCAL_PER_LB = 3500.0
DEFAULT_HALF_LIFE_DAYS = 7.0

# Rate = how far the trend line moved over the last RATE_LOOKBACK_DAYS.
# Note this is NOT a 7-day measurement: each trend endpoint is itself a
# backward-weighted average with a ~10-day time constant, so the estimate is
# effectively centred ~14 days back. That is the price of the smoothing, and
# it is why nothing further is applied on top.
RATE_LOOKBACK_DAYS = 7.0
# Below this the trend has barely moved and the quotient blows up.
_MIN_RATE_SPAN_DAYS = 0.5

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

    Tiny gaps are handled by coalescing duplicate samples before EMA — do not
    use alpha=1 here (that used to snap the trend to raw weight on spam posts).
    """
    if half_life_days <= 0:
        raise ValueError("half_life_days must be > 0")
    if dt_days <= 0:
        # Identical timestamp after coalesce shouldn't happen; keep previous.
        return 0.0, 1.0
    decay = math.pow(0.5, dt_days / half_life_days)
    if decay < 1e-12:
        decay = 0.0
    if decay > 1.0:
        decay = 1.0
    return 1.0 - decay, decay


# Collapse auto-log spam / same-session repeats before smoothing.
_COALESCE_WINDOW_DAYS = 2.0 / 1440.0  # 2 minutes
_COALESCE_WEIGHT_LB = 0.15


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
    rate_provisional: bool = False
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
            "rate_provisional": self.rate_provisional,
            "gap_days": self.gap_days,
            "alpha": self.alpha,
        }


def _trend_at(
    times: Sequence[datetime], trends: Sequence[float], when: datetime
) -> Optional[float]:
    """Trend value at an arbitrary instant, linearly interpolated.

    Weigh-ins are irregular, so the lookback instant rarely lands on one.
    The trend is a continuous curve, so interpolating between the two
    bracketing anchors is more faithful than snapping to the nearest.

    Returns None when `when` predates the series — the caller then falls
    back to the oldest anchor it has and flags the result provisional.
    """
    if not times or when < times[0]:
        return None
    i = bisect_right(times, when) - 1
    if i >= len(times) - 1:
        return trends[-1]
    span = (times[i + 1] - times[i]).total_seconds()
    if span <= 0:
        return trends[i]
    f = (when - times[i]).total_seconds() / span
    return trends[i] + f * (trends[i + 1] - trends[i])


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

    # Coalesce reconnect spam: within 2 minutes + ~same weight → one EMA update.
    # Map every sample id → the anchor id that owns the EMA step.
    anchors: List[Point] = []
    sample_to_anchor: dict = {}  # sample id -> anchor Point
    for p in pts:
        if anchors:
            prev = anchors[-1]
            dt = days_between_dt(prev.logged_at, p.logged_at)
            same_w = abs(p.weight - prev.weight) <= _COALESCE_WEIGHT_LB
            if dt <= _COALESCE_WINDOW_DAYS and same_w:
                if prev.body_fat is None and p.body_fat is not None:
                    prev.body_fat = p.body_fat
                if p.id is not None:
                    sample_to_anchor[p.id] = prev
                continue
        anchors.append(p)
        if p.id is not None:
            sample_to_anchor[p.id] = p

    # Run EMA on anchors only
    prev_trend: Optional[float] = None
    prev_bf_trend: Optional[float] = None
    prev_at: Optional[datetime] = None
    ema: dict = {}  # anchor id -> filled Point fields
    hist_times: List[datetime] = []  # anchor trend curve, for the rate lookback
    hist_trends: List[float] = []

    for p in anchors:
        if prev_trend is None or prev_at is None:
            p.trend = p.weight
            p.body_fat_trend = p.body_fat
            p.gap_days = 0.0
            p.alpha = 1.0
            p.rate_lb_per_day = 0.0
            p.rate_lb_per_week = 0.0
            p.kcal_per_day = 0.0
            p.rate_provisional = True  # one point is not a slope
        else:
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
                p.body_fat_trend = prev_bf_trend

            # Slope of the already-smoothed trend over the lookback window.
            target = p.logged_at - timedelta(days=RATE_LOOKBACK_DAYS)
            base = _trend_at(hist_times, hist_trends, target)
            if base is None:
                # Series younger than the lookback: use everything there is.
                base, base_at = hist_trends[0], hist_times[0]
                provisional = True
            else:
                base_at = target
                provisional = False

            span = days_between_dt(base_at, p.logged_at)
            if span >= _MIN_RATE_SPAN_DAYS:
                rate = (trend - base) / span
            else:
                rate = 0.0
                provisional = True

            p.rate_lb_per_day = rate
            p.rate_lb_per_week = rate * 7.0
            p.kcal_per_day = rate * KCAL_PER_LB
            p.rate_provisional = provisional

        prev_trend = p.trend
        if p.body_fat_trend is not None:
            prev_bf_trend = p.body_fat_trend
        prev_at = p.logged_at
        hist_times.append(p.logged_at)
        hist_trends.append(p.trend)
        if p.id is not None:
            ema[p.id] = p

    # Copy EMA onto every sample (duplicates inherit their anchor's state)
    for p in pts:
        anchor = sample_to_anchor.get(p.id) if p.id is not None else None
        if anchor is None:
            continue
        src = ema.get(anchor.id, anchor)
        p.trend = src.trend
        p.body_fat_trend = src.body_fat_trend
        p.rate_lb_per_day = src.rate_lb_per_day
        p.rate_lb_per_week = src.rate_lb_per_week
        p.kcal_per_day = src.kcal_per_day
        p.rate_provisional = src.rate_provisional
        if src is p:
            continue  # already filled as anchor
        p.gap_days = 0.0
        p.alpha = 0.0  # marks coalesced duplicate in UI/history

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
            "rate_provisional": True,
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
        "rate_provisional": last.rate_provisional,
        "span_days": span_days,
        "net_trend_change": (
            (last.trend - first.trend)
            if last.trend is not None and first.trend is not None
            else None
        ),
    }
