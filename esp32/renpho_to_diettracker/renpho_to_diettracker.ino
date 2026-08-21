/**
 * Renpho ES-CS20M / Elis 1  →  Hacker's Diet tracker
 *
 * Listens for a weigh-in over BLE, then POSTs:
 *   {"weight":197.4,"body_fat":25.8,"note":"renpho-ble"}
 * to TRACKER_HOST (http or https) /api/weights with optional Bearer ingest token.
 * (server stamps logged_at; date is derived there).
 *
 * One POST per scale power-on: ScaleSession claims before HTTP so spam
 * finals cannot double-log; then cooldown → re-arm. Logic in
 * scale_session.h; host tests: `make test` in this directory.
 *
 * Board: ESP32-C3 (+ WiFi). Library: NimBLE-Arduino (h2zero).
 * Config: copy config.example.h → config.h
 * Monitor: baud 115200, dtr=off, rts=off (C3 USB-Serial/JTAG).
 *
 * Protocol notes distilled from community reverse-engineering
 * (openScale / renpho-escs20m). Unofficial — not affiliated with Renpho.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <time.h>
#include <string.h>
#include <NimBLEDevice.h>

#include "config.h"
#include "scale_session.h"

#ifndef RESCAN_COOLDOWN_SECONDS
#define RESCAN_COOLDOWN_SECONDS 90
#endif

#ifndef TRACKER_TLS
#define TRACKER_TLS 0
#endif

static String trackerUrl(const char* path) {
  String u;
#if TRACKER_TLS
  u = String("https://") + TRACKER_HOST;
  if (TRACKER_PORT != 443) {
    u += ":";
    u += String(TRACKER_PORT);
  }
#else
  u = String("http://") + TRACKER_HOST + ":" + String(TRACKER_PORT);
#endif
  u += path;
  return u;
}

// Begin HTTP(S). Caller must http.end(). Returns false if begin failed.
static bool httpBeginTracker(HTTPClient& http, WiFiClientSecure& tls, const String& url) {
#if TRACKER_TLS
  // Hobby deploy: skip CA verify (Caddy/Let's Encrypt still encrypts in transit).
  tls.setInsecure();
  return http.begin(tls, url);
#else
  (void)tls;
  return http.begin(url);
#endif
}

// --- QN GATT (FFF0 layout used by many Renpho ES-CS20M units) ---
static NimBLEUUID UUID_SVC_FFF0("fff0");
static NimBLEUUID UUID_NOTIFY_FFF1("fff1");
static NimBLEUUID UUID_CMD_FFF2("fff2");

// Alternate QN layout (FFE0) — notify on ffe1
static NimBLEUUID UUID_SVC_FFE0("ffe0");
static NimBLEUUID UUID_NOTIFY_FFE1("ffe1");
static NimBLEUUID UUID_CMD_FFE3("ffe3");

static uint8_t g_vendorByte = 0xFF;  // echoed from scale frames
static const int32_t QN_EPOCH_OFFSET = 946656000;  // 2000-01-01 UTC
static const uint8_t UNIT_LB = 0x02;

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
static bool g_bleReady = false;
static bool g_endSession = false;  // disconnect in loop() after successful post

// One-shot / cooldown state machine (unit-tested in test/test_scale_session.cpp)
static ScaleSession g_session((uint32_t)RESCAN_COOLDOWN_SECONDS * 1000u);

// Handshake flags (once per GATT session)
static bool g_sentUnit = false;
static bool g_sentInit = false;
static bool g_sentProfile = false;
// Defer HTTPS profile refresh until GATT is idle (C3 WiFi+BLE share one radio)
static bool g_profileRefreshPending = false;
// Defer weight POST to loop() so we never TLS while GATT notify is on the stack
static bool g_pendingPost = false;
static float g_pendingPostLb = 0;
static float g_pendingPostBf = -1;
static char g_pendingPostVia[40] = "";

// Cached profile from diet tracker /api/scale-profile
static bool g_profileReady = false;
static int g_profSex = 0;       // 0=male 1=female (QN)
static int g_profAge = 40;
static float g_profHeightM = 1.75f;
static bool g_profAthlete = false;
static int g_profAlgorithm = 4;
static uint32_t g_profileFetchedMs = 0;

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

static void stopScanQuiet() {
  if (!g_bleReady) return;
  NimBLEScan* scan = NimBLEDevice::getScan();
  if (scan && scan->isScanning()) {
    scan->stop();
  }
}

/** Drop GATT so WiFi/TLS can use the C3 radio. Safe to call more than once. */
static void releaseGattForWifi(const char* why) {
  stopScanQuiet();
  if (g_client && g_client->isConnected()) {
    Serial.printf("[ble] disconnect before WiFi (%s)\n", why);
    g_client->disconnect();
    delay(150);
  }
  g_notifyChr = nullptr;
  g_cmdChr = nullptr;
  g_haveTarget = false;
  g_wantConnect = false;
  g_endSession = false;
}

