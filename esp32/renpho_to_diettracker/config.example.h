#pragma once

// Copy this file to config.h and edit.
//   cp config.example.h config.h

// --- WiFi ---
#define WIFI_SSID     "your-ssid"
#define WIFI_PASS     "your-password"

// Prefer a LAN IP; mDNS (tomservo.local) is unreliable on some ESP32 builds.
#define TRACKER_HOST  "192.168.1.50"
#define TRACKER_PORT  8510

// Optional path prefix if you reverse-proxy later
#define TRACKER_PATH  "/api/weights"

// Note stored on each auto log
#define WEIGHT_NOTE   "renpho-ble"

// Post weight in pounds (Hacker's Diet default)
#define WEIGHT_UNIT_POST  "lb"   // only "lb" supported by tracker today

// Empty string = accept first Renpho/QN-looking device.
// After first successful Serial discovery, lock it:
//   #define SCALE_MAC "A4:C1:38:12:34:56"
#define SCALE_MAC  ""

// BLE strategy:
//   MODE_AUTO      — try GATT connect; also parse AABB broadcasts
//   MODE_GATT      — connectable QN / FFF0 only
//   MODE_BROADCAST — advertisement manufacturer-data only
#define MODE_AUTO      0
#define MODE_GATT      1
#define MODE_BROADCAST 2
#define BLE_MODE       MODE_AUTO

// Don't re-POST the same/near weight within this window
#define DEDUPE_SECONDS  90

// Serial debug
#define SERIAL_BAUD  115200
