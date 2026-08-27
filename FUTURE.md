# Future improvements

## Public multi-user / photo safety

Before any open internet deploy with photo upload → Grok: see **Safety note** and **Staged deployment plan** in [`MULTIUSER.md`](./MULTIUSER.md). Short version: closed beta → public signup with invite-only private photos → moderation before Grok vision → open uploads only if monetized. Do not ship an unmoderated upload pipe on your xAI key. Stopping at “public accounts, photos still invite-only” is a fine long-term shape.

## Renpho BLE auto-ingest (ES-CS20M / Elis 1)

**Status:** Working on ESP32-C3 — one POST per scale power-on (claim-before-HTTP + cooldown), body fat via `/api/scale-profile`. See [`esp32/`](./esp32/).  
**Goal:** When you step on the Renpho scale, weight lands in τrend automatically — no web UI action.

### How it works (auto, not click-to-sync)

1. Scale wakes on weigh-in and advertises / accepts a short BLE GATT session.
2. ESP32 near the scale catches the measurement (broadcast `0xAABB` and/or QN GATT `FFF0`/`FFE0`).
3. Firmware `POST`s to `/api/weights` (`note: renpho-ble`).
4. EMA / BMI / coach update as usual; page just reflects new data on refresh.

### Sketch

- [`esp32/README.md`](./esp32/README.md) — flash + config
- [`esp32/renpho_to_diettracker/`](./esp32/renpho_to_diettracker/) — Arduino / NimBLE sketch

### Prerequisites

- ESP32 with WiFi + BLE, powered near the scale
- Check HVIN/FCC on the scale sticker (some ES-CS20M revisions differ)
- Disconnect Renpho phone app while testing
- Prefer tracker **LAN IP** over mDNS in `config.h`

### Still nice-to-have

- Lock `SCALE_MAC` after first discovery (multi-scale households)
- ~~Optional API ingest token~~ (multi-user: Settings → ingest tokens + Bearer on ESP)
- Store impedance / water % later
- Keep manual log form as fallback (already present)

### Out of scope for now

- Renpho cloud / app OAuth
- Full multi-user on-scale profile dance
- Calorie / food logging (separate future item)

## Kalman filter for trend + rate

**Status:** Considered and deferred 2026-08-27. Current implementation works and
is tested; this is a cleaner end state, not a fix.

Replace the EMA trend *and* the OLS rate window with one constant-velocity
Kalman filter — state `[weight, velocity]`, covariance carried forward. Trend,
rate, and the uncertainty on both fall out of a single recursion.

### Why it is tempting

Every gap heuristic in `trend.py` exists because the two estimators are bolted
together. A KF gets them for free: `dt` goes into `F` and `Q`, so variance
inflates across a gap on its own.

    now:  half_life_days, RATE_WINDOW_DAYS, RATE_MIN_POINTS,
          RATE_MAX_WINDOW_DAYS, _MIN_RATE_SPAN_DAYS      (5 constants)
    KF:   q, R                                            (2, and R is
                                                           estimable from data)

Also more accurate at both ends — RMS error against a known rate, sigma 1.01,
2000 runs:

| day | KF | OLS-21 |
|-----|--------|--------|
| 3   | 0.373  | 0.711  |
| 8   | 0.152  | 0.153  |
| 21  | 0.038  | 0.037  |
| 45  | 0.025  | 0.034  |

Early it wins because the prior regularises a 3-point fit; late because OLS
discards everything past 21 days.

### What to design deliberately

1. **Gap extrapolation — the real risk.** The KF carries velocity across a gap.
   Simulated: lose 0.4/day for 20d, two weeks off *gaining* 0.3/day, and on the
   first weigh-in back the KF's weight estimate was **4.25 lb low** (192.25 vs
   196.50 actual) because it trusted the old velocity. An EMA cannot do this —
   it holds and jumps. Choose `q` so variance genuinely inflates over a gap.
2. **Step changes are slower.** A rectangular window forgets completely at
   exactly 21 days; the KF forgets exponentially with a tail. On a rate change
   OLS landed at day 21, the KF took ~30.
3. **`q` is not a user-facing knob.** Settings exposes "EMA half-life (days)".
   Reparameterise as something like "days for the rate to shift appreciably".
4. **The chart changes.** A velocity-aware trend leads the EMA — better, but no
   longer the Hacker's Diet curve the app is named for.

A KF does not beat the noise/lag tradeoff, it navigates it optimally. The virtue
is sitting on the efficient edge *and reporting where it is* — which is exactly
what the trend-difference estimator failed to do when it read 31% of true while
claiming to be settled.
