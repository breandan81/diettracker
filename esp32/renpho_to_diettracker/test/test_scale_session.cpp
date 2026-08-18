/**
 * Host unit tests for ScaleSession (no Arduino).
 *
 *   make -C esp32/renpho_to_diettracker test
 */
#include <cassert>
#include <cmath>
#include <cstdio>
#include <string>

#include "../scale_session.h"

static int g_failed = 0;
static int g_passed = 0;

#define CHECK(cond, msg)                                                       \
  do {                                                                         \
    if (!(cond)) {                                                             \
      std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, msg);                \
      g_failed++;                                                              \
    } else {                                                                   \
      g_passed++;                                                              \
    }                                                                          \
  } while (0)

#define CHECK_EQ(a, b, msg) CHECK((a) == (b), msg)
#define CHECK_NEAR(a, b, eps, msg) CHECK(std::fabs((a) - (b)) < (eps), msg)

static void test_one_final_posts_once() {
  ScaleSession s(90000);
  CHECK(s.shouldConnect(0), "armed → connect");
  s.onConnected(100);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::InSession, "in session");
  ScaleMeasurement m{};
  bool post = s.onExtendedFrame(200, 2, 88.95f, 25.8f, &m);
  CHECK(post, "final frame should post");
  CHECK(s.hasClaimed(), "claimed before HTTP");
  CHECK_NEAR(m.weight_kg, 88.95f, 0.01f, "kg");
  CHECK_NEAR(m.body_fat, 25.8f, 0.01f, "bf");
  s.onPostSuccess(200);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Cooldown, "cooldown after post");
  CHECK(!s.shouldConnect(250), "no connect in cooldown");

  // Spam same final again — must not post (also wrong phase)
  post = s.onExtendedFrame(300, 2, 88.95f, 25.8f, &m);
  CHECK(!post, "no second post in same session after success");
}

static void test_spam_finals_claim_before_http() {
  // Claim is immediate — second final BEFORE onPostSuccess must not post.
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(s.onExtendedFrame(10, 2, 88.95f, 25.8f, &m), "first final claims");
  CHECK(s.hasClaimed(), "claimed");
  CHECK(!s.onExtendedFrame(11, 2, 88.95f, 25.8f, &m), "spam final blocked");
  CHECK(!s.onExtendedFrame(12, 2, 90.0f, 26.0f, &m), "different spam blocked");
  CHECK_NEAR(m.weight_kg, 88.95f, 0.01f, "first kg kept in out from first call");
  s.onPostSuccess(20);
  CHECK(!s.onExtendedFrame(30, 2, 88.95f, 25.8f, &m), "ignored after post");
}

static void test_stable_then_final_posts_once_with_bf() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(!s.onExtendedFrame(100, 1, 88.95f, -1.0f, &m), "stable waits");
  CHECK(!s.onTick(500, 2500, &m), "not yet timeout");
  CHECK(s.onExtendedFrame(800, 2, 88.95f, 25.8f, &m), "final posts");
  CHECK_NEAR(m.body_fat, 25.8f, 0.01f, "bf from final");
  s.onPostSuccess(800);
  CHECK(!s.onTick(5000, 2500, &m), "no pending flush after post");
}

static void test_stable_timeout_posts_without_bf() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(!s.onExtendedFrame(100, 1, 88.95f, -1.0f, &m), "stable");
  CHECK(!s.onTick(1000, 2500, &m), "too early");
  CHECK(s.onTick(2700, 2500, &m), "timeout flush");
  CHECK_NEAR(m.weight_kg, 88.95f, 0.01f, "kg");
  CHECK(m.body_fat < 0, "no bf");
  CHECK(s.hasClaimed(), "claimed on timeout");
  // Further ticks must not re-claim
  CHECK(!s.onTick(5000, 2500, &m), "no double tick claim");
  s.onPostSuccess(2700);
}