static bool postMeasurement(float lb, float bodyFatPct /* <0 if unknown */) {
  if (!g_wifiReady) {
    Serial.println("[http] wifi not ready");
    return false;
  }
  if (lb < 50.0f || lb > 500.0f) {
    Serial.printf("[reject] out of range %.2f lb\n", lb);
    return false;
  }

  // Must not hold a BLE link during HTTPS — otherwise http.POST returns -1 on C3
  releaseGattForWifi("POST /api/weights");
  delay(TRACKER_TLS ? 200 : 50);

  // Do NOT send logged_at — the diet tracker stamps with its own system clock.
  String url = trackerUrl(TRACKER_PATH);
  String body = String("{\"weight\":") + String(lb, 2);
  if (bodyFatPct >= 0.0f && bodyFatPct <= 80.0f) {
    body += ",\"body_fat\":" + String(bodyFatPct, 1);
  }
  body += ",\"note\":\"" + String(WEIGHT_NOTE) + "\"}";

  Serial.printf("[http] POST %s  %s\n", url.c_str(), body.c_str());

  HTTPClient http;
  WiFiClientSecure tls;
  http.setTimeout(20000);
  if (!httpBeginTracker(http, tls, url)) {
    Serial.println("[http] begin failed");
    return false;
  }
  http.addHeader("Content-Type", "application/json");
#ifdef INGEST_TOKEN
  if (INGEST_TOKEN[0] != '\0') {
    http.addHeader("Authorization", String("Bearer ") + INGEST_TOKEN);
  }
#endif
  int code = http.POST(body);
  String resp = http.getString();
  if (code < 0) {
    Serial.printf("[http] -> %d (%s)\n", code, http.errorToString(code).c_str());
  } else {
    Serial.printf("[http] -> %d  %s\n", code, resp.substring(0, 160).c_str());
  }
  http.end();

  // After WiFi/TLS, BLE needs a quiet period before scanning again
  delay(TRACKER_TLS ? 400 : 50);
  if (code >= 200 && code < 300) {
    g_lastPostedLb = lb;
    g_lastPostedMs = millis();
    return true;
  }
  return false;
}

/**
 * Called only after ScaleSession has already claimed the measurement.
 * Queues HTTPS for loop() so we never TLS on the BLE notify stack, and
 * always drops GATT first (C3 WiFi+BLE coexistence).
 */
