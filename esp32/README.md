# ESP32 → Hacker's Diet (Renpho ES-CS20M / Elis 1)

Sketch that sits near the scale, hears a weigh-in over BLE, and `POST`s weight to:

```http
POST http://tomservo.local:8510/api/weights
{"date":"YYYY-MM-DD","weight":197.4,"note":"renpho-ble"}
```

**Auto-log** — no web UI click. Step on the scale; the tracker updates.

## Hardware

- ESP32 (classic or ESP32-C3/S3 with BLE + WiFi)
- Power near the bathroom / scale (USB wall wart is fine)
- Scale: Renpho **ES-CS20M / Elis 1** (or QN-compatible cousin)

Check the **HVIN / FCC ID** on the back sticker. Some ES-CS20M revisions speak different protocols:

| HVIN / FCC tip | Likely mode in sketch |
|----------------|------------------------|
| `ESCS20MA2`, `ESCS20MN`, `2A26P-ESCS20M` | GATT (`MODE_GATT`) |
| `2APXUES-CS20M` (broadcast-only) | Advertisements (`MODE_BROADCAST`) |
| `ESCS20MB2` | Often **unsupported** — try both modes / report |

See community notes in [`../FUTURE.md`](../FUTURE.md) and [renpho-escs20m](https://github.com/ronnnnnnnnnnnnn/renpho-escs20m).

## Arduino IDE / PlatformIO

**Board:** ESP32 Dev Module (or your exact board)  
**Libraries (Library Manager):**

- [NimBLE-Arduino](https://github.com/h2zero/NimBLE-Arduino) (h2zero)

WiFi + HTTPClient ship with the ESP32 Arduino core.

1. Copy `config.example.h` → `config.h` and edit WiFi + tracker URL.
2. Optionally set `SCALE_MAC` after a first scan (Serial prints advertisements).
3. Set `BLE_MODE` to `MODE_AUTO`, `MODE_GATT`, or `MODE_BROADCAST`.
4. Flash `renpho_to_diettracker.ino`.
5. Open Serial Monitor @ **115200** with **DTR/RTS off** (otherwise the C3 keeps resetting):

```bash
arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200,dtr=off,rts=off
```

6. Step on the scale. After a successful log, wait ~90s (cooldown) before the next weigh-in.

## Config knobs (`config.h`)

| Define | Meaning |
|--------|---------|
| `WIFI_SSID` / `WIFI_PASS` | LAN credentials |
| `TRACKER_HOST` | e.g. `192.168.x.x` or `tomservo.local` (mDNS can be flaky — prefer IP) |
| `TRACKER_PORT` | `8510` |
| `WEIGHT_UNIT_POST` | `"lb"` (matches the tracker) |
| `SCALE_MAC` | `""` = first matching Renpho/QN device; or `"AA:BB:..."` |
| `BLE_MODE` | `MODE_AUTO` tries GATT then falls back to broadcast parse |
| `DEDUPE_SECONDS` | Ignore repeat posts within N seconds |

## Protocol sketch (what the code does)

**Broadcast (0xAABB):** manufacturer data magic `AA BB`, final flag in status byte, weight = `u16 LE / 100` kg.

**GATT (QN / FFF0):** connect → notify `FFF1` → write init `0x20…` to `FFF2` → parse `0x10` measurement frames; stable/final statuses yield kg weight (`u16 BE / 100`).

Weight is converted to **lb** before POST (`kg * 2.2046226218`).

## Limitations (v0 sketch)

- Not a full port of `renpho-escs20m` (no full body-comp / multi-user profile dance).
- Intermediate scale readings are ignored; only **final/stable** weights POST.
- If GATT handshake fails on your HVIN, try `MODE_BROADCAST` or capture a phone HCI log (see FUTURE.md).
- Keep the Renpho phone app disconnected while testing (exclusive BLE).

## After it works

Wire systemd/docs as needed; consider adding an ingest token on the tracker API later.