static void test_cooldown_then_second_session() {
  ScaleSession s(5000);  // 5s cooldown for test speed
  CHECK(s.shouldConnect(0), "start armed");
  s.onConnected(10);
  ScaleMeasurement m{};
  CHECK(s.onExtendedFrame(20, 2, 88.95f, 25.8f, &m), "first weigh-in");
  s.onPostSuccess(20);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Cooldown, "in cooldown");
  CHECK(!s.shouldConnect(1000), "blocked mid cooldown");
  CHECK(!s.shouldArm(1000), "not done");
  CHECK(s.cooldownRemainingMs(1000) > 0, "remaining");
  CHECK(s.cooldownRemainingMs(1000) <= 5000, "remaining bounded");

  CHECK(s.shouldArm(5020), "arm after cooldown");
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Armed, "armed");
  CHECK(s.shouldConnect(5021), "may connect again");
  CHECK(!s.hasClaimed(), "claim cleared on arm");

  s.onConnected(5100);
  CHECK(s.onExtendedFrame(5200, 2, 88.00f, 25.0f, &m), "second weigh-in");
  CHECK_NEAR(m.weight_kg, 88.00f, 0.01f, "new kg");
  s.onPostSuccess(5200);
  CHECK(!s.shouldConnect(5300), "cooldown again");
}

static void test_disconnect_without_post_rearms() {
  ScaleSession s(90000);
  s.onConnected(0);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::InSession, "session");
  s.onDisconnected(1000);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Armed, "re-armed");
  CHECK(s.shouldConnect(1001), "re-armed after failed session");
}

static void test_disconnect_after_claim_enters_cooldown() {
  ScaleSession s(5000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(s.onExtendedFrame(10, 2, 80.0f, 20.0f, &m), "claim");
  // Scale drops before HTTP finishes
  s.onDisconnected(50);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Cooldown, "cooldown on claimed disconnect");
  CHECK(!s.shouldConnect(100), "blocked");
  // HTTP eventually succeeds
  s.onPostSuccess(200);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Cooldown, "still cooldown");
  CHECK(s.shouldArm(5200), "arm later");
}

static void test_post_failure_allows_retry_same_session() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(s.onExtendedFrame(10, 2, 88.95f, 25.8f, &m), "first claim");
  s.onPostFailure(20);
  CHECK(!s.hasClaimed(), "unclaimed after failure");
  CHECK_EQ((int)s.phase(), (int)ScalePhase::InSession, "still in session");
  CHECK(s.onExtendedFrame(30, 2, 88.95f, 25.8f, &m), "retry claim");
  s.onPostSuccess(40);
  CHECK(!s.onExtendedFrame(50, 2, 88.95f, 25.8f, &m), "no third");
}

static void test_post_failure_after_disconnect_rearms() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(s.onExtendedFrame(10, 2, 80.0f, 20.0f, &m), "claim");
  s.onDisconnected(20);  // → Cooldown
  s.onPostFailure(30);   // → Armed
  CHECK_EQ((int)s.phase(), (int)ScalePhase::Armed, "re-armed for next power-on");
  CHECK(s.shouldConnect(40), "can connect");
}

static void test_basic_final() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(!s.onBasicFrame(10, 0x11, 88.5f, &m), "stable waits");
  CHECK(s.onBasicFrame(50, 0x01, 88.5f, &m), "final");
  CHECK(s.hasClaimed(), "claimed");
  CHECK(!s.onBasicFrame(55, 0x01, 88.5f, &m), "spam basic final blocked before HTTP");
  s.onPostSuccess(50);
  CHECK(!s.onBasicFrame(60, 0x01, 88.5f, &m), "no double");
}

static void test_basic_stable_timeout() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(!s.onBasicFrame(10, 0x11, 88.5f, &m), "stable");
  CHECK(s.onTick(2600, 2500, &m), "flush");
  CHECK_NEAR(m.weight_kg, 88.5f, 0.01f, "kg");
  CHECK(m.body_fat < 0, "no bf");
  s.onPostSuccess(2600);
}