static void postClaimedMeasurement(const ScaleMeasurement& m, const char* via) {
  float lb = kgToLb(m.weight_kg);
  if (m.body_fat >= 0) {
    Serial.printf("[weight] %.2f kg = %.2f lb  BF=%.1f%%  via %s\n",
                  m.weight_kg, lb, m.body_fat, via);
  } else {
    Serial.printf("[weight] %.2f kg = %.2f lb  via %s\n", m.weight_kg, lb, via);
  }

  if (g_pendingPost) {
    Serial.println("[http] post already queued — ignoring duplicate final");
    return;
  }
  g_pendingPostLb = lb;
  g_pendingPostBf = m.body_fat;
  strncpy(g_pendingPostVia, via ? via : "?", sizeof(g_pendingPostVia) - 1);
  g_pendingPostVia[sizeof(g_pendingPostVia) - 1] = '\0';
  g_pendingPost = true;
  // Free the radio immediately; HTTP runs in loop()
  releaseGattForWifi("queue post");
  // Claimed + disconnect → cooldown until onPostSuccess/Failure settles it
  g_session.onDisconnected(millis());
}

static void servicePendingPost() {
  if (!g_pendingPost) return;
  g_pendingPost = false;
  float lb = g_pendingPostLb;
  float bf = g_pendingPostBf;
  Serial.printf("[http] sending queued post (via %s)\n", g_pendingPostVia);
  uint32_t now = millis();
  if (postMeasurement(lb, bf)) {
    g_session.onPostSuccess(now);
    Serial.printf("[gatt] posted — sleeping %ds (one log per scale session)\n",
                  RESCAN_COOLDOWN_SECONDS);
  } else {
    g_session.onPostFailure(now);
    Serial.println("[gatt] post failed — re-armed for next scale power-on");
    if (g_session.isArmed()) startScan();
  }
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
// GATT: QN handshake + measurement frames
// ---------------------------------------------------------------------------

static bool cmdWrite(const uint8_t* buf, size_t n) {
  if (!g_cmdChr) return false;
  return g_cmdChr->writeValue(buf, n, false);
}

static bool sendUnitLb() {
  // 13 09 <vendor> <unit> 10 00 00 00 <checksum>   unit 0x02 = lb
  uint8_t cmd[9] = {0x13, 0x09, g_vendorByte, UNIT_LB, 0x10, 0x00, 0x00, 0x00, 0x00};
  cmd[8] = checksum8(cmd, 8);
  Serial.printf("[gatt] reply unit=lb vendor=0x%02X\n", g_vendorByte);
  return cmdWrite(cmd, sizeof(cmd));
}

static bool sendMeasurementInit() {
  // 20 08 <vendor> <ts LE u32> <checksum>
  uint8_t cmd[8];
  cmd[0] = 0x20;
  cmd[1] = 0x08;
  cmd[2] = g_vendorByte;
  time_t now = time(nullptr);
  uint32_t ts = (now > QN_EPOCH_OFFSET) ? (uint32_t)(now - QN_EPOCH_OFFSET) : 0;
  cmd[3] = (uint8_t)(ts & 0xFF);
  cmd[4] = (uint8_t)((ts >> 8) & 0xFF);
  cmd[5] = (uint8_t)((ts >> 16) & 0xFF);
  cmd[6] = (uint8_t)((ts >> 24) & 0xFF);
  cmd[7] = checksum8(cmd, 7);
  Serial.printf("[gatt] reply meas-init ts=%lu\n", (unsigned long)ts);
  return cmdWrite(cmd, sizeof(cmd));
}

static bool jsonExtractInt(const String& body, const char* key, int* out) {
  String pat = String("\"") + key + "\":";
  int i = body.indexOf(pat);
  if (i < 0) return false;
  i += pat.length();
  while (i < (int)body.length() && (body[i] == ' ')) i++;
  *out = body.substring(i).toInt();
  return true;
}

static bool jsonExtractFloat(const String& body, const char* key, float* out) {
  String pat = String("\"") + key + "\":";
  int i = body.indexOf(pat);
  if (i < 0) return false;
  i += pat.length();
  while (i < (int)body.length() && (body[i] == ' ')) i++;
  *out = body.substring(i).toFloat();
  return true;
}

static bool jsonExtractBool(const String& body, const char* key, bool* out) {
  String pat = String("\"") + key + "\":";
  int i = body.indexOf(pat);
  if (i < 0) return false;
  i += pat.length();
  while (i < (int)body.length() && (body[i] == ' ')) i++;
  if (body.startsWith("true", i)) {
    *out = true;
    return true;
  }
  if (body.startsWith("false", i)) {
    *out = false;
    return true;
  }
  *out = body.substring(i).toInt() != 0;
  return true;
}

static bool fetchScaleProfile() {
  if (!g_wifiReady) return false;
  String url = trackerUrl("/api/scale-profile");
  HTTPClient http;
  WiFiClientSecure tls;
  http.setTimeout(12000);
  if (!httpBeginTracker(http, tls, url)) return false;
#ifdef INGEST_TOKEN
  if (INGEST_TOKEN[0] != '\0') {
    http.addHeader("Authorization", String("Bearer ") + INGEST_TOKEN);
  }
#endif
  int code = http.GET();
  String body = http.getString();
  http.end();
  if (code != 200) {
    Serial.printf("[profile] GET failed %d\n", code);
    return false;
  }

  bool ready = false;
  jsonExtractBool(body, "ready", &ready);
  int sex = 0, age = 40, algo = 4;
  float hm = 1.75f;
  bool athlete = false;
  jsonExtractInt(body, "sex_code", &sex);
  jsonExtractInt(body, "age", &age);
  jsonExtractFloat(body, "height_m", &hm);
  jsonExtractBool(body, "athlete", &athlete);
  jsonExtractInt(body, "algorithm", &algo);

  g_profileReady = ready && hm > 0.5f && age >= 5;
  if (g_profileReady) {
    g_profSex = sex;
    g_profAge = age;
    g_profHeightM = hm;
    g_profAthlete = athlete;
    g_profAlgorithm = algo > 0 ? algo : 4;
    g_profileFetchedMs = millis();
    Serial.printf("[profile] ok sex=%d age=%d height_m=%.3f athlete=%d algo=%d\n",
                  g_profSex, g_profAge, g_profHeightM, (int)g_profAthlete, g_profAlgorithm);
  } else {
    Serial.println("[profile] incomplete — set height/sex/age on tracker for body fat");
  }
  return g_profileReady;
}

static bool sendUserProfile() {
  // Guest-slot profile frame (renpho-escs20m build_user_profile_command)
  // Sex: Male=0 Female=1. Height as mm uint16 BE. Flag = algorithm (+0x0A if athlete).
  if (!g_profileReady) {
    // weight-only bootstrap so measurement still starts
    uint8_t payload[13] = {
        0xA0, 0x0D, 0x02, 0xFE, 0xFF, 0xEE,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00};
    payload[12] = checksum8(payload, 12);
    Serial.println("[gatt] reply bootstrap profile (weight-only — profile not ready)");
    return cmdWrite(payload, 13);
  }

  uint16_t height_mm = (uint16_t)lroundf(g_profHeightM * 1000.0f);
  uint8_t flag = (uint8_t)((g_profAlgorithm + (g_profAthlete ? 0x0A : 0)) & 0xFF);
  uint8_t payload[13];
  payload[0] = 0xA0;
  payload[1] = 0x0D;
  payload[2] = 0x02;
  payload[3] = 0xFE;
  payload[4] = 0xFF;
  payload[5] = 0xEE;
  payload[6] = (uint8_t)(g_profSex & 0xFF);
  payload[7] = (uint8_t)(g_profAge & 0xFF);
  payload[8] = (uint8_t)((height_mm >> 8) & 0xFF);
  payload[9] = (uint8_t)(height_mm & 0xFF);
  payload[10] = flag;
  payload[11] = 0x02;
  payload[12] = checksum8(payload, 12);
  Serial.printf("[gatt] reply profile sex=%d age=%d height_mm=%u flag=0x%02X\n",
                g_profSex, g_profAge, (unsigned)height_mm, flag);
  return cmdWrite(payload, 13);
}

static void handleQnNotify(uint8_t* data, size_t len) {
  if (len < 2) return;
  uint8_t op = data[0];
  uint8_t flen = data[1];

  // Capture vendor byte when present (not on profile frames)
  if (len >= 3 && (op == 0x10 || op == 0x12 || op == 0x14 || op == 0x21 || op == 0x23)) {
    g_vendorByte = data[2];
  }

  // --- handshake ---
  if (op == 0x12) {  // unit request
    Serial.printf("[gatt] op=0x12 unit-request flen=%u\n", flen);
    if (!g_sentUnit) {
      g_sentUnit = true;
      sendUnitLb();
    }
    return;
  }
  if (op == 0x14) {  // measurement init request
    Serial.printf("[gatt] op=0x14 meas-init-request flen=%u\n", flen);
    if (!g_sentInit) {
      g_sentInit = true;
      sendMeasurementInit();
    }
    return;
  }
  if (op == 0x21) {  // pre-measurement
    Serial.printf("[gatt] op=0x21 pre-meas flen=%u\n", flen);
    // extended wants profile when length==0x05
    if (flen == 0x05 && !g_sentProfile) {
      g_sentProfile = true;
      // NEVER call HTTPS here — WiFi TLS while GATT is up drops notifies on C3.
      if (!g_profileReady || (millis() - g_profileFetchedMs) > 60000UL) {
        g_profileRefreshPending = true;
        Serial.println("[gatt] profile stale — will HTTPS-refresh after this session");
      }
      sendUserProfile();
    }
    return;
  }

  if (op != 0x10) {
    Serial.printf("[gatt] op=0x%02X len=%u flen=%u\n", op, (unsigned)len, flen);
    return;
  }

  // Always log weight-ish frames so second-session silence is diagnosable
  Serial.printf("[gatt] 0x10 flen=%u len=%u", flen, (unsigned)len);
  for (size_t i = 0; i < len && i < 16; i++) Serial.printf(" %02X", data[i]);
  Serial.println();

  if (g_session.hasClaimed() || g_session.phase() != ScalePhase::InSession) return;

  uint32_t now = millis();
  ScaleMeasurement m{};

  // Extended flavor: 10 0E/0F … status@4 weight@5..6 BE; BF@11..12 when status==2
  if ((flen == 0x0E || flen == 0x0F) && len >= 7) {
    uint8_t status = data[4];
    uint16_t raw = ((uint16_t)data[5] << 8) | data[6];
    float kg = raw / 100.0f;
    float bf = -1.0f;
    if (status == 2 && len >= 13) {
      uint16_t bfRaw = ((uint16_t)data[11] << 8) | data[12];
      if (bfRaw > 0) bf = bfRaw / 10.0f;
    }
    if (status == 1 && kg > 0) {
      Serial.printf("[gatt] stable %.2f kg — waiting for BF frame\n", kg);
    } else if (status == 0) {
      Serial.printf("[gatt] extended unstable status=%u kg=%.2f\n", status, kg);
    }
    if (g_session.onExtendedFrame(now, status, kg, bf, &m)) {
      postClaimedMeasurement(m, "gatt-extended-final");
    }
    return;
  }

  // Basic flavor: 10 0B … weight@3..4 BE, status@5
  if (flen == 0x0B && len >= 6) {
    uint8_t status = data[5];
    uint16_t raw = ((uint16_t)data[3] << 8) | data[4];
    float kg = raw / 100.0f;
    if (status == 0x11 && kg > 0) {
      Serial.printf("[gatt] basic stable %.2f kg — waiting for final\n", kg);
    } else if (status != 0x01 && status != 0x11) {
      Serial.printf("[gatt] basic settling status=0x%02X kg=%.2f\n", status, kg);
    }
    if (g_session.onBasicFrame(now, status, kg, &m)) {
      postClaimedMeasurement(m, status == 0x01 ? "gatt-basic-final" : "gatt-basic");
    }
    return;
  }

  Serial.printf("[gatt] 0x10 unparsed flen=%u — not basic/extended layout\n", flen);
}

static void notifyCB(NimBLERemoteCharacteristic* chr, uint8_t* data, size_t len, bool isNotify) {
  (void)chr;
  (void)isNotify;
  handleQnNotify(data, len);
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

  g_sentUnit = false;
  g_sentInit = false;
  g_sentProfile = false;
  g_endSession = false;
  g_vendorByte = 0xFF;
  g_session.onConnected(millis());

  delay(100);
  // Proactive init helps some firmwares; 0x12/0x14 handlers cover the rest
  sendMeasurementInit();
  // FFE0 / many QN units won't stream 0x10 weight frames until they have a profile.
  // Send cached profile only — never HTTPS while connected.
  delay(80);
  if (g_profileReady && !g_sentProfile) {
    g_sentProfile = true;
    sendUserProfile();
  }

  Serial.println("[gatt] subscribed — stand still for final reading");
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
            ScaleMeasurement m{};
            if (g_session.onBroadcastFinal(millis(), kg, &m)) {
              Serial.printf("[adv] AABB final from %s\n", adv->getAddress().toString().c_str());
              postClaimedMeasurement(m, "broadcast-aabb");
            }
          }
        }
      }
    }
