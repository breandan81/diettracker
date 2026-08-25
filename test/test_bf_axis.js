/**
 * Node unit tests for body-fat chart axis scaling.
 *
 *   node test/test_bf_axis.js
 */
"use strict";

const assert = require("assert");
const {
  bodyFatAxisRange,
  demographicBand,
  bodyFatBands,
  bodyFatCategory,
} = require("../public/bf_axis.js");

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

function approx(a, b) {
  return Math.abs(a - b) < 1e-9;
}

// 44yo man — user's profile: tight readings must not shrink the axis
{
  const r = bodyFatAxisRange("male", 44, [25.7, 26.1, 25.8]);
  check(approx(r.min, 6), `male 44 min=6 got ${r.min}`);
  check(approx(r.max, 35), `male 44 max=35 got ${r.max}`);
  check(r.max - r.min >= 20, "male 44 span wide enough");
  check(r.label.includes("40–49"), `label age bracket: ${r.label}`);
}

// Same man, no samples — still full band
{
  const r = bodyFatAxisRange("male", 44, []);
  check(approx(r.min, 6) && approx(r.max, 35), "empty samples still demographic");
}

// Outlier above obese expands max only
{
  const r = bodyFatAxisRange("male", 44, [25, 40]);
  check(approx(r.min, 6), "outlier keeps lean floor");
  check(r.max >= 41, `outlier expands max got ${r.max}`);
}

// Outlier below athletic expands min
{
  const r = bodyFatAxisRange("male", 44, [3.5, 12]);
  check(r.min <= 2, `very lean expands min got ${r.min}`);
  check(approx(r.max, 35), "keeps obese ceiling");
}

// Female 44 — higher band
{
  const r = bodyFatAxisRange("female", 44, [28, 29]);
  check(approx(r.min, 14), `female 44 min got ${r.min}`);
  check(approx(r.max, 42), `female 44 max got ${r.max}`);
  check(r.min > bodyFatAxisRange("male", 44).min, "female floor above male");
}

// Age brackets shift
{
  const young = demographicBand("male", 25);
  const mid = demographicBand("male", 44);
  const old = demographicBand("male", 65);
  check(young.min <= mid.min && mid.min <= old.min, "male floor rises with age");
  check(young.max <= mid.max && mid.max <= old.max, "male ceiling rises with age");
}

// Default sex/age when unset → male-ish 40 band
{
  const r = bodyFatAxisRange(null, null, [20]);
  check(approx(r.min, 6) && approx(r.max, 35), `defaults got ${r.min}-${r.max}`);
}

// String age from settings JSON
{
  const r = bodyFatAxisRange("male", "44", [25.9]);
  check(approx(r.min, 6) && approx(r.max, 35), "string age ok");
}

// --- verdict bands (the fat% twin of the BMI bar) ---

// Gallagher/NIH table, spot-checked against the published cutoffs.
{
  const m = bodyFatBands("male", 44);
  check(m.bracket === "40–59", `male 44 bracket, got ${m.bracket}`);
  check(m.healthy.low === 11 && m.healthy.high === 22, `male 40–59 healthy 11–22, got ${m.healthy.low}–${m.healthy.high}`);
  check(m.bands.map((b) => b.key).join(",") === "underfat,healthy,overfat,obese", "four bands, low to high");
  check(m.bands[3].min === 28, `male 40–59 obese floor 28, got ${m.bands[3].min}`);

  const w = bodyFatBands("female", 44);
  check(w.healthy.low === 23 && w.healthy.high === 34, `female 40–59 healthy 23–34, got ${w.healthy.low}–${w.healthy.high}`);
  check(w.bands[3].min === 40, `female 40–59 obese floor 40, got ${w.bands[3].min}`);
}

// The whole point of the request: same number, different verdict by sex.
{
  check(bodyFatCategory("male", 44, 26).key === "overfat", "26% is overfat for a 44yo man");
  check(bodyFatCategory("female", 44, 26).key === "healthy", "26% is healthy for a 44yo woman");
}

// ...and by age, at a fixed sex.
{
  check(bodyFatCategory("male", 25, 23).key === "overfat", "23% is overfat for a 25yo man");
  check(bodyFatCategory("male", 65, 23).key === "healthy", "23% is healthy for a 65yo man");
}