static void test_no_connect_until_armed() {
  ScaleSession s(1000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(s.onExtendedFrame(1, 2, 80.0f, 20.0f, &m), "claim");
  s.onPostSuccess(1);
  CHECK(!s.shouldConnect(500), "cooldown blocks");
  CHECK(!s.onExtendedFrame(600, 2, 81.0f, 21.0f, &m), "frames ignored in cooldown");
  CHECK(!s.shouldArm(500), "not yet");
  CHECK(s.shouldArm(1001), "now");
}

static void test_rejects_zero_and_unstable() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(!s.onExtendedFrame(1, 2, 0.0f, 20.0f, &m), "zero kg");
  CHECK(!s.onExtendedFrame(2, 0, 80.0f, -1.0f, &m), "unstable");
  CHECK(!s.onBasicFrame(3, 0x00, 80.0f, &m), "settling");
  CHECK(!s.hasClaimed(), "still unclaimed");
}

static void test_broadcast_final_from_armed() {
  ScaleSession s(5000);
  ScaleMeasurement m{};
  CHECK(s.onBroadcastFinal(100, 90.0f, &m), "broadcast claims");
  CHECK_NEAR(m.weight_kg, 90.0f, 0.01f, "kg");
  CHECK(s.hasClaimed(), "claimed");
  CHECK(!s.onBroadcastFinal(110, 91.0f, &m), "no second broadcast while claimed");
  s.onPostSuccess(120);
  CHECK(!s.onBroadcastFinal(200, 91.0f, &m), "blocked in cooldown");
  CHECK(s.shouldArm(5200), "arm");
  CHECK(s.onBroadcastFinal(5300, 89.5f, &m), "second broadcast session");
  s.onPostSuccess(5300);
}

static void test_cannot_connect_while_in_session() {
  ScaleSession s(90000);
  s.onConnected(0);
  CHECK(!s.shouldConnect(1), "already in session");
  // Double onConnected is ignored
  s.onConnected(2);
  CHECK_EQ((int)s.phase(), (int)ScalePhase::InSession, "still one session");
}

static void test_millis_wrap_safe_cooldown() {
  // near wrap: cooldown_until = 0xFFFFFFF0, now wraps past
  ScaleSession s(100);
  s.onConnected(0);
  ScaleMeasurement m{};
  s.onExtendedFrame(1, 2, 80.0f, 20.0f, &m);
  // Force success at time near uint32 max
  uint32_t t0 = 0xFFFFFFF0u;
  s.onPostSuccess(t0);
  // 50ms later still cooling (wrap-safe signed compare)
  CHECK(!s.shouldArm(t0 + 50), "still cooling across wrap region");
  CHECK(s.shouldArm(t0 + 100), "armed after wrap-safe elapsed");
}

static void test_pending_cleared_on_final() {
  ScaleSession s(90000);
  s.onConnected(0);
  ScaleMeasurement m{};
  CHECK(!s.onExtendedFrame(100, 1, 88.0f, -1.0f, &m), "stable");
  CHECK(s.onExtendedFrame(200, 2, 88.1f, 24.0f, &m), "final");
  // Even if we wait past pending window, tick must not fire
  CHECK(!s.onTick(5000, 2500, &m), "pending cleared by final");
  s.onPostSuccess(200);
}

int main() {
  test_one_final_posts_once();
  test_spam_finals_claim_before_http();
  test_stable_then_final_posts_once_with_bf();
  test_stable_timeout_posts_without_bf();
  test_cooldown_then_second_session();
  test_disconnect_without_post_rearms();
  test_disconnect_after_claim_enters_cooldown();
  test_post_failure_allows_retry_same_session();
  test_post_failure_after_disconnect_rearms();
  test_basic_final();
  test_basic_stable_timeout();
  test_no_connect_until_armed();
  test_rejects_zero_and_unstable();
  test_broadcast_final_from_armed();
  test_cannot_connect_while_in_session();
  test_millis_wrap_safe_cooldown();
  test_pending_cleared_on_final();

  if (g_failed) {
    std::printf("%d check(s) failed, %d passed\n", g_failed, g_passed);
    return 1;
  }
  std::printf("All ScaleSession tests passed (%d checks).\n", g_passed);
  return 0;
}