#endif

#if BLE_MODE == MODE_GATT || BLE_MODE == MODE_AUTO
    // Look for connectable QN / Renpho
    if (!g_session.shouldConnect(millis())) {
      // Help diagnose "second weigh-in does nothing" — often still in post-success cooldown
      static uint32_t lastCdPrint = 0;
      uint32_t rem = g_session.cooldownRemainingMs(millis());
      if (rem > 0 && (millis() - lastCdPrint) > 5000) {
        Serial.printf("[ble] cooldown %lus left — let the scale power off, then wait for armed\n",
                      (unsigned long)((rem + 999) / 1000));
        lastCdPrint = millis();
      }
      return;
    }
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
  if (!g_bleReady) return;
  NimBLEScan* scan = NimBLEDevice::getScan();
  if (scan->isScanning()) {
    scan->stop();
    delay(20);
  }
  scan->setScanCallbacks(&scanCallbacks, false);
  scan->setActiveScan(true);
  scan->setInterval(80);
  scan->setWindow(40);
  scan->setMaxResults(0);
  Serial.println("[scan] start");
  scan->start(0, false, true);  // forever
}

// ---------------------------------------------------------------------------
// WiFi / time
// ---------------------------------------------------------------------------

// Returns true if WiFi was down and just came back.
static bool ensureWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    g_wifiReady = true;
    return false;
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
    configTime(-6 * 3600, 0, "pool.ntp.org", "time.nist.gov");
    return true;  // recovered
  }
  Serial.println("[wifi] FAILED");
  return false;
}