// Every band is reachable, and the boundary belongs to the band above it.
{
  const b = (v) => bodyFatCategory("male", 44, v).key;
  check(b(6) === "underfat", `6% underfat, got ${b(6)}`);
  check(b(10.9) === "underfat", "just under the healthy floor is underfat");
  check(b(11) === "healthy", "the healthy floor is healthy");
  check(b(21.9) === "healthy", "just under the overfat floor is healthy");
  check(b(22) === "overfat", "the overfat floor is overfat");
  check(b(27.9) === "overfat", "just under the obese floor is overfat");
  check(b(28) === "obese", "the obese floor is obese");
  check(b(55) === "obese", "far above stays obese");
}

// Sex is required — guessing male would call a healthy woman obese.
{
  check(bodyFatBands(null, 44) === null, "no sex → no bands");
  check(bodyFatBands("", 44) === null, "empty sex → no bands");
  check(bodyFatBands("other", 44) === null, "unknown sex → no bands");
  check(bodyFatCategory(null, 44, 26) === null, "no sex → no verdict");
}

// Age, unlike sex, has a safe default and arrives as a string from settings.
{
  check(bodyFatBands("male", null).bracket === "40–59", "missing age → mid-adult bracket");
  check(bodyFatBands("male", "").bracket === "40–59", "empty age → mid-adult bracket");
  check(bodyFatBands("male", "25").bracket === "20–39", "string age is parsed");
  check(bodyFatBands("male", 120).bracket === "60+", "past the last bracket still lands");
  check(bodyFatCategory("MALE", 44, 26).key === "overfat", "sex is case-insensitive");
}

// No reading, no verdict.
{
  check(bodyFatCategory("male", 44, null) === null, "null bf → no verdict");
  check(bodyFatCategory("male", 44, undefined) === null, "undefined bf → no verdict");
  check(bodyFatCategory("male", 44, NaN) === null, "NaN bf → no verdict");
}

// Marker: four equal quarters, and it travels inside its own band rather than
// pinning to the left edge (the bug the BMI bar was fixed for).
{
  const pct = (v) => bodyFatCategory("male", 44, v).markerPct;
  check(pct(11) === 25, `healthy floor sits at the 25% seam, got ${pct(11)}`);
  check(pct(22) === 50, `overfat floor sits at the 50% seam, got ${pct(22)}`);
  check(pct(28) === 75, `obese floor sits at the 75% seam, got ${pct(28)}`);
  check(pct(21.9) > 49 && pct(21.9) < 50, `top of healthy is near its right edge, got ${pct(21.9)}`);
  check(pct(16.5) > 37 && pct(16.5) < 38, `mid-healthy is mid-band, got ${pct(16.5)}`);
  // Open ends clamp instead of running off the bar.
  check(pct(0) === 0, `0% pins to the left edge, got ${pct(0)}`);
  check(pct(90) === 100, `absurdly high pins to the right edge, got ${pct(90)}`);
  check(pct(33) === 87.5, `mid-way into obese is mid-band, got ${pct(33)}`);
  check(pct(38) === 100, `obese floor + span reaches the edge, got ${pct(38)}`);
}

// Segment titles are what the tooltips show, so they must state real numbers.
{
  const t = bodyFatBands("female", 30).bands.map((b) => b.title);
  check(t[0] === "Underfat <21%", `got ${t[0]}`);
  check(t[1] === "Healthy 21–33%", `got ${t[1]}`);
  check(t[2] === "Overfat 33–39%", `got ${t[2]}`);
  check(t[3] === "Obese ≥39%", `got ${t[3]}`);
}

// Every band sits inside the chart axis span, or the tile and the graph would
// disagree about where "obese" starts.
{
  ["male", "female"].forEach((sex) => {
    [25, 44, 65].forEach((age) => {
      const axis = bodyFatAxisRange(sex, age, []);
      const bands = bodyFatBands(sex, age);
      check(axis.min <= bands.healthy.low, `${sex} ${age}: axis floor below healthy floor`);
      check(axis.max >= bands.bands[3].min, `${sex} ${age}: axis ceiling above obese floor`);
    });
  });
}

if (failed) {
  console.log(`${failed} failed, ${passed} passed`);
  process.exit(1);
}
console.log(`All bf_axis tests passed (${passed} checks).`);
