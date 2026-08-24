/**
 * Shared chartjs-plugin-zoom wiring (browser + Node).
 *
 * One interaction contract for every chart in the app:
 *
 *   drag a box   → zoom to that box
 *   shift + drag → pan
 *   ctrl + wheel → zoom (plain wheel still scrolls the page)
 *   pinch        → zoom (touch)
 *   Escape       → cancel an in-progress drag box
 *
 * The plugin resolves drag-zoom vs. pan by modifier: pan carries "shift", so a
 * bare drag is always a zoom box and a shifted drag is always a pan.
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
   * Chart.js `options.plugins.zoom` block.
   * @param {{mode?: string, limits?: object, onChange?: function}} [opts]
   *   mode     — "x" | "y" | "xy" (default "xy")
   *   limits   — per-scale-id clamps, e.g. a frozen reference axis
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
        modifierKey: "shift",
        onPan: settled,
        onPanComplete: settled,
      },
      zoom: {
        mode,
        drag: {
          enabled: true,
          backgroundColor: DRAG_FILL,
          borderColor: DRAG_BORDER,
          borderWidth: 1,
        },
        wheel: { enabled: true, modifierKey: "ctrl", speed: 0.08 },
        pinch: { enabled: true },
        onZoom: settled,
        onZoomComplete: settled,
      },
    };
    if (o.limits) block.limits = o.limits;
    return block;
  }

  /**
   * Limits entry that freezes a scale at an exact span — used for reference
   * axes (the body-fat lean→obese band) that must not follow a zoom.
   * @param {number} min
   * @param {number} max
   */
  function frozenAxis(min, max) {
    return { min, max, minRange: max - min };
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

  return {
    zoomOptions,
    frozenAxis,
    wireResetButton,
  };
});
