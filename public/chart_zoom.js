/**
 * Shared chartjs-plugin-zoom wiring (browser + Node).
 *
 * One interaction contract for every chart in the app:
 *
 *   scroll              → zoom both axes
 *   scroll over an axis → zoom only that axis
 *   drag                → pan (drag on an axis pans only that axis)
 *   shift + drag        → zoom to a box, Escape cancels
 *   two-finger drag     → pan (one finger still scrolls the page)
 *   pinch               → zoom
 *
 * Per-axis scroll and drag come from the plugin's `scaleMode`: when the pointer
 * sits inside a scale's own strip rather than the plot area, only that scale
 * moves. The two-finger rule is not a plugin option — see installTouchContract.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HdChartZoom = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const DRAG_FILL = "rgba(91, 159, 212, 0.15)";
  const DRAG_BORDER = "rgba(91, 159, 212, 0.85)";

  /**
   * Reject a pan that starts with a single finger, so a one-finger swipe over a
   * chart still scrolls the page. Mouse and pen drags pass through untouched.
   * @param {object} arg  the plugin's {chart, event, point}
   */
  function requireTwoFingers(arg) {
    const src = (arg && arg.event) || {};
    if (src.pointerType !== "touch") return true;
    const count = src.pointers ? src.pointers.length : 1;
    return count >= 2;
  }

  /**
   * Teach Hammer the two-finger contract.
   *
   * chartjs-plugin-zoom builds its own Hammer recognizers and exposes no
   * pointer-count or touch-action option, so both have to be installed on the
   * Hammer namespace before the first chart is constructed. The plugin reads
   * `Hammer.Manager` / `Hammer.Pan` off the live object at chart-creation time,
   * so patching after the plugin script has loaded is fine.
   *
   *   - Pan gets `pointers: 0` ("any count"); its default of exactly 1 makes a
   *     two-finger drag literally unrecognizable. requireTwoFingers then turns
   *     the one-finger case back off, which is the half we actually want gone.
   *   - The Manager gets `touchAction: "pan-y"`. Left to compute it, Hammer
   *     picks "none" for an all-direction pan and swallows every touch over the
   *     canvas — including the vertical swipe that should scroll the page.
   *
   * @param {object} Hammer  the global Hammer namespace, if present
   * @returns {boolean} whether the contract is now installed
   */
  function installTouchContract(Hammer) {
    if (!Hammer) return false;
    if (Hammer.__hdTouchContract) return true;

    const RealManager = Hammer.Manager;
    const RealPan = Hammer.Pan;
    if (typeof RealManager !== "function" || typeof RealPan !== "function") return false;

    Hammer.Manager = function (element, options) {
      return new RealManager(element, Object.assign({ touchAction: "pan-y" }, options));
    };
    Hammer.Manager.prototype = RealManager.prototype;

    Hammer.Pan = function (options) {
      return new RealPan(Object.assign({ pointers: 0 }, options));
    };
    Hammer.Pan.prototype = RealPan.prototype;

    Hammer.__hdTouchContract = true;
    return true;
  }

  /**
   * Chart.js `options.plugins.zoom` block.
   * @param {{mode?: string, limits?: object, onChange?: function}} [opts]
   *   mode     — "x" | "y" | "xy" (default "xy")
   *   limits   — per-scale-id clamps, if a chart needs them
   *   onChange — called whenever the view changes; Chart.js emits no zoom event
   *              of its own, so the plugin callbacks are the only hook. It has
   *              to ride on all four: onZoom/onPan cover the live gestures,
   *              onZoomComplete covers resetZoom() and the 250ms-debounced
   *              wheel settle, onPanComplete covers pan release. Must be
   *              supplied up front — the plugin snapshots these options, so
   *              assigning them after construction would not take effect.
   */
  function zoomOptions(opts) {
    const o = opts || {};
    const mode = o.mode || "xy";
    const settled = typeof o.onChange === "function" ? o.onChange : function () {};
    const block = {
      pan: {
        enabled: true,
        mode,
        // Pointer inside an axis strip → drag that axis alone.
        scaleMode: mode,
        onPanStart: requireTwoFingers,
        onPan: settled,
        onPanComplete: settled,
      },
      zoom: {
        mode,
        // Same rule for the wheel: over an axis, zoom only that axis.
        scaleMode: mode,
        // Bare drag is a pan, so the zoom box moves to shift-drag. The plugin
        // resolves the two by modifier: it rejects drag-zoom unless shift is
        // held, and rejects pan while it is.
        drag: {
          enabled: true,
          modifierKey: "shift",
          backgroundColor: DRAG_FILL,
          borderColor: DRAG_BORDER,
          borderWidth: 1,
        },
        wheel: { enabled: true, speed: 0.08 },
        pinch: { enabled: true },
        onZoom: settled,
        onZoomComplete: settled,
      },
    };
    if (o.limits) block.limits = o.limits;
    return block;
  }

  /**
   * Wire a "reset zoom" button to a chart: hidden until the view is actually
   * zoomed or panned, restores the default view on click.
   *
   * Safe to call before the chart exists — pass a getter.
   *
   * @param {HTMLElement|null} button
   * @param {function(): (object|undefined)} getChart
   * @returns {{sync: function(): void}} sync — re-evaluate button visibility
   */
  function wireResetButton(button, getChart) {
    const noop = { sync: function () {} };
    if (!button || typeof getChart !== "function") return noop;

    const sync = function () {
      const chart = getChart();
      const zoomed = !!(chart && typeof chart.isZoomedOrPanned === "function" && chart.isZoomedOrPanned());
      button.hidden = !zoomed;
    };

    button.addEventListener("click", function () {
      const chart = getChart();
      if (chart && typeof chart.resetZoom === "function") chart.resetZoom();
      sync();
    });

    sync();
    return { sync };
  }

  // Install as soon as this file runs: charts are built later, and the patch
  // has to be in place before the first Hammer Manager is constructed.
  if (typeof self !== "undefined") installTouchContract(self.Hammer);

  return {
    zoomOptions,
    wireResetButton,
    installTouchContract,
    requireTwoFingers,
  };
});
