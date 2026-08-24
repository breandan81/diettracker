/**
 * Node unit tests for the shared chart zoom helper.
 *
 *   node test/test_chart_zoom.js
 */
"use strict";

const { zoomOptions, frozenAxis, wireResetButton } = require("../public/chart_zoom.js");

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

// Drag-zoom and pan must be separated by a modifier, or a drag is ambiguous:
// the plugin rejects drag-zoom while pan's modifier is held, and vice versa.
{
  const o = zoomOptions();
  check(o.zoom.drag.enabled === true, "drag-zoom enabled by default");
  check(o.pan.enabled === true, "pan enabled by default");
  check(o.pan.modifierKey === "shift", `pan modifier shift, got ${o.pan.modifierKey}`);
  check(o.zoom.drag.modifierKey == null, "bare drag is a zoom box (no modifier)");
}

// Plain wheel must keep scrolling the page — zoom only with ctrl held.
{
  const o = zoomOptions();
  check(o.zoom.wheel.enabled === true, "wheel zoom enabled");
  check(o.zoom.wheel.modifierKey === "ctrl", `wheel modifier ctrl, got ${o.zoom.wheel.modifierKey}`);
  check(o.zoom.pinch.enabled === true, "pinch enabled for touch");
}

// Mode propagates to both zoom and pan, defaulting to xy.
{
  check(zoomOptions().zoom.mode === "xy", "default mode xy");
  const x = zoomOptions({ mode: "x" });
  check(x.zoom.mode === "x" && x.pan.mode === "x", "explicit mode reaches zoom and pan");
}

// limits are only emitted when supplied, so charts without a frozen axis stay clean.
{
  check(zoomOptions().limits === undefined, "no limits key without opts.limits");
  const lim = { yBf: frozenAxis(6, 35) };
  check(zoomOptions({ limits: lim }).limits === lim, "limits passed through");
}

// A frozen axis pins an exact span: minRange equal to the full span leaves no
// room to zoom in, and min/max leave none to zoom out.
{
  const f = frozenAxis(6, 35);
  check(f.min === 6 && f.max === 35, `frozen bounds 6..35, got ${f.min}..${f.max}`);
  check(f.minRange === 29, `frozen minRange 29, got ${f.minRange}`);
  const unit = frozenAxis(1, 10);
  check(unit.minRange === 9, `1..10 minRange 9, got ${unit.minRange}`);
}

// onChange must land on ALL FOUR plugin callbacks. onZoomComplete alone is not
// enough: the programmatic zoom API only fires onZoom, and the wheel path
// debounces onZoomComplete by 250ms — either gap leaves the reset button stale.
{
  const hooks = ["onZoom", "onZoomComplete"];
  const panHooks = ["onPan", "onPanComplete"];
  let hits = 0;
  const o = zoomOptions({ onChange: () => hits++ });

  hooks.forEach((h) => check(typeof o.zoom[h] === "function", `zoom.${h} wired`));
  panHooks.forEach((h) => check(typeof o.pan[h] === "function", `pan.${h} wired`));

  hooks.forEach((h) => o.zoom[h]());
  panHooks.forEach((h) => o.pan[h]());
  check(hits === 4, `every callback reaches onChange, got ${hits} of 4`);

  const bare = zoomOptions();
  hooks.forEach((h) => bare.zoom[h]());
  panHooks.forEach((h) => bare.pan[h]());
  check(true, "callbacks are safe no-ops without onChange");
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