// ---------------------------------------------------------------------------
// Setup / loop
// ---------------------------------------------------------------------------

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  Serial.println();
  Serial.println(F("=== Renpho → τrend (ESP32) ==="));
  Serial.printf("mode=%d tracker=%s:%d tls=%d\n", BLE_MODE, TRACKER_HOST, TRACKER_PORT, TRACKER_TLS);

  ensureWifi();
  if (g_wifiReady) fetchScaleProfile();

  NimBLEDevice::init("diet-ble");
  NimBLEDevice::setPower(ESP_PWR_LVL_P9);
  g_bleReady = true;

  startScan();
}

void loop() {
  // HTTPS weight upload (never from BLE notify callback)
  servicePendingPost();

  if (WiFi.status() != WL_CONNECTED) {
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 10000) {
      lastTry = millis();
      bool recovered = ensureWifi();
      if (recovered && g_bleReady) {
        // WiFi blip on C3 often kills BLE scan — restart it if armed
        Serial.println("[ble] wifi recovered");
        g_haveTarget = false;
        g_wantConnect = false;
        if (!g_pendingPost) fetchScaleProfile();
        if (g_session.isArmed()) {
          Serial.println("[ble] restarting scan");
          startScan();
        }
      }
    }
  } else if (!g_pendingPost &&
             !(g_client && g_client->isConnected()) &&
             (g_profileRefreshPending ||
              (millis() - g_profileFetchedMs > 300000UL))) {
    // Refresh profile only when GATT is idle (WiFi TLS vs BLE coexistence)
    g_profileRefreshPending = false;
    fetchScaleProfile();
  }

