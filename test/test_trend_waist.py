"""Waist smoothing: same half-life as weight, but on its own clock.

    python3 test/test_trend_waist.py
"""
import math
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from trend import (  # noqa: E402
    DEFAULT_HALF_LIFE_DAYS,
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


def close(a, b, eps=1e-9):
    return a is not None and b is not None and abs(a - b) < eps


BASE = datetime(2026, 1, 1, 7, 0, tzinfo=timezone.utc)


def at(days):
    return (BASE + timedelta(days=days)).isoformat()


def series(rows, half_life=DEFAULT_HALF_LIFE_DAYS):
    """rows: (day_offset, weight, body_fat, waist)"""
    samples = [
        (i + 1, at(d), w, None, bf, waist) for i, (d, w, bf, waist) in enumerate(rows)
    ]
    return compute_trend(samples, half_life_days=half_life)


# --- optional means optional: nothing breaks when waist is never supplied ---
pts = series([(i, 200.0 - 0.2 * i, 25.0, None) for i in range(10)])
check(all(p.waist is None for p in pts), "waist stays None when never logged")
check(all(p.waist_trend is None for p in pts), "waist trend stays None")
check(summary(pts)["latest_waist"] is None, "summary latest_waist None")
check(summary(pts)["waist_trend"] is None, "summary waist_trend None")
check(summary(pts)["latest_waist_at"] is None, "summary latest_waist_at None")
check("waist" in pts[0].to_dict(), "to_dict exposes waist")
check("waist_trend" in pts[0].to_dict(), "to_dict exposes waist_trend")

# Short tuples (no waist element at all) must still parse — the ESP32 posts
# weight+BF only, and the legacy single-user path builds 5-tuples.
short = compute_trend([(1, at(0), 200.0, None, 25.0), (2, at(1), 199.0, None, 24.9)])
check(short[0].waist is None, "5-tuple sample parses with waist None")
check(short[1].waist_trend is None, "5-tuple sample has no waist trend")
tiny = compute_trend([(1, at(0), 200.0)])
check(tiny[0].waist is None, "3-tuple sample still parses")

# --- blank/garbage waist values are dropped, not crashed on ---
junk = compute_trend(
    [
        (1, at(0), 200.0, None, None, ""),
        (2, at(1), 199.0, None, None, "not a number"),
        (3, at(2), 198.0, None, None, "34.5"),
    ]
)
check(junk[0].waist is None, "empty string waist → None")
check(junk[1].waist is None, "unparseable waist → None")
check(close(junk[2].waist, 34.5), "numeric string waist → float")

# --- first measurement seeds the trend exactly ---
pts = series([(0, 200.0, None, 36.0), (1, 199.5, None, None)])
check(close(pts[0].waist_trend, 36.0), "first waist seeds its own trend")

# --- carried forward, not decayed, on rows without a measurement ---
pts = series([(0, 200.0, None, 36.0)] + [(i, 200.0 - 0.2 * i, None, None) for i in range(1, 15)])
check(
    all(close(p.waist_trend, 36.0) for p in pts),
    "waist trend holds flat across weigh-ins with no tape",
)
check(all(p.waist is None for p in pts[1:]), "carrying forward does not invent raw values")

# --- alpha comes from the waist gap, not the weigh-in gap ---
# Daily weigh-ins, waist every 7 days. A 7-day gap at H=7 is exactly one
# half-life, so the second reading must land halfway between the two.
rows = []
for i in range(15):
    rows.append((i, 200.0 - 0.2 * i, None, 36.0 if i == 0 else (34.0 if i == 7 else None)))
pts = series(rows)
second = [p for p in pts if p.waist == 34.0][0]
check(close(second.waist_trend, 35.0), f"7d gap at H=7 → halfway, got {second.waist_trend}")

# The bug this guards against: if waist borrowed the weight gap (1 day), alpha
# would be ~0.094 and the trend would barely move off 36.
check(second.waist_trend < 35.9, "waist does not use the one-day weigh-in alpha")

# --- daily waist readings behave exactly like the weight EMA ---
rows = [(i, 200.0, None, 36.0 - 0.1 * i) for i in range(20)]
pts = series(rows)
alpha = 1 - 0.5 ** (1 / DEFAULT_HALF_LIFE_DAYS)
expect = 36.0
for i in range(1, 20):
    expect = alpha * (36.0 - 0.1 * i) + (1 - alpha) * expect
check(close(pts[-1].waist_trend, expect, 1e-9), "daily waist matches the weight EMA formula")
check(pts[-1].waist_trend > pts[-1].waist, "trend lags a falling waist, as it should")

# --- half-life is honoured ---
rows = [(0, 200.0, None, 40.0), (14, 200.0, None, 30.0)]
slow = series(rows, half_life=14.0)
fast = series(rows, half_life=3.5)
check(close(slow[-1].waist_trend, 35.0), f"H=14 over 14d → halfway, got {slow[-1].waist_trend}")
check(fast[-1].waist_trend < 31.0, "short half-life snaps closer to the new reading")

# --- summary reports the newest real measurement, not the newest row ---
rows = [(0, 200.0, None, 36.0), (10, 198.0, None, 34.0)] + [
    (i, 198.0, None, None) for i in range(11, 20)
]
pts = series(rows)
summ = summary(pts)
check(close(summ["latest_waist"], 34.0), "latest_waist skips back over rows with no tape")
check(summ["latest_waist_at"].startswith("2026-01-11"), f"latest_waist_at {summ['latest_waist_at']}")
check(close(summ["waist_trend"], pts[-1].waist_trend), "summary waist_trend is the carried trend")

# --- a waist logged before any weight still works ---
pts = series([(0, 200.0, None, 36.0), (0.5, 200.5, None, None)])
check(close(pts[0].waist_trend, 36.0), "waist on the very first row is fine")

# --- coalesced duplicates ---
# Reconnect spam within 2 minutes at the same weight collapses to one EMA step.
# A waist typed onto the duplicate must not be lost, and the duplicate row must
# report the anchor's trend.
dup = compute_trend(
    [
        (1, at(0), 200.0, None, 25.0, None),
        (2, at(1), 199.0, None, 25.0, None),
        (3, at(2), 198.0, None, 25.0, None),
        (999, at(2 + 30.0 / 86400), 198.0, None, 25.0, 35.5),
    ]
)
tail = [p for p in dup if p.id == 999][0]
anchor = [p for p in dup if p.id == 3][0]
check(close(anchor.waist, 35.5), "waist on a coalesced duplicate moves onto the anchor")
check(close(anchor.waist_trend, 35.5), "anchor smooths the salvaged waist")
check(close(tail.waist_trend, anchor.waist_trend), "duplicate inherits the waist trend")
check(close(summary(dup)["latest_waist"], 35.5), "summary sees the salvaged waist")

# A duplicate must not double-count a waist the anchor already has.
dup2 = compute_trend(
    [
        (1, at(0), 200.0, None, None, 36.0),
        (2, at(7), 199.0, None, None, 34.0),
        (999, at(7 + 30.0 / 86400), 199.0, None, None, 34.0),
    ]
)
anchor2 = [p for p in dup2 if p.id == 2][0]
check(close(anchor2.waist_trend, 35.0), "duplicate does not apply a second EMA step")

# --- waist does not disturb weight or body fat ---
rows = [(i, 200.0 - 0.2 * i, 25.0 - 0.01 * i, 36.0 if i % 5 == 0 else None) for i in range(20)]
withw = series(rows)
without = series([(d, w, bf, None) for d, w, bf, _ in rows])
check(
    all(close(a.trend, b.trend) for a, b in zip(withw, without)),
    "weight trend is untouched by waist",
)
check(
    all(close(a.body_fat_trend, b.body_fat_trend) for a, b in zip(withw, without)),
    "body-fat trend is untouched by waist",
)
check(
    all(close(a.rate_lb_per_day, b.rate_lb_per_day) for a, b in zip(withw, without)),
    "rate is untouched by waist",
)

# ==========================================================================
# Waist-only entries: rows with a tape measurement and no weight at all.
# ==========================================================================

def entry(i, day, weight, bf, waist):
    return (i, at(day), weight, None, bf, waist)


# --- the weight trend passes straight through, undisturbed ---
base = [entry(i + 1, i, 200.0 - 0.25 * i, 25.0, None) for i in range(15)]
plain = compute_trend(base)
mixed = compute_trend(base + [entry(900, 7.5, None, None, 35.0)])
by_id = {p.id: p for p in mixed}
for p in plain:
    check(close(by_id[p.id].trend, p.trend), f"weight trend unchanged at id {p.id}")
    check(close(by_id[p.id].rate_lb_per_day, p.rate_lb_per_day), f"rate unchanged at id {p.id}")
    check(
        close(by_id[p.id].rate_se_lb_per_day, p.rate_se_lb_per_day)
        or (by_id[p.id].rate_se_lb_per_day is None and p.rate_se_lb_per_day is None),
        f"error bar unchanged at id {p.id}",
    )
check(len(mixed) == len(plain) + 1, "the waist-only entry is its own row")

# --- the waist-only row reports state, and invents nothing ---
wo = by_id[900]
check(wo.weight is None, "waist-only row has no weight")
check(wo.body_fat is None, "waist-only row has no body fat")
check(close(wo.waist, 35.0), "waist-only row keeps its measurement")
check(close(wo.waist_trend, 35.0), "first waist seeds the trend from a weight-less row")
check(wo.trend is not None, "waist-only row carries the weight trend forward")
check(close(wo.trend, by_id[8].trend), "carried trend is the previous weigh-in's")
check(wo.gap_days is None, "waist-only row has no weigh-in gap")
check(wo.alpha is None, "waist-only row has no weight alpha")
check(close(wo.rate_lb_per_day, by_id[8].rate_lb_per_day), "carries the standing rate")
check(close(wo.kcal_per_day, by_id[8].kcal_per_day), "carries the standing kcal")

# --- summary keeps pointing at the last real weigh-in ---
rows = [entry(i + 1, i, 200.0 - 0.25 * i, 25.0, None) for i in range(10)]
rows.append(entry(900, 12.0, None, None, 34.0))  # tape two days after the last weigh-in
summ = summary(compute_trend(rows))
check(close(summ["latest_weight"], 200.0 - 0.25 * 9), "latest_weight is the last weigh-in")
check(summ["latest_logged_at"].startswith("2026-01-10"), f"latest_logged_at {summ['latest_logged_at']}")
check(summ["count"] == 10, f"count is weigh-ins, got {summ['count']}")
check(summ["entry_count"] == 11, f"entry_count is all rows, got {summ['entry_count']}")
check(close(summ["latest_waist"], 34.0), "latest_waist is the tape measurement")
check(summ["latest_waist_at"].startswith("2026-01-13"), "latest_waist_at is the tape's own date")
check(summ["rate_lb_per_day"] is not None, "rate survives a trailing waist-only entry")
check(summ["trend"] is not None, "trend survives a trailing waist-only entry")
check(close(summ["span_days"], 9.0), f"span is weigh-in to weigh-in, got {summ['span_days']}")

# --- a waist-only entry before any weigh-in ---
first = compute_trend([entry(1, 0, None, None, 36.0), entry(2, 1, 200.0, 25.0, None)])
check(first[0].trend is None, "no weight yet → no trend to carry")
check(close(first[0].waist_trend, 36.0), "waist still smooths with no weight anywhere")
check(first[0].rate_provisional, "no weigh-ins yet → rate is provisional")
check(close(first[1].trend, 200.0), "the first real weigh-in still seeds the trend")
s2 = summary(first)
check(close(s2["latest_weight"], 200.0), "summary finds the only weigh-in")
check(s2["count"] == 1, "one weigh-in counted")
check(s2["entry_count"] == 2, "two entries counted")

# --- nothing but waist measurements ---
only = compute_trend([entry(1, 0, None, None, 36.0), entry(2, 7, None, None, 34.0)])
check(all(p.trend is None for p in only), "no weights → no weight trend anywhere")
check(close(only[1].waist_trend, 35.0), "waist EMA runs with no weights at all")
s3 = summary(only)
check(s3["latest_weight"] is None, "summary reports no weight")
check(s3["count"] == 0, "no weigh-ins counted")
check(s3["entry_count"] == 2, "both entries counted")
check(close(s3["waist_trend"], 35.0), "waist trend still reported")
check(close(s3["latest_waist"], 34.0), "latest waist still reported")

# --- waist-only entries never merge into a neighbouring weigh-in ---
near = compute_trend(
    [
        entry(1, 0, 200.0, 25.0, None),
        entry(2, 1, 199.0, 25.0, None),
        # 30 seconds after the weigh-in — inside the coalesce window
        (3, at(1 + 30.0 / 86400), None, None, None, 35.0),
    ]
)
check(len(near) == 3, "a waist-only entry beside a weigh-in stays its own row")
check(near[2].weight is None, "and keeps its own identity")
check(close(near[2].waist, 35.0), "and its measurement")
check(near[1].waist is None, "the weigh-in is not given the waist")

# --- reconnect spam still coalesces normally alongside all this ---
spam = compute_trend(
    [
        entry(1, 0, 200.0, 25.0, None),
        entry(2, 1, 199.0, 25.0, None),
        (3, at(1 + 30.0 / 86400), 199.0, None, 25.0, None),
        entry(4, 2, None, None, 35.0),
    ]
)
check(close(spam[2].alpha, 0.0), "duplicate weigh-in still marked coalesced")
check(spam[3].alpha is None, "waist-only row is not marked as a duplicate")

# --- the waist EMA clock ignores weigh-ins entirely ---
# Waist at day 0 and day 7 as standalone entries, daily weigh-ins in between:
# still exactly one half-life apart.
rows = [entry(i + 1, i, 200.0, 25.0, None) for i in range(10)]
rows += [entry(900, 0.1, None, None, 36.0), entry(901, 7.1, None, None, 34.0)]
pts = compute_trend(rows)
second = [p for p in pts if p.id == 901][0]
check(close(second.waist_trend, 35.0), f"7d apart → halfway, got {second.waist_trend}")

print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
