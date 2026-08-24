/**
 * Node unit tests for the shared chart zoom helper.
 *
 *   node test/test_chart_zoom.js
 */
"use strict";

const {
  zoomOptions,
  wireResetButton,
  installTouchContract,
  requireTwoFingers,
} = require("../public/chart_zoom.js");

let failed = 0;
let passed = 0;

function check(cond, msg) {
  if (!cond) {
    console.log("FAIL:", msg);
    failed++;
  } else {
    passed++;
  }
}

// Scroll must zoom outright — a modifier here would be the old contract.
{
  const o = zoomOptions();
  check(o.zoom.wheel.enabled === true, "wheel zoom enabled");
  check(o.zoom.wheel.modifierKey == null, "bare scroll zooms (no modifier)");
  check(o.zoom.pinch.enabled === true, "pinch enabled for touch");
}

// scaleMode is what makes a scroll over an axis zoom only that axis: the plugin
// picks the scale under the pointer, and falls back to `mode` in the plot area.
{
  const o = zoomOptions();
  check(o.zoom.scaleMode === "xy", `zoom.scaleMode xy, got ${o.zoom.scaleMode}`);
  check(o.pan.scaleMode === "xy", `pan.scaleMode xy, got ${o.pan.scaleMode}`);
  const x = zoomOptions({ mode: "x" });
  check(x.zoom.scaleMode === "x" && x.pan.scaleMode === "x", "scaleMode tracks mode");
}

// Bare drag pans; the zoom box moves to shift. The plugin disambiguates purely
// by modifier, so pan must have none and drag must have one — never both, never
// neither, or a single drag fires both handlers.
{
  const o = zoomOptions();
  check(o.pan.enabled === true, "pan enabled");
  check(o.pan.modifierKey == null, "bare drag pans (no modifier)");
  check(o.zoom.drag.enabled === true, "box zoom still available");
  check(o.zoom.drag.modifierKey === "shift", `box zoom on shift, got ${o.zoom.drag.modifierKey}`);
}

// Mode propagates to both zoom and pan, defaulting to xy.
{
  check(zoomOptions().zoom.mode === "xy", "default mode xy");
  const x = zoomOptions({ mode: "x" });
  check(x.zoom.mode === "x" && x.pan.mode === "x", "explicit mode reaches zoom and pan");
}

// limits are only emitted when supplied — no chart pins an axis today.
{
  check(zoomOptions().limits === undefined, "no limits key without opts.limits");
  const lim = { yBf: { min: 6, max: 35 } };
  check(zoomOptions({ limits: lim }).limits === lim, "limits passed through when asked for");
}

// onChange must land on ALL FOUR plugin callbacks. onZoomComplete alone is not
// enough: the programmatic zoom API only fires onZoom, and the wheel path
// debounces onZoomComplete by 250ms — either gap leaves the reset button stale.
{
  const zoomHooks = ["onZoom", "onZoomComplete"];
  const panHooks = ["onPan", "onPanComplete"];
  let hits = 0;
  const o = zoomOptions({ onChange: () => hits++ });

  zoomHooks.forEach((h) => check(typeof o.zoom[h] === "function", `zoom.${h} wired`));
  panHooks.forEach((h) => check(typeof o.pan[h] === "function", `pan.${h} wired`));

  zoomHooks.forEach((h) => o.zoom[h]());
  panHooks.forEach((h) => o.pan[h]());
  check(hits === 4, `every callback reaches onChange, got ${hits} of 4`);

  const bare = zoomOptions();
  zoomHooks.forEach((h) => bare.zoom[h]());
  panHooks.forEach((h) => bare.pan[h]());
  check(true, "callbacks are safe no-ops without onChange");
}

// --- the two-finger rule ---

// One finger belongs to the page, two to the chart. A mouse only ever has one
// pointer, so it must never be caught by the finger count.
{
  const touch = (n) => ({ event: { pointerType: "touch", pointers: new Array(n).fill(0) } });
  check(requireTwoFingers(touch(1)) === false, "one finger is rejected — page keeps scrolling");
  check(requireTwoFingers(touch(2)) === true, "two fingers pan");
  check(requireTwoFingers(touch(3)) === true, "three fingers pan");

  check(requireTwoFingers({ event: { pointerType: "mouse", pointers: [0] } }) === true, "mouse drag pans");
  check(requireTwoFingers({ event: { pointerType: "pen", pointers: [0] } }) === true, "pen drag pans");
  // Defensive: the plugin hands us whatever Hammer produced.
  check(requireTwoFingers({ event: { pointerType: "touch" } }) === false, "touch with no pointer list counts as one");
  check(requireTwoFingers({}) === true, "missing event does not block a pan");
  check(requireTwoFingers() === true, "missing arg does not block a pan");
}

