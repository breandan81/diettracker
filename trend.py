"""Time-aware exponential smoothing for irregular weight samples.

Uses real elapsed time between weigh-ins (fractional days from timestamps),
not calendar-day counts — so automatic logging at 7am vs 9pm is handled
correctly.

    decay = 0.5 ** (dt_days / H)
    alpha = 1 - decay
    trend_t = alpha * weight_t + decay * trend_{t-1}

Body fat (when present) is smoothed with the same half-life.

    kcal/day ≈ rate_lb_per_day * 3500

Rate is a least-squares fit to the raw weigh-ins over a trailing window —
not a slope of the trend line, which understates badly until the EMA settles.
The window is RATE_WINDOW_DAYS, shorter when the series is young and wider
when a gap would otherwise leave too few points to fit. `rate_se_lb_per_day`
carries the standard error, so a young or sparse window reports a wide error
bar rather than a falsely confident number.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Sequence, Tuple, Union

KCAL_PER_LB = 3500.0
DEFAULT_HALF_LIFE_DAYS = 7.0

# Rate = least squares on the RAW weigh-ins over a trailing window.
#
# Deliberately NOT derived from the trend line. The trend's lag grows from zero
# to ~10 days while it settles, and differencing two points with different lags
# understates the slope — 31% of truth at day 8, still 91% at day 28. OLS has
# no warm-up: it is unbiased from the third weigh-in, and at matched delay it
# is also less noisy.
RATE_WINDOW_DAYS = 21.0
# Widen through a gap until a line can be fitted, but do not reach back forever
# after a long layoff — old points describe a different diet.
RATE_MIN_POINTS = 3
RATE_MAX_WINDOW_DAYS = 60.0
# Below this the window is a sliver and the slope is meaningless.
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
    rate_se_lb_per_day: Optional[float] = None
    rate_window_days: Optional[float] = None
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
            "rate_se_lb_per_day": self.rate_se_lb_per_day,
            "rate_window_days": self.rate_window_days,
            "gap_days": self.gap_days,
            "alpha": self.alpha,
        }


def _ols(
    times: Sequence[datetime], values: Sequence[float]
) -> Tuple[float, Optional[float]]:
    """Least-squares slope in units/day, with the standard error of that slope.

    sigma comes from this fit's own residuals (df = n - 2) rather than a figure
    pooled over the series: residuals about the TREND line would carry its
    warm-up ramp, which is not measurement noise and would inflate every error
    bar. Two points fit exactly, leaving no degrees of freedom and no error bar
    — which is honest, and those windows are flagged provisional anyway.
    """
    xs = [days_between_dt(times[0], t) for t in times]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(values) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return 0.0, None
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, values)) / sxx
    if n < 3:
        return slope, None
    intercept = my - slope * mx
    ss = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, values))
    return slope, math.sqrt(max(ss, 0.0) / (n - 2) / sxx)


def _rate_window(times: Sequence[datetime], i: int) -> int:
    """First index of the regression window ending at `i`.

    Normally the trailing RATE_WINDOW_DAYS. A gap can leave that window with
    too few points to fit a line — weigh in today after three weeks off and it
    holds exactly one — so it widens backwards until RATE_MIN_POINTS are in
    hand, stopping at RATE_MAX_WINDOW_DAYS so a long layoff does not regress
    today against a different era.
    """
    now = times[i]
    j = bisect_left(times, now - timedelta(days=RATE_WINDOW_DAYS))
    while i - j + 1 < RATE_MIN_POINTS and j > 0:
        if days_between_dt(times[j - 1], now) > RATE_MAX_WINDOW_DAYS:
            break
        j -= 1
    return j


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

            pass  # rate is fitted below, once every trend is known

        prev_trend = p.trend
        if p.body_fat_trend is not None:
            prev_bf_trend = p.body_fat_trend
        prev_at = p.logged_at
        if p.id is not None:
            ema[p.id] = p

    # Rate: least squares on the raw weigh-ins (see RATE_WINDOW_DAYS).
    times = [a.logged_at for a in anchors]

    for i, p in enumerate(anchors):
        j = _rate_window(times, i)
        n = i - j + 1
        span = days_between_dt(times[j], times[i])
        if n >= 2 and span >= _MIN_RATE_SPAN_DAYS:
            slope, se = _ols(times[j : i + 1], [a.weight for a in anchors[j : i + 1]])
            p.rate_lb_per_day = slope
            p.rate_se_lb_per_day = se
            p.rate_provisional = n < RATE_MIN_POINTS
        else:
            p.rate_lb_per_day = 0.0
            p.rate_se_lb_per_day = None
            p.rate_provisional = True
        p.rate_lb_per_week = p.rate_lb_per_day * 7.0
        p.kcal_per_day = p.rate_lb_per_day * KCAL_PER_LB
        p.rate_window_days = round(span, 4)

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
        p.rate_se_lb_per_day = src.rate_se_lb_per_day
        p.rate_window_days = src.rate_window_days
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
            "rate_se_lb_per_day": None,
            "rate_window_days": None,
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
        "rate_se_lb_per_day": last.rate_se_lb_per_day,
        "rate_window_days": last.rate_window_days,
        "span_days": span_days,
        "net_trend_change": (
            (last.trend - first.trend)
            if last.trend is not None and first.trend is not None
            else None
        ),
    }
