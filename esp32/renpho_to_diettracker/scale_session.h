#pragma once
/**
 * Pure session logic for Renpho/QN weigh-ins (no Arduino deps).
 *
 * Contract: at most ONE POST attempt claimed per scale power-on session.
 * Measurement is "claimed" as soon as we decide to post (before HTTP returns),
 * so duplicate final GATT frames cannot double-log.
 *
 * After post success → Cooldown (no scan/connect) → Arm → next advertisement.
 * Post failure un-claims so another final in the same GATT session can retry.
 *
 * Host-unit-tested in test/test_scale_session.cpp
 */

#include <stdint.h>

struct ScaleMeasurement {
  float weight_kg;
  float body_fat;  // < 0 if unknown
};

enum class ScalePhase : uint8_t {
  Armed = 0,      // may connect on advertisement
  InSession = 1,  // connected; accepting at most one post claim
  Cooldown = 2,   // ignore scale until cooldown_until_ms
};

class ScaleSession {
 public:
  explicit ScaleSession(uint32_t cooldown_ms = 90000)
      : cooldown_ms_(cooldown_ms) {}

  ScalePhase phase() const { return phase_; }

  bool isArmed() const { return phase_ == ScalePhase::Armed; }

  /** Advertisement seen — should we open GATT? */
  bool shouldConnect(uint32_t now_ms) const {
    (void)now_ms;
    return phase_ == ScalePhase::Armed;
  }

  void onConnected(uint32_t now_ms) {
    (void)now_ms;
    if (phase_ != ScalePhase::Armed) return;
    phase_ = ScalePhase::InSession;
    claimed_ = false;
    pending_ = false;
    pending_kg_ = 0;
    pending_at_ms_ = 0;
  }

  /**
   * GATT dropped.
   * - No claim yet → re-arm (abandoned / failed handshake).
   * - Already claimed → enter cooldown so we do not immediately reconnect
   *   while HTTP may still be in flight; onPostSuccess/Failure still apply.
   */
  void onDisconnected(uint32_t now_ms) {
    if (phase_ != ScalePhase::InSession) return;
    pending_ = false;
    if (claimed_) {
      phase_ = ScalePhase::Cooldown;
      cooldown_until_ms_ = now_ms + cooldown_ms_;
    } else {
      phase_ = ScalePhase::Armed;
    }
  }

  /**
   * Extended QN 0x10 frame.
   * status 0 = unstable, 1 = stable (no BF yet), 2 = final with metrics.
   * Returns true once — measurement is claimed immediately.
   */
  bool onExtendedFrame(uint32_t now_ms, uint8_t status, float kg, float body_fat,
                       ScaleMeasurement* out) {
    if (phase_ != ScalePhase::InSession || claimed_) return false;
    if (kg <= 0) return false;

    if (status == 2) {
      return claim(now_ms, kg, body_fat, out);
    }
    if (status == 1) {
      pending_ = true;
      pending_kg_ = kg;
      pending_at_ms_ = now_ms;
      return false;
    }
    return false;
  }

  /**
   * Basic QN 0x10 frame.
   * status 0x00 settling, 0x11 stable+BIA, 0x01 final.
   */
  bool onBasicFrame(uint32_t now_ms, uint8_t status, float kg,
                    ScaleMeasurement* out) {
    if (phase_ != ScalePhase::InSession || claimed_) return false;
    if (kg <= 0) return false;

    if (status == 0x01) {
      return claim(now_ms, kg, -1.0f, out);
    }
    if (status == 0x11) {
      pending_ = true;
      pending_kg_ = kg;
      pending_at_ms_ = now_ms;
      return false;
    }
    return false;
  }

  /**
   * Broadcast AABB final without a prior GATT session.
   * Opens a synthetic InSession claim from Armed.
   */
  bool onBroadcastFinal(uint32_t now_ms, float kg, ScaleMeasurement* out) {
    if (phase_ != ScalePhase::Armed) return false;
    if (kg <= 0) return false;
    onConnected(now_ms);
    return claim(now_ms, kg, -1.0f, out);
  }

  /** Call after HTTP 2xx (including server-side dedupe 200). */
  void onPostSuccess(uint32_t now_ms) {
    claimed_ = true;
    pending_ = false;
    phase_ = ScalePhase::Cooldown;
    cooldown_until_ms_ = now_ms + cooldown_ms_;
  }

  /**
   * HTTP failed — allow another claim.
   * If we already dropped to Cooldown via disconnect-during-post, re-arm
   * so the next scale power-on can retry.
   */
  void onPostFailure(uint32_t now_ms) {
    (void)now_ms;
    claimed_ = false;
    if (phase_ == ScalePhase::Cooldown) {
      phase_ = ScalePhase::Armed;
      cooldown_until_ms_ = 0;
    }
  }

  /**
   * Flush pending stable reading if BF/final never arrived.
   * Returns true once (claims).
   */
  bool onTick(uint32_t now_ms, uint32_t wait_ms, ScaleMeasurement* out) {
    if (phase_ != ScalePhase::InSession || claimed_ || !pending_) return false;
    if ((now_ms - pending_at_ms_) < wait_ms) return false;
    pending_ = false;
    return claim(now_ms, pending_kg_, -1.0f, out);
  }

  /** Cooldown finished — caller should startScan(). */
  bool shouldArm(uint32_t now_ms) {
    if (phase_ != ScalePhase::Cooldown) return false;
    if ((int32_t)(now_ms - cooldown_until_ms_) < 0) return false;
    phase_ = ScalePhase::Armed;
    claimed_ = false;
    pending_ = false;
    cooldown_until_ms_ = 0;
    return true;
  }

  uint32_t cooldownRemainingMs(uint32_t now_ms) const {
    if (phase_ != ScalePhase::Cooldown) return 0;
    if ((int32_t)(now_ms - cooldown_until_ms_) >= 0) return 0;
    return cooldown_until_ms_ - now_ms;
  }

  bool hasClaimed() const { return claimed_; }

 private:
  bool claim(uint32_t now_ms, float kg, float bf, ScaleMeasurement* out) {
    (void)now_ms;
    if (claimed_) return false;
    claimed_ = true;  // before HTTP — blocks duplicate finals
    pending_ = false;
    if (out) {
      out->weight_kg = kg;
      out->body_fat = bf;
    }
    return true;
  }

  uint32_t cooldown_ms_;
  ScalePhase phase_ = ScalePhase::Armed;
  bool claimed_ = false;
  bool pending_ = false;
  float pending_kg_ = 0;
  uint32_t pending_at_ms_ = 0;
  uint32_t cooldown_until_ms_ = 0;
};