// zoomOptions must actually install the rule, not just export it.
{
  check(zoomOptions().pan.onPanStart === requireTwoFingers, "onPanStart enforces the finger count");
}

// The Hammer patch: Pan must accept any pointer count (its default of exactly
// one makes a two-finger drag unrecognizable) and the Manager must not let
// Hammer compute touchAction:none, which would eat one-finger page scrolling.
{
  const seen = { pan: null, manager: null };
  function FakePan(options) { seen.pan = options; }
  function FakeManager(element, options) { seen.manager = options; }
  const Hammer = { Pan: FakePan, Manager: FakeManager };

  check(installTouchContract(Hammer) === true, "patch installs on a well-formed Hammer");
  check(Hammer.Pan !== FakePan, "Pan was wrapped");
  check(Hammer.Manager !== FakeManager, "Manager was wrapped");

  // Mimic the plugin's own construction calls.
  new Hammer.Pan({ threshold: 10, enable: () => true });
  check(seen.pan.pointers === 0, `Pan gets pointers:0 (any count), got ${seen.pan && seen.pan.pointers}`);
  check(seen.pan.threshold === 10, "plugin's own Pan options survive the wrap");
  check(typeof seen.pan.enable === "function", "plugin's enable callback survives the wrap");

  new Hammer.Manager({});
  check(seen.manager.touchAction === "pan-y", `Manager gets touchAction pan-y, got ${seen.manager && seen.manager.touchAction}`);

  // Idempotent: a second install must not wrap the wrapper.
  const wrappedPan = Hammer.Pan;
  check(installTouchContract(Hammer) === true, "second install reports success");
  check(Hammer.Pan === wrappedPan, "second install does not double-wrap");
}

// A Hammer that is absent or shaped differently (version bump) must degrade to
// a no-op rather than throwing on page load.
{
  check(installTouchContract(null) === false, "no Hammer → no patch, no throw");
  check(installTouchContract(undefined) === false, "undefined Hammer → no patch");
  check(installTouchContract({}) === false, "Hammer without Pan/Manager → no patch");
  check(installTouchContract({ Pan: 1, Manager: 2 }) === false, "non-callable Pan/Manager → no patch");
}

// --- wireResetButton, against a minimal fake button/chart ---

function fakeButton() {
  return {
    hidden: false,
    handlers: [],
    addEventListener(type, fn) {
      if (type === "click") this.handlers.push(fn);
    },
    click() {
      this.handlers.forEach((fn) => fn());
    },
  };
}

// Hidden while the default view is showing, revealed once zoomed.
{
  let zoomed = false;
  const btn = fakeButton();
  const chart = {
    isZoomedOrPanned: () => zoomed,
    resetZoom() {
      zoomed = false;
    },
  };
  const ui = wireResetButton(btn, () => chart);

  check(btn.hidden === true, "button hidden at default view");
  zoomed = true;
  ui.sync();
  check(btn.hidden === false, "button shown once zoomed");

  btn.click();
  check(zoomed === false, "click resets the chart zoom");
  check(btn.hidden === true, "button re-hides after reset");
}

// The chart is created after the button exists, so a null chart must not throw.
{
  let chart = null;
  const btn = fakeButton();
  const ui = wireResetButton(btn, () => chart);
  check(btn.hidden === true, "hidden while chart is not built yet");
  btn.click();
  chart = { isZoomedOrPanned: () => true, resetZoom() {} };
  ui.sync();
  check(btn.hidden === false, "picks up the chart once it exists");
}

// Missing button (panel not rendered) degrades to a no-op syncer.
{
  const ui = wireResetButton(null, () => ({}));
  ui.sync();
  check(typeof ui.sync === "function", "null button yields a usable no-op");
}

console.log(`${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
