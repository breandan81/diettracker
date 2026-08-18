# Future improvements

## Renpho BLE auto-ingest (ES-CS20M / Elis 1)

**Status:** noted, not started  
**Goal:** When you step on the Renpho scale, weight lands in Hacker's Diet automatically — no web UI action.

### How it would work (auto, not click-to-sync)

1. Scale wakes on weigh-in and advertises / accepts a short BLE GATT session.
2. A **background listener** (always-on) catches the measurement.
3. Listener `POST`s to `/api/weights` (date + weight, optional note like `renpho-ble`).
4. EMA / BMI / coach update as usual; page just reflects new data on refresh.

You would **not** need to open the web page or press a button for logging. The page is only for charts/settings/pep talks.

### Prerequisites

- **BLE radio near the scale:** USB dongle on tomServo, *or* ESP32 / ESPHome Bluetooth proxy (tomServo currently has no `hci` adapter).
- Community stack options: [ble-scale-sync](https://github.com/KristianP26/ble-scale-sync) (Renpho Elis 1 / ES-CS20M → webhook / MQTT / JSONL), openScale lineage, HA `renpho_fitness_scale_ble`.
- Wire exporter → `http://tomservo.local:8510/api/weights` (or a small ingest wrapper for dedupe / multi-user).

### Design notes when building

- Deduplicate: same MAC + timestamp (±few seconds) shouldn’t double-log.
- Prefer **final stable weight** (scale often streams intermediate values).
- Optional: store impedance / body-fat later; v1 = weight only.
- Keep manual log form as fallback.
- No official Renpho BLE docs — rely on reverse-engineered adapters; model-specific.

### Out of scope for v1

- Renpho cloud / app OAuth
- Calorie logging (separate future item)
