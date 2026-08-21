# ESP32 → τrend (Renpho ES-CS20M / Elis 1)

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
2. For **Linode / public HTTPS** (e.g. `tau.bposhaughnessy.com`):
   - `TRACKER_HOST` = your hostname  
   - `TRACKER_PORT` = `443`  
   - `TRACKER_TLS` = `1`  
   For LAN-only HTTP, use your LAN IP, port `8511`, and `TRACKER_TLS 0`.
3. For **multi-user** τrend: create an ingest token in the web UI (Settings → ESP32) and set
   `INGEST_TOKEN` in `config.h`. The sketch sends `Authorization: Bearer …` on
   `POST /api/weights` and `GET /api/scale-profile`.
4. Optionally set `SCALE_MAC` after a first scan (Serial prints advertisements).
5. Set `BLE_MODE` to `MODE_AUTO`, `MODE_GATT`, or `MODE_BROADCAST`.
6. Flash `renpho_to_diettracker.ino` (ESP32-C3 SuperMini example):

```bash
FQBN=esp32:esp32:esp32c3:CDCOnBoot=cdc,UploadSpeed=115200,PartitionScheme=huge_app
arduino-cli compile --fqbn "$FQBN" renpho_to_diettracker
arduino-cli upload -p /dev/ttyACM0 --fqbn "$FQBN" renpho_to_diettracker
```

7. Open Serial Monitor @ **115200** with **DTR/RTS off** (otherwise the C3 keeps resetting / WiFi comes up before BLE looks wrong):

```bash
arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200,dtr=off,rts=off
```

8. Step on the scale. Expect one POST per power-on. After success you should see
   `posted — sleeping …` then ~90s later `[ble] armed — step on scale for next log`.
   Only then will a second weigh-in connect.

**ESP32-C3 note:** WiFi and BLE share one radio. This sketch disconnects GATT before HTTPS
so Linode TLS posts don’t return `http -> -1`.

Or download a ready zip from the running app: **About → Download ESP32 sketch**
(`/api/esp/firmware.zip`).

## One-shot session state machine

Duplicate GATT finals used to double-POST. Logic now lives in pure C++
`renpho_to_diettracker/scale_session.h` (no Arduino deps):

- **Armed** → connect on advertisement
- **InSession** → claim at most one measurement *before* HTTP (spam finals ignored)
- **Cooldown** → no scan/connect for `RESCAN_COOLDOWN_SECONDS` (default 90)
- Post failure un-claims so the same session can retry; disconnect without a claim re-arms

Host unit tests (no ESP toolchain):

```bash
make -C renpho_to_diettracker test
```

## Config knobs (`config.h`)

| Define | Meaning |
|--------|---------|
| `WIFI_SSID` / `WIFI_PASS` | LAN credentials |
| `TRACKER_HOST` | e.g. `192.168.x.x` or `tomservo.local` (mDNS can be flaky — prefer IP) |
| `TRACKER_PORT` | `8510` |
| `WEIGHT_UNIT_POST` | `"lb"` (matches the tracker) |
| `SCALE_MAC` | `""` = first matching Renpho/QN device; or `"AA:BB:..."` |
| `BLE_MODE` | `MODE_AUTO` tries GATT then falls back to broadcast parse |
| `RESCAN_COOLDOWN_SECONDS` | Ignore scale after a successful log (default 90) |

## Protocol sketch (what the code does)

**Broadcast (0xAABB):** manufacturer data magic `AA BB`, final flag in status byte, weight = `u16 LE / 100` kg.

**GATT (QN / FFF0):** connect → notify `FFF1` → write init `0x20…` to `FFF2` → parse `0x10` measurement frames; stable/final statuses yield kg weight (`u16 BE / 100`).

Weight is converted to **lb** before POST (`kg * 2.2046226218`).

## Limitations

- Profile (height/sex/age/athlete) is fetched from the tracker `/api/scale-profile`
  so the scale can compute body fat; incomplete profile → weight-only logs.
- Intermediate unstable readings are ignored; stable waits briefly for a BF final,
  then times out and POSTs weight alone.
- If GATT handshake fails on your HVIN, try `MODE_BROADCAST` or capture a phone HCI log (see FUTURE.md).
- Keep the Renpho phone app disconnected while testing (exclusive BLE).
- Tracker also dedupes near-identical posts (~2 min); the ESP still enforces one claim per power-on.

## After it works

Wire systemd/docs as needed; consider adding an ingest token on the tracker API later.
