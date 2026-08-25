/**
 * Body-fat helpers, sex- and age-aware (browser + Node).
 *
 * Two jobs, both keyed off the same demographics:
 *
 *   bodyFatAxisRange — fixed lean→obese chart span so day-to-day noise
 *     (e.g. 25.7–26.1%) does not auto-zoom and look dramatic.
 *   bodyFatBands / bodyFatCategory — the four-band verdict behind the stat
 *     tile's bar, the fat% counterpart to the BMI bar.
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

  /**
   * Healthy-fat brackets from Gallagher et al. (Am J Clin Nutr 2000), the
   * table NIH publishes alongside BMI: the cutoffs were fitted so that each
   * body-fat boundary lands on the matching BMI boundary (18.5 / 25 / 30) for
   * that sex and age. That is what makes these the right ranges to sit beside
   * the BMI bar — same four verdicts, same thresholds, expressed in fat%.
   *
   * Fat mass rises with age at a fixed BMI, and women carry more of it at every
   * age, so the whole table shifts by both — one set of numbers cannot serve
   * both sexes.
   *
   *   [healthy low, healthy high, obese floor]
   */
  const HEALTHY_BRACKETS = {
    male: [
      { maxAge: 39, bracket: "20–39", low: 8, high: 20, obese: 25 },
      { maxAge: 59, bracket: "40–59", low: 11, high: 22, obese: 28 },
      { maxAge: Infinity, bracket: "60+", low: 13, high: 25, obese: 30 },
    ],
    female: [
      { maxAge: 39, bracket: "20–39", low: 21, high: 33, obese: 39 },
      { maxAge: 59, bracket: "40–59", low: 23, high: 34, obese: 40 },
      { maxAge: Infinity, bracket: "60+", low: 24, high: 36, obese: 42 },
    ],
  };

  /** Marker travel inside the two open-ended bands (see markerPct). */
  const UNDERFAT_SPAN = 8;
  const OBESE_SPAN = 10;

  /**
   * The four bands for one sex/age, low to high.
   *
   * Returns null when sex is unset: defaulting to male would quietly label a
   * woman at 28% "obese" when that is squarely healthy for her. The axis band
   * can guess; a verdict printed next to the number cannot.
   *
   * @param {string|null|undefined} sex  "male" | "female"
   * @param {number|string|null|undefined} ageYears  missing → 40 (mid-adult)
   * @returns {{bands: object[], bracket: string, sexLabel: string,
   *            healthy: {low: number, high: number}}|null}
   */
  function bodyFatBands(sex, ageYears) {
    const key = String(sex || "").toLowerCase();
    const table = HEALTHY_BRACKETS[key];
    if (!table) return null;

    const ageNum = Number(ageYears);
    const age =
      ageYears == null || ageYears === "" || !Number.isFinite(ageNum) || ageNum <= 0
        ? 40
        : ageNum;
    const row = table.find(function (r) {
      return age <= r.maxAge;
    });

    return {
      sexLabel: key,
      bracket: row.bracket,
      healthy: { low: row.low, high: row.high },
      bands: [
        { key: "underfat", label: "Underfat", max: row.low, title: `Underfat <${row.low}%` },
        { key: "healthy", label: "Healthy", min: row.low, max: row.high,
          title: `Healthy ${row.low}–${row.high}%` },
        { key: "overfat", label: "Overfat", min: row.high, max: row.obese,
          title: `Overfat ${row.high}–${row.obese}%` },
        { key: "obese", label: "Obese", min: row.obese, title: `Obese ≥${row.obese}%` },
      ],
    };
  }

  /**
   * Where a reading sits on a four-band bar of equal-width segments.
   *
   * Equal widths, not a linear fat% scale: the bands differ in width (healthy
   * spans 12 points, overfat 6), and a linear bar would leave the marker at the
   * far left of a band it has nearly grown out of. The open ends get a fixed
   * span to travel through so the marker still moves there.
   */
  function markerPct(bands, value) {
    const seg = function (lo, hi, startPct) {
      const t = (value - lo) / (hi - lo);
      return startPct + Math.max(0, Math.min(1, t)) * 25;
    };
    const lo = bands[0].max;
    const mid = bands[1].max;
    const obese = bands[3].min;
    if (value < lo) return seg(Math.max(0, lo - UNDERFAT_SPAN), lo, 0);
    if (value < mid) return seg(lo, mid, 25);
    if (value < obese) return seg(mid, obese, 50);
    return seg(obese, obese + OBESE_SPAN, 75);
  }

  /**
   * Classify a body-fat reading against its sex/age bands.
   *
   * @param {string|null|undefined} sex
   * @param {number|string|null|undefined} ageYears
   * @param {number|null|undefined} bodyFatPct
   * @returns {{key: string, label: string, markerPct: number, bands: object[],
   *            bracket: string, sexLabel: string,
   *            healthy: {low: number, high: number}}|null}
   */
  function bodyFatCategory(sex, ageYears, bodyFatPct) {
    const info = bodyFatBands(sex, ageYears);
    const v = Number(bodyFatPct);
    if (!info || bodyFatPct == null || !Number.isFinite(v)) return null;

    const band =
      info.bands.find(function (b) {
        return (b.min == null || v >= b.min) && (b.max == null || v < b.max);
      }) || info.bands[info.bands.length - 1];

    return {
      key: band.key,
      label: band.label,
      markerPct: markerPct(info.bands, v),
      bands: info.bands,
      bracket: info.bracket,
      sexLabel: info.sexLabel,
      healthy: info.healthy,
    };
  }

  return {
    demographicBand,
    bodyFatAxisRange,
    bodyFatBands,
    bodyFatCategory,
  };
});
