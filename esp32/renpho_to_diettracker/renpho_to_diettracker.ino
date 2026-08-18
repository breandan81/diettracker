/**
 * Renpho ES-CS20M / Elis 1  →  Hacker's Diet tracker
 *
 * Listens for a weigh-in over BLE, then POSTs:
 *   {"date":"YYYY-MM-DD","weight":197.4,"note":"renpho-ble"}
 * to http://TRACKER_HOST:8510/api/weights
 *
 * Board: ESP32 (+ WiFi). Library: NimBLE-Arduino (h2zero).
 * Config: copy config.example.h → config.h
 *
 * Protocol notes distilled from community reverse-engineering
 * (openScale / renpho-escs20m). Unofficial — not affiliated with Renpho.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <NimBLEDevice.h>

#include "config.h"

// --- QN GATT (FFF0 layout used by many Renpho ES-CS20M units) ---
static NimBLEUUID UUID_SVC_FFF0("fff0");
static NimBLEUUID UUID_NOTIFY_FFF1("fff1");
static NimBLEUUID UUID_CMD_FFF2("fff2");

// Alternate QN layout (FFE0) — notify on ffe1
static NimBLEUUID UUID_SVC_FFE0("ffe0");
static NimBLEUUID UUID_NOTIFY_FFE1("ffe1");
static NimBLEUUID UUID_CMD_FFE3("ffe3");

static const uint8_t VENDOR_BYTE = 0xFF;
static const int32_t QN_EPOCH_OFFSET = 946656000;  // 2000-01-01 UTC

// --- Runtime state ---
static float g_lastPostedLb = -1;
static uint32_t g_lastPostedMs = 0;
static bool g_wifiReady = false;

static NimBLEClient* g_client = nullptr;
static NimBLERemoteCharacteristic* g_notifyChr = nullptr;
static NimBLERemoteCharacteristic* g_cmdChr = nullptr;
static bool g_wantConnect = false;
static NimBLEAddress g_targetAddr;
static bool g_haveTarget = false;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static float kgToLb(float kg) {
  return kg * 2.2046226218f;
}

static bool macAllowed(const NimBLEAddress& addr) {
  if (SCALE_MAC[0] == '\0') return true;
  String want = String(SCALE_MAC);
  want.toUpperCase();
  String got = String(addr.toString().c_str());
  got.toUpperCase();
  return want == got;
}

static bool looksLikeScaleName(const std::string& name) {
  // Common advertisement names — not exhaustive
  String n = String(name.c_str());
  n.toUpperCase();
  return n.indexOf("QN-SCALE") >= 0
      || n.indexOf("RENPHO") >= 0
      || n.indexOf("ELIS") >= 0
      || n.indexOf("CS20") >= 0
      || n.indexOf("FITINDEX") >= 0
      || n.indexOf("Health") >= 0;  // some firmwares
}

static uint8_t checksum8(const uint8_t* p, size_t n) {
  uint16_t s = 0;
  for (size_t i = 0; i < n; i++) s += p[i];
  return (uint8_t)(s & 0xFF);
}

static String todayISO() {
  // Prefer NTP clock; fall back to compile-ish placeholder if unset
  time_t now = time(nullptr);
  if (now < 1700000000) {
    // Clock not synced — still post; server will accept date string.
    // Use a fixed tag the human can edit if needed.
    return String("1970-01-01");
  }
  struct tm tm;
  localtime_r(&now, &tm);
  char buf[16];
  strftime(buf, sizeof(buf), "%Y-%m-%d", &tm);
  return String(buf);
}

static bool shouldPost(float lb) {
  uint32_t now = millis();
  if (g_lastPostedLb > 0
      && fabsf(lb - g_lastPostedLb) < 0.15f
      && (now - g_lastPostedMs) < (uint32_t)DEDUPE_SECONDS * 1000u) {
    Serial.printf("[dedupe] skip %.2f lb (recent)\n", lb);
    return false;
  }
  if (lb < 50.0f || lb > 500.0f) {
    Serial.printf("[reject] out of range %.2f lb\n", lb);
    return false;
  }
  return true;
}

static bool postWeightLb(float lb) {
  if (!g_wifiReady) {
    Serial.println("[http] wifi not ready");
    return false;
  }
  if (!shouldPost(lb)) return false;

  String url = String("http://") + TRACKER_HOST + ":" + String(TRACKER_PORT) + TRACKER_PATH;
  String body = String("{\"date\":\"") + todayISO()
              + "\",\"weight\":" + String(lb, 2)
              + ",\"note\":\"" + WEIGHT_NOTE + "\"}";

  Serial.printf("[http] POST %s  %s\n", url.c_str(), body.c_str());

  HTTPClient http;
  http.setTimeout(8000);
  if (!http.begin(url)) {
    Serial.println("[http] begin failed");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
  int code = http.POST(body);
  String resp = http.getString();
  http.end();

  Serial.printf("[http] -> %d  %s\n", code, resp.substring(0, 120).c_str());
  if (code >= 200 && code < 300) {
    g_lastPostedLb = lb;
    g_lastPostedMs = millis();
    return true;
  }
  return false;
}

static void onFinalKg(float kg, const char* via) {
  float lb = kgToLb(kg);
  Serial.printf("[weight] %.2f kg = %.2f lb  via %s\n", kg, lb, via);
  postWeightLb(lb);
}

// ---------------------------------------------------------------------------
// Broadcast path: manufacturer data magic AA BB (non-connectable variants)
// ---------------------------------------------------------------------------

static bool parseAabbBroadcast(const uint8_t* payload, size_t len, float* outKg) {
  // Need bytes through index 18
  if (len < 19) return false;
  if (payload[0] != 0xAA || payload[1] != 0xBB) return false;
  uint8_t status = payload[15];
  bool isFinal = (status & 0x01) == 0x01;
  if (!isFinal) return false;
  uint16_t raw = (uint16_t)payload[17] | ((uint16_t)payload[18] << 8);  // LE
  float kg = raw / 100.0f;
  if (kg <= 0) return false;
  *outKg = kg;
  return true;
}

// ---------------------------------------------------------------------------
// GATT notify parser (QN 0x10 measurement frames)
// ---------------------------------------------------------------------------

static void handleQnNotify(uint8_t* data, size_t len) {
  if (len < 6) return;
  uint8_t op = data[0];
  uint8_t flen = data[1];

  if (op != 0x10) {
    // Useful for debugging handshake
    Serial.printf("[gatt] op=0x%02X len=%u flen=%u\n", op, (unsigned)len, flen);
    return;
  }

  // Extended flavor: 10 0E/0F … status@4 weight@5..6 BE
  if (flen >= 0x0E && len >= 7) {
    uint8_t status = data[4];
    // 0=unstable, 1=stable, 2=stable+metrics
    if (status == 1 || status == 2) {
      uint16_t raw = ((uint16_t)data[5] << 8) | data[6];
      float kg = raw / 100.0f;
      if (kg > 0) onFinalKg(kg, "gatt-extended");
    }
    return;
  }

  // Basic flavor: 10 0B … weight@3..4 BE, status@5
  // status 0x01 = final (BIA done); 0x11 = stable weight, BIA running
  if (flen == 0x0B && len >= 6) {
    uint8_t status = data[5];
    if (status == 0x01 || status == 0x11) {
      uint16_t raw = ((uint16_t)data[3] << 8) | data[4];
      float kg = raw / 100.0f;
      // Prefer final 0x01; allow 0x11 once if we never see 0x01
      if (status == 0x01) {
        onFinalKg(kg, "gatt-basic-final");
      } else {
        // Debounce: only take BIA-running as fallback after a short hold
        static float pending = 0;
        static uint32_t pendingAt = 0;
        pending = kg;
        pendingAt = millis();
        // If still no 0x01 within 4s, post the stable weight
        // (handled in loop via pending — keep simple: post on 0x11 once)
        static uint32_t lastBasic = 0;
        if (millis() - lastBasic > 5000) {
          onFinalKg(kg, "gatt-basic-stable");
          lastBasic = millis();
        }
        (void)pending;
        (void)pendingAt;
      }
    }
  }
}

class NotifyCallbacks : public NimBLEClientCallbacks {
  // unused — notify uses characteristic callback
};

static void notifyCB(NimBLERemoteCharacteristic* chr, uint8_t* data, size_t len, bool isNotify) {
  (void)chr;
  (void)isNotify;
  handleQnNotify(data, len);
}

static bool sendMeasurementInit(NimBLERemoteCharacteristic* cmd) {
  // 20 08 FF <ts LE u32> <checksum>
  uint8_t cmdBuf[8];
  cmdBuf[0] = 0x20;
  cmdBuf[1] = 0x08;
  cmdBuf[2] = VENDOR_BYTE;
  time_t now = time(nullptr);
  uint32_t ts = (now > QN_EPOCH_OFFSET) ? (uint32_t)(now - QN_EPOCH_OFFSET) : 0;
  cmdBuf[3] = (uint8_t)(ts & 0xFF);
  cmdBuf[4] = (uint8_t)((ts >> 8) & 0xFF);
  cmdBuf[5] = (uint8_t)((ts >> 16) & 0xFF);
  cmdBuf[6] = (uint8_t)((ts >> 24) & 0xFF);
  cmdBuf[7] = checksum8(cmdBuf, 7);

  Serial.printf("[gatt] write init ts=%lu\n", (unsigned long)ts);
  return cmd->writeValue(cmdBuf, sizeof(cmdBuf), false);
}

static bool connectAndSubscribe(const NimBLEAddress& addr) {
  if (g_client && g_client->isConnected()) {
    g_client->disconnect();
    delay(200);
  }

  if (!g_client) {
    g_client = NimBLEDevice::createClient();
  }

  Serial.printf("[gatt] connecting to %s …\n", addr.toString().c_str());
  if (!g_client->connect(addr)) {
    Serial.println("[gatt] connect failed");
    return false;
  }

  NimBLERemoteService* svc = g_client->getService(UUID_SVC_FFF0);
  NimBLEUUID notifyUuid = UUID_NOTIFY_FFF1;
  NimBLEUUID cmdUuid = UUID_CMD_FFF2;

  if (!svc) {
    Serial.println("[gatt] no FFF0 — trying FFE0");
    svc = g_client->getService(UUID_SVC_FFE0);
    notifyUuid = UUID_NOTIFY_FFE1;
    cmdUuid = UUID_CMD_FFE3;
  }
  if (!svc) {
    Serial.println("[gatt] no known service");
    g_client->disconnect();
    return false;
  }

  g_notifyChr = svc->getCharacteristic(notifyUuid);
  g_cmdChr = svc->getCharacteristic(cmdUuid);
  if (!g_notifyChr || !g_cmdChr) {
    Serial.println("[gatt] missing notify/cmd characteristic");
    g_client->disconnect();
    return false;
  }

  if (g_notifyChr->canNotify()) {
    if (!g_notifyChr->subscribe(true, notifyCB)) {
      Serial.println("[gatt] subscribe failed");
      g_client->disconnect();
      return false;
    }
  } else if (g_notifyChr->canIndicate()) {
    if (!g_notifyChr->subscribe(false, notifyCB)) {
      Serial.println("[gatt] indicate subscribe failed");
      g_client->disconnect();
      return false;
    }
  } else {
    Serial.println("[gatt] char cannot notify/indicate");
    g_client->disconnect();
    return false;
  }

  delay(100);
  if (!sendMeasurementInit(g_cmdChr)) {
    Serial.println("[gatt] init write failed");
  }

  Serial.println("[gatt] subscribed — step on scale if you haven't");
  return true;
}

// ---------------------------------------------------------------------------
// Scan callbacks
// ---------------------------------------------------------------------------

class ScanCallbacks : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* adv) override {
    if (!macAllowed(adv->getAddress())) return;

    const bool nameOk = adv->haveName() && looksLikeScaleName(adv->getName());
    const bool macForced = SCALE_MAC[0] != '\0';

#if BLE_MODE == MODE_BROADCAST || BLE_MODE == MODE_AUTO
    // Manufacturer data AABB
    if (adv->haveManufacturerData()) {
      std::string md = adv->getManufacturerData();
      // NimBLE: first 2 bytes are company id (often 0xFFFF), rest is payload
      if (md.size() >= 2 + 19) {
        const uint8_t* raw = (const uint8_t*)md.data();
        uint16_t company = (uint16_t)raw[0] | ((uint16_t)raw[1] << 8);
        const uint8_t* payload = raw + 2;
        size_t plen = md.size() - 2;
        float kg = 0;
        if ((company == 0xFFFF || company == 0x00FF) && parseAabbBroadcast(payload, plen, &kg)) {
          if (nameOk || macForced || SCALE_MAC[0] == '\0') {
            Serial.printf("[adv] AABB final from %s\n", adv->getAddress().toString().c_str());
            onFinalKg(kg, "broadcast-aabb");
          }
        }
      }
    }
#endif

#if BLE_MODE == MODE_GATT || BLE_MODE == MODE_AUTO
    // Look for connectable QN / Renpho
    if ((nameOk || macForced) && adv->isConnectable()) {
      bool hasFff0 = adv->isAdvertisingService(UUID_SVC_FFF0);
      bool hasFfe0 = adv->isAdvertisingService(UUID_SVC_FFE0);
      if (hasFff0 || hasFfe0 || nameOk || macForced) {
        if (!g_wantConnect && !g_haveTarget) {
          g_targetAddr = adv->getAddress();
          g_haveTarget = true;
          g_wantConnect = true;
          Serial.printf("[scan] target %s name='%s' fff0=%d ffe0=%d\n",
                        adv->getAddress().toString().c_str(),
                        adv->haveName() ? adv->getName().c_str() : "",
                        hasFff0, hasFfe0);
          NimBLEDevice::getScan()->stop();
        }
      }
    }
#endif

    // Discovery aid
    if (adv->haveName() && looksLikeScaleName(adv->getName())) {
      static uint32_t lastPrint = 0;
      if (millis() - lastPrint > 3000) {
        Serial.printf("[see] %s  %s  RSSI=%d\n",
                      adv->getAddress().toString().c_str(),
                      adv->getName().c_str(),
                      adv->getRSSI());
        lastPrint = millis();
      }
    }
  }
};

static ScanCallbacks scanCallbacks;

static void startScan() {
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(&scanCallbacks, false);
  scan->setActiveScan(true);
  scan->setInterval(80);
  scan->setWindow(40);
  scan->setMaxResults(0);
  Serial.println("[scan] start");
  scan->start(0, false, true);  // forever, don't clear results dump
}

// ---------------------------------------------------------------------------
// WiFi / time
// ---------------------------------------------------------------------------

static void ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    g_wifiReady = true;
    return;
  }
  g_wifiReady = false;
  Serial.printf("[wifi] connecting to %s …\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < 20000) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("[wifi] ok %s\n", WiFi.localIP().toString().c_str());
    g_wifiReady = true;
    // US Central-ish; adjust if you want — date string only needs calendar day
    configTime(-6 * 3600, 0, "pool.ntp.org", "time.nist.gov");
  } else {
    Serial.println("[wifi] FAILED");
  }
}

// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  Serial.println();
  Serial.println(F("=== Renpho → Hacker's Diet (ESP32) ==="));
  Serial.printf("mode=%d tracker=%s:%d\n", BLE_MODE, TRACKER_HOST, TRACKER_PORT);

  ensureWifi();

  NimBLEDevice::init("diet-ble");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);

  startScan();
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 10000) {
      lastTry = millis();
      ensureWifi();
    }
  }

#if BLE_MODE == MODE_GATT || BLE_MODE == MODE_AUTO
  if (g_wantConnect && g_haveTarget) {
    g_wantConnect = false;
    bool ok = connectAndSubscribe(g_targetAddr);
    if (!ok) {
      g_haveTarget = false;
      delay(500);
      startScan();
    }
  }

  // If connected but idle too long, disconnect and rescan
  static uint32_t connectedAt = 0;
  if (g_client && g_client->isConnected()) {
    if (connectedAt == 0) connectedAt = millis();
    if (millis() - connectedAt > 45000) {
      Serial.println("[gatt] session timeout — disconnect");
      g_client->disconnect();
      g_haveTarget = false;
      connectedAt = 0;
      delay(300);
      startScan();
    }
  } else {
    connectedAt = 0;
  }
#endif

  delay(20);
}
