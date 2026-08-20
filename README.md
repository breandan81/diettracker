# τrend — weight trend tracker

Local Tom Servo webapp for irregular weigh-ins smoothed with a **time-aware EMA**, plus body fat, BMI, AI pep talks, progress photos, and optional Renpho BLE auto-log via ESP32.

The EMA math is inspired by John Walker’s *[The Hacker's Diet](https://www.fourmilab.ch/hackdiet/)* — **τrend is not affiliated** with that book or its author.

Runs at **http://host:8510/** (SQLite under `data/weights.db`).

---

## Features

### Weight logging & trend
- Manual log form: datetime, weight (lb), optional body fat %, note
- Edit / delete past entries from the history table
- **Time-aware EMA** with configurable half-life (default 7 days ≈ classic daily α≈0.1)
- **Gap-tolerant**: irregular sampling is fine — no daily requirement, no fake interpolation
- Near-duplicate coalescing (~2 min / ±0.15 lb) so identical stamps don’t jerk the trend
- Summary tiles: trend weight, rate (lb/week & lb/day), estimated **kcal/day** from slope (`× 3500`)
- Goal weight with progress meter and ETA from current rate
- Range tabs on the chart: 30d / 90d / 180d / 1y / all

### Body fat & BMI
- Body fat % from manual entry or Renpho BLE auto-log
- Separate **BF% EMA** alongside weight EMA
- Dual-axis chart: weight (lb) left, body fat (%) right
- BF Y-axis uses a **fixed lean→obese band from sex/age** (ACE-style), not auto-zoom to today’s noise
- BMI from trend weight + height; category bar and healthy weight range for your height
- Settings: height, sex, age, Renpho athlete-mode flag (also fed to the ESP/scale profile)

### Coach UI (mood + LLM)
- Mood art and badges driven by trend (crushing / losing / steady / gaining / goal / idle)
- Fat-burn **throttle** gauge from the loss/gain rate
- Optional **KoboldCPP** pep talks: styles `pep` | `roast` | `haiku` | `brief`
- One-click generate, or auto-on-save; last talk cached in settings
- Streak / logging badges

### Progress photos (xAI Grok)
- Photos view: upload dated progress shots
- **Grok vision** rates appearance (1–10) and visual BMI
- Chart overlays appearance, visual BMI, and scale BMI at weigh-ins
- **Imagine** goal projections from a photo toward goal weight (visual preview only — not re-rated)
- Requires `XAI_API_KEY` / `GROK_API_KEY` in gitignored `secrets.env`

### Renpho BLE auto-log (ESP32)
- Sketch under [`esp32/`](./esp32/) for Renpho **ES-CS20M / Elis 1** (QN GATT / AABB broadcast)
- ESP32-C3 posts weight (+ body fat when the scale computes it) to `/api/weights`
- Tracker `/api/scale-profile` supplies height / sex / age / athlete for on-device BF
- **One POST per scale power-on**: claim-before-HTTP session machine + ~90s cooldown, then re-arm
- Host unit tests for the session logic: `make -C esp32/renpho_to_diettracker test`
- Serial monitor tip: **115200, DTR/RTS off** on USB-Serial/JTAG C3 boards

### Ops
- Pure Python stdlib HTTP server (`server.py`) + static `public/`
- User systemd unit (`hackers-diet.service`) for always-on LAN use
- CORS-friendly JSON API for the ESP and the browser UI

---

## Run

```bash
cd ~/AIML/claude/hackers-diet
python3 server.py          # http://0.0.0.0:8510/
```

User systemd unit:

```bash
systemctl --user enable --now hackers-diet.service
systemctl --user status hackers-diet.service
```

SQLite DB: `data/weights.db` · photos: `data/photos/` (both under gitignored `data/`).

### Tests

```bash
node test/test_bf_axis.js                          # BF chart axis bands
make -C esp32/renpho_to_diettracker test           # ScaleSession one-shot / cooldown
```

---

## Gap math

For a gap of `Δt` days and half-life `H`:

```
decay = 0.5 ** (Δt / H)
trend = (1 - decay) * weight + decay * trend_prev
```

Classic daily α≈0.1 ≈ half-life of ~6.6 days (default **7**). Body-fat EMA uses the same half-life when BF samples are present.

---

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/health` | liveness |
| GET | `/api/trend` | series + summary (+ BMI when height set) |
| GET | `/api/weights` | same series |
| GET | `/api/summary` | summary only |
| POST | `/api/weights` | `{weight, body_fat?, note?, logged_at?}` — server stamps time if omitted |
| PUT | `/api/weights/:id` | update |
| DELETE | `/api/weights/:id` | delete |
| GET/PUT | `/api/settings` | half-life, goal, height, sex, age, athlete, … |
| GET | `/api/scale-profile` | Renpho/QN profile blob for ESP32 |
| GET | `/api/coach/status` | KoboldCPP reachability + model |
| GET | `/api/coach` | last cached pep talk |
| POST | `/api/coach` | `{style?: pep\|roast\|haiku\|brief}` → generate via Kobold |
| GET | `/api/vision/status` | xAI key configured? |
| GET | `/api/photos` | progress photos + ratings |
| POST | `/api/photos` | multipart `file` + `date` (+ optional `note`, `analyze`) |
| GET | `/api/photos/:id` | one photo |
| GET | `/api/photos/:id/image` | image bytes |
| GET | `/api/photos/:id/projection` | Imagine goal image bytes |
| POST | `/api/photos/:id/analyze` | re-run Grok vision |
| POST | `/api/photos/:id/project-goal` | generate Imagine projection |
| DELETE | `/api/photos/:id` | delete photo + file |
| GET | `/api/photos/series` | chart series (appearance + visual BMI) |

Env: `KOBOLD_URL`, `KOBOLD_TIMEOUT`, `XAI_API_KEY` / `GROK_API_KEY`, `XAI_MODEL` (default `grok-4.6`).  
Secrets: gitignored `secrets.env`. ESP WiFi/tracker config: `esp32/**/config.h` (from `config.example.h`).

---

## ESP32 quick start

See [`esp32/README.md`](./esp32/README.md). Short version:

```bash
cp esp32/renpho_to_diettracker/config.example.h esp32/renpho_to_diettracker/config.h
# edit WiFi + TRACKER_HOST
FQBN=esp32:esp32:esp32c3:CDCOnBoot=cdc,UploadSpeed=115200,PartitionScheme=huge_app
arduino-cli compile --fqbn "$FQBN" esp32/renpho_to_diettracker
arduino-cli upload -p /dev/ttyACM0 --fqbn "$FQBN" esp32/renpho_to_diettracker
arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200,dtr=off,rts=off
```

---

## Future improvements

See [`FUTURE.md`](./FUTURE.md).
