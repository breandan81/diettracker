# Future improvements

## Renpho BLE auto-ingest (ES-CS20M / Elis 1)

**Status:** ESP32 sketch sketched — see [`esp32/`](./esp32/)  
**Goal:** When you step on the Renpho scale, weight lands in Hacker's Diet automatically — no web UI action.

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

### Still TODO when bringing hardware up

- Confirm which mode works on your HVIN (`MODE_GATT` vs `MODE_BROADCAST`)
- Lock `SCALE_MAC` after first discovery
- Optional: API ingest token; store impedance later
- Keep manual log form as fallback

### Out of scope for v1

- Renpho cloud / app OAuth
- Full on-device body-comp / multi-user profile dance (use community libs if needed)
- Calorie logging (separate future item)