#if BLE_MODE == MODE_GATT || BLE_MODE == MODE_AUTO
  if (g_wantConnect && g_haveTarget) {
    g_wantConnect = false;
    bool ok = connectAndSubscribe(g_targetAddr);
    if (!ok) {
      g_haveTarget = false;
      g_session.onDisconnected(millis());
      delay(500);
      if (g_session.isArmed()) startScan();
    }
  }

  // Flush pending stable weight if BF frame never arrives
  {
    ScaleMeasurement m{};
    if (g_session.onTick(millis(), 2500u, &m)) {
      postClaimedMeasurement(m, "gatt-stable-timeout");
    }
  }

  // After a successful post: disconnect and stay quiet until cooldown ends
  if (g_endSession) {
    g_endSession = false;
    if (g_client && g_client->isConnected()) {
      g_client->disconnect();
    }
    g_session.onDisconnected(millis());  // no-op if already Cooldown via onPostSuccess
    g_haveTarget = false;
    g_wantConnect = false;
    g_notifyChr = nullptr;
    g_cmdChr = nullptr;
    stopScanQuiet();
    Serial.println("[ble] session closed — waiting for cooldown before next weigh-in");
  }

  // Arm for the next scale power-on after cooldown
  if (g_session.shouldArm(millis())) {
    g_haveTarget = false;
    g_wantConnect = false;
    Serial.println("[ble] armed — step on scale for next log");
    startScan();
  }

  // If connected but no final arrives, drop and rescan.
  // Idle scales often advertise after a prior weigh-in — we handshake, then sit forever.
  static uint32_t connectedAt = 0;
  static uint32_t lastWaitPrint = 0;
  static bool wasConnected = false;
  if (g_client && g_client->isConnected()) {
    wasConnected = true;
    if (connectedAt == 0) {
      connectedAt = millis();
      lastWaitPrint = 0;
    }
    const uint32_t idleMs = millis() - connectedAt;
    // Heartbeat so serial isn't silent while waiting for a final frame
    if (!g_session.hasClaimed() && (millis() - lastWaitPrint) > 4000) {
      Serial.printf("[gatt] waiting for weight frame… %lus\n",
                    (unsigned long)(idleMs / 1000));
      lastWaitPrint = millis();
    }
    // Unclaimed sessions: give up sooner so the next power-on can be caught
    const uint32_t limitMs = g_session.hasClaimed() ? 45000u : 22000u;
    if (idleMs > limitMs) {
      if (!g_session.hasClaimed()) {
        Serial.println("[gatt] no weight yet — disconnect (power-cycle scale, then step on again)");
      } else {
        Serial.println("[gatt] session timeout — disconnect");
      }
      g_client->disconnect();
      g_session.onDisconnected(millis());
      g_haveTarget = false;
      connectedAt = 0;
      wasConnected = false;
      delay(300);
      if (g_session.isArmed()) startScan();
    }
  } else {
    if (wasConnected) {
      // Unexpected drop (scale powered off mid-session)
      g_session.onDisconnected(millis());
      wasConnected = false;
      g_haveTarget = false;
      if (g_session.isArmed()) startScan();
    }
    connectedAt = 0;
  }
#endif

  delay(20);
}
