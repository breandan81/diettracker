"""Rate calculation: trend line differenced over a lookback window.

    python3 test/test_trend_rate.py
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trend import (  # noqa: E402
    RATE_MAX_WINDOW_DAYS,
    RATE_MIN_POINTS,
    RATE_WINDOW_DAYS,
    _MIN_RATE_SPAN_DAYS,
    _ols,
    _rate_window,
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


# --- _ols -----------------------------------------------------------------

def at(days):
    return T0 + timedelta(days=days)


slope, se = _ols([at(0), at(1), at(2)], [200.0, 199.0, 198.0])
check(close(slope, -1.0), f"clean slope, got {slope}")
check(se is not None and se < 1e-9, "perfect fit has no error")
slope, se = _ols([at(0), at(0)], [200.0, 199.0])
check(slope == 0.0 and se is None, "zero spread cannot define a slope")
slope, se = _ols([at(0), at(2)], [200.0, 198.0])
check(close(slope, -1.0) and se is None, "two points fit exactly, no df for an error bar")
# Irregular spacing must not tilt the fit.
slope, _se = _ols([at(0), at(0.5), at(7)], [200.0, 199.75, 196.5])
check(close(slope, -0.5), f"irregular spacing fits correctly, got {slope}")


# --- _rate_window: the gap-tolerance rule ---------------------------------

# Dense daily data: window is exactly RATE_WINDOW_DAYS, nothing wider.
dense = [at(i) for i in range(40)]
j = _rate_window(dense, len(dense) - 1)
span = (dense[-1] - dense[j]).total_seconds() / 86400
check(span <= RATE_WINDOW_DAYS, f"dense window capped at {RATE_WINDOW_DAYS}d, got {span}")
check(span >= RATE_WINDOW_DAYS - 1.01, f"dense window uses the full {RATE_WINDOW_DAYS}d, got {span}")

# THE GAP CASE: today plus a cluster three weeks back. A fixed window would
# hold one point; it must widen until a line can be fitted.
gap = [at(0), at(1), at(2), at(30)]
j = _rate_window(gap, 3)
check(len(gap) - j >= RATE_MIN_POINTS, f"widens through a gap, got {len(gap)-j} points")

# Only two points ever: use them, do not invent a third.
j = _rate_window([at(0), at(30)], 1)
check(j == 0, "two-point series uses both")

# A long layoff must not regress today against a different era.
stale = [at(0), at(1), at(2), at(400)]
j = _rate_window(stale, 3)
span = (stale[-1] - stale[j]).total_seconds() / 86400
check(span <= RATE_MAX_WINDOW_DAYS, f"layoff capped at {RATE_MAX_WINDOW_DAYS}d, got {span}")


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


# --- the fit matches a plain regression over the window -------------------
pts = compute_trend(series([200.0 - 0.5 * i for i in range(40)]))
p_last = pts[-1]
inwin = [p for p in pts if (p_last.logged_at - p.logged_at).days <= RATE_WINDOW_DAYS]
expected, _ = _ols([p.logged_at for p in inwin], [p.weight for p in inwin])
check(close(p_last.rate_lb_per_day, expected, 1e-9), "rate is OLS over the window")
check(p_last.rate_window_days <= RATE_WINDOW_DAYS + 1e-9, "window is reported and capped")


# --- no warm-up: unbiased from the very first days ------------------------
# This is the whole reason for regressing raw weights instead of the trend.
for n in (4, 8, 14, 21, 40, 70):
    pts = compute_trend(series([200.0 - 0.4 * i for i in range(n)]))
    got = pts[-1].rate_lb_per_day
    check(close(got, -0.4, 1e-6), f"day {n} recovers -0.4 exactly, got {got:.4f}")


# --- standard error -------------------------------------------------------
import random  # noqa: E402

random.seed(11)
noisy = compute_trend(
    series([200.0 - 0.3 * i + random.gauss(0, 1.0) for i in range(40)])
)
se_long = noisy[-1].rate_se_lb_per_day
check(se_long is not None and se_long > 0, "noisy series reports a standard error")
short_noisy = compute_trend(
    series([200.0 - 0.3 * i + random.gauss(0, 1.0) for i in range(6)])
)
check(
    short_noisy[-1].rate_se_lb_per_day > se_long,
    "a shorter window reports a WIDER error bar",
)
clean = compute_trend(series([200.0 - 0.3 * i for i in range(40)]))
check(
    clean[-1].rate_se_lb_per_day is not None
    and clean[-1].rate_se_lb_per_day < 1e-6,
    "noise-free data has an essentially zero error bar",
)


# --- provisional flag -----------------------------------------------------
# Now means only "too few points to fit a line", not "biased".
two = compute_trend(series([200.0, 199.0]))
check(two[-1].rate_provisional is True, f"{RATE_MIN_POINTS - 1} points is provisional")
check(close(two[-1].rate_lb_per_day, -1.0), "two points still give the secant slope")
check(two[-1].rate_se_lb_per_day is None, "two points have no error bar to report")
three = compute_trend(series([200.0, 199.0, 198.0]))
check(three[-1].rate_provisional is False, f"{RATE_MIN_POINTS} points is enough to fit")
check(close(three[-1].rate_lb_per_day, -1.0), "three points fit the line exactly")

single = compute_trend(series([200.0]))
check(single[0].rate_lb_per_day == 0.0, "one point has no slope")
check(single[0].rate_provisional is True, "one point is provisional")

check(compute_trend([]) == [], "empty input -> empty output")
check(summary([])["rate_provisional"] is True, "empty summary is provisional")

long = compute_trend(series([200.0 - 0.1 * i for i in range(30)]))
check(long[-1].rate_provisional is False, "long series is not provisional")
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

# Gap tolerance end-to-end: a lone weigh-in after three weeks off still gets a
# rate, by reaching back to the cluster before the gap.
after_gap = compute_trend(
    [(i + 1, T0 + timedelta(days=i), 200.0 - 0.3 * i, None, None) for i in range(5)]
    + [(99, T0 + timedelta(days=26), 192.0, None, None)]
)
check(math.isfinite(after_gap[-1].rate_lb_per_day), "post-gap weigh-in still gets a rate")
check(after_gap[-1].rate_lb_per_day < 0, "post-gap rate has the right sign")
check(after_gap[-1].rate_window_days > RATE_WINDOW_DAYS, "window widened past the gap")

# ...but a year off does not regress today against last year.
stale = compute_trend(
    [(i + 1, T0 + timedelta(days=i), 200.0 - 0.3 * i, None, None) for i in range(5)]
    + [(99, T0 + timedelta(days=400), 192.0, None, None)]
)
check(
    stale[-1].rate_window_days <= RATE_MAX_WINDOW_DAYS or stale[-1].rate_provisional,
    "a long layoff is capped or flagged, never silently stale",
)


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
check("rate_se_lb_per_day" in dup[-1].to_dict(), "to_dict exposes the error bar")
check(close(tail.rate_se_lb_per_day, anchor.rate_se_lb_per_day), "duplicate inherits SE")


print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
