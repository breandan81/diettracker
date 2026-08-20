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
