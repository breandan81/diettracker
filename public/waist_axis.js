/**
 * Waist helpers (browser + Node).
 *
 * Waist is the third leg of the same stool as BMI and body fat, and it catches
 * what neither does: BMI cannot tell muscle from belly, and a bioimpedance
 * body-fat number cannot tell you *where* the fat is. Central fat is the part
 * that tracks metabolic risk, and a tape measure reads it directly.
 *
 * The bands are waist-to-height ratio (WHtR), not raw inches:
 *
 *   - It needs no sex table. The same 0.5 boundary works for men and women,
 *     which the absolute cutoffs (94/102 cm men, 80/88 cm women) do not — they
 *     are two different numbers for the same underlying risk, scaled by the
 *     fact that men are taller.
 *   - "Keep your waist to less than half your height" is the whole rule, and
 *     it is what NICE tells clinicians to say (NG246, Overweight and obesity
 *     management, 2025 — it supersedes CG189).
 *   - It reuses the height already stored for BMI, so nothing new is asked of
 *     the profile.
 *
 * Boundaries 0.40 / 0.50 / 0.60 come from the Ashwell Shape Chart, and NG246
 * classifies adults on the same three: 0.4-0.49 healthy central adiposity,
 * 0.5-0.59 increased, 0.6+ high. Worth knowing that Ashwell describes these as
 * "originally set on pragmatic decisions" rather than derived from an ROC
 * optimum — they are round numbers chosen to be memorable and validated after
 * the fact, not a threshold where risk actually steps.
 *
 * The sex-specific WHO cutoffs (whoCutoff) are reported as context: they are
 * the numbers most people have heard, and they land near WHtR 0.5 at average
 * adult height.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HdWaistAxis = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var RATIO_BANDS = [
    { key: "low", label: "Low", max: 0.4 },
    { key: "healthy", label: "Healthy", min: 0.4, max: 0.5 },
    { key: "increased", label: "Increased", min: 0.5, max: 0.6 },
    { key: "high", label: "High", min: 0.6 },
  ];

  /** Marker travel inside the two open-ended bands (see markerPct). */
  var LOW_SPAN = 0.1;
  var HIGH_SPAN = 0.1;

  /**
   * WHO Expert Consultation (Geneva 2008, published 2011): 94/102 cm for men,
   * 80/88 cm for women — "increased" and "substantially increased" risk of
   * metabolic complications. NIH/NHLBI publishes only the upper pair, rounded
   * to 40 in and 35 in. These are exact conversions, so the women's figure
   * reads 34.6 rather than the familiar 35: same 88 cm, less rounding.
   */
  var WHO_CM = {
    male: { increased: 94, high: 102 },
    female: { increased: 80, high: 88 },
  };
  var CM_PER_IN = 2.54;

  function num(v) {
    var n = Number(v);
    return v == null || v === "" || !Number.isFinite(n) ? null : n;
  }

  function round1(n) {
    return Math.round(n * 10) / 10;
  }

  /**
   * The sex-specific absolute cutoffs, in inches, or null when sex is unset.
   * Context only — the bar itself is WHtR, which does not need this.
   */
  function whoCutoff(sex) {
    var key = String(sex || "").toLowerCase();
    var cm = WHO_CM[key];
    if (!cm) return null;
    return {
      sexLabel: key,
      increasedIn: round1(cm.increased / CM_PER_IN),
      highIn: round1(cm.high / CM_PER_IN),
      increasedCm: cm.increased,
      highCm: cm.high,
    };
  }

  /**
   * The four bands for one height, with each ratio boundary also expressed in
   * inches so the tooltip reads as something you can hold a tape up to.
   *
   * Returns null without a height: a waist of 34 in is healthy at 6'2" and
   * "increased" at 5'4", and there is no defensible default to fall back on.
   */
  function waistBands(heightIn) {
    var h = num(heightIn);
    if (h == null || h <= 0) return null;

    var bands = RATIO_BANDS.map(function (b) {
      var lo = b.min == null ? null : round1(b.min * h);
      var hi = b.max == null ? null : round1(b.max * h);
      var title;
      if (lo == null) title = "Low <" + hi + " in (WHtR <" + b.max.toFixed(2) + ")";
      else if (hi == null)
        title = "High ≥" + lo + " in (WHtR ≥" + b.min.toFixed(2) + ")";
      else
        title =
          b.label +
          " " +
          lo +
          "–" +
          hi +
          " in (WHtR " +
          b.min.toFixed(2) +
          "–" +
          b.max.toFixed(2) +
          ")";
      return {
        key: b.key,
        label: b.label,
        min: b.min,
        max: b.max,
        minIn: lo,
        maxIn: hi,
        title: title,
      };
    });

    return {
      heightIn: h,
      bands: bands,
      // The headline number: half your height.
      healthyIn: { low: bands[1].minIn, high: bands[1].maxIn },
    };
  }

  /**
   * Where a ratio sits on a four-band bar of equal-width segments.
   *
   * Equal widths rather than a linear ratio scale, matching the BMI and
   * body-fat bars: the marker should sit near the right edge of a band it has
   * nearly grown out of. The open ends get a fixed span to travel through.
   */
  function markerPct(ratio) {
    var seg = function (lo, hi, startPct) {
      var t = (ratio - lo) / (hi - lo);
      return startPct + Math.max(0, Math.min(1, t)) * 25;
    };
    if (ratio < 0.4) return seg(0.4 - LOW_SPAN, 0.4, 0);
    if (ratio < 0.5) return seg(0.4, 0.5, 25);
    if (ratio < 0.6) return seg(0.5, 0.6, 50);
    return seg(0.6, 0.6 + HIGH_SPAN, 75);
  }

  /**
   * Classify a waist measurement against the WHtR bands for this height.
   *
   * @param {number|string|null|undefined} waistIn   inches
   * @param {number|string|null|undefined} heightIn  inches (from settings)
   * @param {string|null|undefined} sex              optional, WHO context only
   * @returns {{key, label, ratio, markerPct, bands, healthyIn, heightIn,
   *            who}|null}
   */
  function waistCategory(waistIn, heightIn, sex) {
    var info = waistBands(heightIn);
    var w = num(waistIn);
    if (!info || w == null || w <= 0) return null;

    var ratio = w / info.heightIn;
    var band =
      info.bands.find(function (b) {
        return (b.min == null || ratio >= b.min) && (b.max == null || ratio < b.max);
      }) || info.bands[info.bands.length - 1];

    return {
      key: band.key,
      label: band.label,
      ratio: ratio,
      markerPct: markerPct(ratio),
      bands: info.bands,
      healthyIn: info.healthyIn,
      heightIn: info.heightIn,
      who: whoCutoff(sex),
    };
  }

  return {
    RATIO_BANDS: RATIO_BANDS,
    whoCutoff: whoCutoff,
    waistBands: waistBands,
    waistCategory: waistCategory,
  };
});
