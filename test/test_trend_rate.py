"""Rate calculation: trend line differenced over a lookback window.

    python3 test/test_trend_rate.py
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trend import (  # noqa: E402
    RATE_LOOKBACK_DAYS,
    _MIN_RATE_SPAN_DAYS,
    _trend_at,
    compute_trend,
    summary,
)

failed = 0
passed = 0


def check(cond, msg):
    global failed, passed
    if cond:
        passed += 1
    else:
        print("FAIL:", msg)
        failed += 1


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def series(weights, step_days=1.0, start=T0):
    return [
        (i + 1, start + timedelta(days=i * step_days), w, None, None)
        for i, w in enumerate(weights)
    ]


# --- _trend_at ------------------------------------------------------------

times = [T0, T0 + timedelta(days=2), T0 + timedelta(days=4)]
trends = [200.0, 198.0, 196.0]

check(_trend_at(times, trends, T0 + timedelta(days=1)) == 199.0, "interpolates midway")
check(_trend_at(times, trends, T0 + timedelta(days=2)) == 198.0, "exact hit on an anchor")
check(_trend_at(times, trends, T0 + timedelta(days=3)) == 197.0, "interpolates second span")
check(_trend_at(times, trends, T0) == 200.0, "first anchor")
check(_trend_at(times, trends, T0 - timedelta(days=1)) is None, "before the series -> None")
check(_trend_at(times, trends, T0 + timedelta(days=9)) == 196.0, "past the end clamps to last")
check(_trend_at([], [], T0) is None, "empty history -> None")
# Two anchors at the same instant must not divide by zero. bisect lands past
# both, so the later (most recent) trend wins — which is the right answer.
check(_trend_at([T0, T0], [200.0, 199.0], T0) == 199.0, "zero-width span does not blow up")
check(_trend_at([T0, T0, T0 + timedelta(days=2)], [200.0, 199.0, 197.0],
                T0 + timedelta(days=1)) == 198.0, "interpolates past a zero-width span")


# --- a steady decline recovers its own slope ------------------------------
# 90 days is long enough for the EMA to settle; then trend slope == true slope.
RATE = -0.32
pts = compute_trend(series([200.0 + RATE * i for i in range(90)]))
last = pts[-1]
check(
    close(last.rate_lb_per_day, RATE, 0.01),
    f"settled linear decline recovers {RATE}, got {last.rate_lb_per_day}",
)
check(close(last.rate_lb_per_week, last.rate_lb_per_day * 7.0), "week = day * 7")
check(close(last.kcal_per_day, last.rate_lb_per_day * 3500.0), "kcal = day * 3500")
check(last.rate_provisional is False, "long series is not provisional")

# Flat weight means zero rate, whatever the noise-free history.
flat = compute_trend(series([185.0] * 30))
check(close(flat[-1].rate_lb_per_day, 0.0, 1e-9), "flat series -> zero rate")

# Gaining reverses the sign.
up = compute_trend(series([200.0 + 0.25 * i for i in range(60)]))
check(up[-1].rate_lb_per_day > 0, "weight gain gives a positive rate")


# --- the lookback window is honoured --------------------------------------
pts = compute_trend(series([200.0 - 0.5 * i for i in range(40)]))
by_time = {p.logged_at: p for p in pts}
p_last = pts[-1]
base = by_time[p_last.logged_at - timedelta(days=RATE_LOOKBACK_DAYS)]
expected = (p_last.trend - base.trend) / RATE_LOOKBACK_DAYS
check(
    close(p_last.rate_lb_per_day, expected, 1e-9),
    "rate equals (trend now - trend at lookback) / lookback",
)

# No second filter: the rate depends only on the two endpoints, so an identical
# trend pair must give an identical rate regardless of the path between them.
check(
    close(
        compute_trend(series([190.0] * 20))[-1].rate_lb_per_day, 0.0, 1e-9
    ),
    "no residual rate carried from earlier history",
)


# --- provisional flag -----------------------------------------------------
short = compute_trend(series([200.0, 199.0, 198.0]))  # 2-day span < 7
check(short[-1].rate_provisional is True, "series shorter than lookback is provisional")
check(short[-1].rate_lb_per_day < 0, "short series still reports a direction")

single = compute_trend(series([200.0]))
check(single[0].rate_lb_per_day == 0.0, "one point has no slope")
check(single[0].rate_provisional is True, "one point is provisional")

check(compute_trend([]) == [], "empty input -> empty output")
check(summary([])["rate_provisional"] is True, "empty summary is provisional")

long = compute_trend(series([200.0 - 0.1 * i for i in range(30)]))
check(long[-1].rate_provisional is False, "series longer than lookback is not provisional")
check(summary(long)["rate_provisional"] is False, "summary carries the flag")


# --- sub-minimum spans must not blow the quotient up ----------------------
# Two anchors minutes apart: the old code divided by dt and produced nonsense.
tight = compute_trend(
    [
        (1, T0, 200.0, None, None),
        (2, T0 + timedelta(minutes=3), 203.0, None, None),
    ]
)
check(
    abs(tight[-1].rate_lb_per_day) < 1e-9,
    f"minutes-apart anchors give no rate, got {tight[-1].rate_lb_per_day}",
)
check(tight[-1].rate_provisional is True, "sub-minimum span is provisional")
check(_MIN_RATE_SPAN_DAYS > 0, "minimum span is positive")


# --- irregular sampling ---------------------------------------------------
# Same underlying decline, sampled unevenly, should land near the same rate.
even = compute_trend(series([200.0 - 0.3 * i for i in range(60)]))
odd_days = [0, 0.4, 1, 2.7, 3, 5, 6.2, 7, 9, 11, 12.5, 14, 17, 19, 21, 24,
            27, 30, 33, 36, 40, 44, 48, 52, 56, 59]
odd = compute_trend(
    [
        (i + 1, T0 + timedelta(days=d), 200.0 - 0.3 * d, None, None)
        for i, d in enumerate(odd_days)
    ]
)
check(
    abs(even[-1].rate_lb_per_day - odd[-1].rate_lb_per_day) < 0.02,
    f"irregular sampling tracks even: {even[-1].rate_lb_per_day:.4f} "
    f"vs {odd[-1].rate_lb_per_day:.4f}",
)

# A gap straddling the lookback instant interpolates rather than snapping.
gapped = compute_trend(
    [(1, T0, 205.0, None, None)]
    + [(i + 2, T0 + timedelta(days=20 + i), 197.0 - 0.3 * i, None, None) for i in range(20)]
)
check(gapped[-1].rate_lb_per_day < 0, "gapped series still yields a sane direction")
check(math.isfinite(gapped[-1].rate_lb_per_day), "gapped series stays finite")


# --- coalesced duplicates inherit the anchor's rate ------------------------
dup = compute_trend(
    [(i + 1, T0 + timedelta(days=i), 200.0 - 0.3 * i, None, None) for i in range(20)]
    + [(999, T0 + timedelta(days=19, seconds=30), 200.0 - 0.3 * 19, None, None)]
)
tail = [p for p in dup if p.id == 999][0]
anchor = [p for p in dup if p.id == 20][0]
check(close(tail.rate_lb_per_day, anchor.rate_lb_per_day), "duplicate inherits rate")
check(tail.rate_provisional == anchor.rate_provisional, "duplicate inherits the flag")
check("rate_provisional" in dup[-1].to_dict(), "to_dict exposes the flag")


print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
