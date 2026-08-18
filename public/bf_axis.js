/**
 * Body-fat chart Y-axis helpers (browser + Node).
 *
 * Fixed lean→obese span from sex/age (ACE-style norms) so day-to-day
 * noise (e.g. 25.7–26.1%) does not auto-zoom and look dramatic.
 */
(function (root, factory) {
  if (typeof module === "object" && module.exports) {
    module.exports = factory();
  } else {
    root.HdBfAxis = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /**
   * Demographic lean→obese axis band.
   * @param {string|null|undefined} sex  "male" | "female" | …
   * @param {number|string|null|undefined} ageYears
   * @returns {{ min: number, max: number, label: string }}
   */
  function demographicBand(sex, ageYears) {
    const female = String(sex || "").toLowerCase() === "female";
    const ageNum = Number(ageYears);
    // null/"" → Number 0; treat missing/invalid as mid-adult default
    const age =
      ageYears == null || ageYears === "" || !Number.isFinite(ageNum) || ageNum <= 0
        ? 40
        : ageNum;

    let min;
    let max;
    let bracket;
    if (!female) {
      // Men: athletic floor → above obesity threshold
      if (age < 30) {
        min = 5;
        max = 32;
        bracket = "18–29";
      } else if (age < 40) {
        min = 5;
        max = 33;
        bracket = "30–39";
      } else if (age < 50) {
        min = 6;
        max = 35;
        bracket = "40–49";
      } else if (age < 60) {
        min = 7;
        max = 36;
        bracket = "50–59";
      } else {
        min = 8;
        max = 38;
        bracket = "60+";
      }
    } else {
      // Women: higher absolute %
      if (age < 30) {
        min = 12;
        max = 40;
        bracket = "18–29";
      } else if (age < 40) {
        min = 13;
        max = 41;
        bracket = "30–39";
      } else if (age < 50) {
        min = 14;
        max = 42;
        bracket = "40–49";
      } else if (age < 60) {
        min = 15;
        max = 44;
        bracket = "50–59";
      } else {
        min = 16;
        max = 45;
        bracket = "60+";
      }
    }

    const who = female ? "female" : "male";
    return {
      min,
      max,
      label: `${who} · ${bracket} lean→obese (${min}–${max}%)`,
    };
  }

  /**
   * Chart Y domain: demographic band, expanded only if samples fall outside.
   * @param {string|null|undefined} sex
   * @param {number|string|null|undefined} ageYears
   * @param {number[]} [sampleValues]
   */
  function bodyFatAxisRange(sex, ageYears, sampleValues) {
    const band = demographicBand(sex, ageYears);
    let min = band.min;
    let max = band.max;

    const vals = (sampleValues || []).filter((v) => typeof v === "number" && Number.isFinite(v));
    if (vals.length) {
      const lo = Math.min.apply(null, vals);
      const hi = Math.max.apply(null, vals);
      if (lo < min) min = Math.floor(lo) - 1;
      if (hi > max) max = Math.ceil(hi) + 1;
    }

    if (max - min < 8) max = min + 8;
    if (min < 0) min = 0;
    if (max > 60) max = 60;

    return { min, max, label: band.label };
  }

  return {
    demographicBand,
    bodyFatAxisRange,
  };
});
