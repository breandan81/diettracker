/**
 * Node unit tests for waist-to-height bands.
 *
 *   node test/test_waist_axis.js
 */
"use strict";

const assert = require("assert");
const {
  RATIO_BANDS,
  whoCutoff,
  waistBands,
  waistCategory,
} = require("../public/waist_axis.js");

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

function approx(a, b, eps) {
  return Math.abs(a - b) < (eps == null ? 1e-9 : eps);
}

// --- bands need a height, and refuse to guess one ---
{
  check(waistBands(null) === null, "no height → no bands");
  check(waistBands("") === null, "empty height → no bands");
  check(waistBands(0) === null, "zero height → no bands");
  check(waistBands(-5) === null, "negative height → no bands");
  check(waistBands("70") !== null, "numeric string height accepted");
  check(waistCategory(34, null) === null, "no height → no verdict");
  check(waistCategory(null, 70) === null, "no waist → no verdict");
  check(waistCategory("", 70) === null, "empty waist → no verdict");
  check(waistCategory(0, 70) === null, "zero waist → no verdict");
}

// --- the boundaries are literally half your height ---
{
  const b = waistBands(70);
  check(approx(b.healthyIn.high, 35), `70in → healthy ceiling 35, got ${b.healthyIn.high}`);
  check(approx(b.healthyIn.low, 28), `70in → healthy floor 28, got ${b.healthyIn.low}`);
  check(b.bands.length === 4, "four bands, same shape as BMI/body fat");
  check(
    b.bands.map((x) => x.key).join(",") === "low,healthy,increased,high",
    "band keys in order"
  );
  check(b.bands[0].min == null, "low band is open at the bottom");
  check(b.bands[3].max == null, "high band is open at the top");
  b.bands.forEach((x) => {
    check(typeof x.title === "string" && x.title.length > 0, `${x.key} has a title`);
    check(x.title.includes("in"), `${x.key} title quotes inches`);
    check(x.title.includes("WHtR"), `${x.key} title quotes the ratio`);
  });
  // Adjacent bands must meet exactly — no gap a reading could fall into.
  for (let i = 0; i < 3; i++) {
    check(
      approx(b.bands[i].max, b.bands[i + 1].min),
      `band ${i} ceiling meets band ${i + 1} floor`
    );
  }
}

// --- the same waist changes verdict with height, which is the whole point ---
{
  check(waistCategory(34, 74).key === "healthy", "34in at 6'2\" is healthy");
  check(waistCategory(34, 64).key === "increased", "34in at 5'4\" is increased");
  check(waistCategory(34, 56).key === "high", "34in at 4'8\" is high");
}

// --- boundary behaviour: bands are half-open [min, max) ---
{
  const h = 70;
  check(waistCategory(27.9, h).key === "low", "just under 0.40 → low");
  check(waistCategory(28.0, h).key === "healthy", "exactly 0.40 → healthy");
  check(waistCategory(34.9, h).key === "healthy", "just under 0.50 → healthy");
  check(waistCategory(35.0, h).key === "increased", "exactly 0.50 → increased");
  check(waistCategory(41.9, h).key === "increased", "just under 0.60 → increased");
  check(waistCategory(42.0, h).key === "high", "exactly 0.60 → high");
  check(waistCategory(60, h).key === "high", "far past 0.60 → still high");
}

// --- ratio is reported, not just the verdict ---
{
  const c = waistCategory(35, 70);
  check(approx(c.ratio, 0.5), `35/70 = 0.5, got ${c.ratio}`);
  check(approx(c.heightIn, 70), "height echoed back");
  check(c.healthyIn.high === 35, "healthy ceiling carried on the verdict");
}

// --- marker maps into four equal 25% segments, like the BMI bar ---
{
  const h = 70;
  const pct = (w) => waistCategory(w, h).markerPct;
  check(approx(pct(28), 25), `0.40 sits on the low/healthy seam, got ${pct(28)}`);
  check(approx(pct(35), 50), `0.50 sits on the healthy/increased seam, got ${pct(35)}`);
  check(approx(pct(42), 75), `0.60 sits on the increased/high seam, got ${pct(42)}`);
  check(approx(pct(31.5), 37.5), `mid-healthy is mid-segment, got ${pct(31.5)}`);
  // Open ends still move, and still clamp inside the bar.
  check(pct(26) > 0 && pct(26) < 25, `low band marker travels, got ${pct(26)}`);
  check(approx(pct(49), 100), `WHtR 0.70 reaches the right edge, got ${pct(49)}`);
  check(pct(80) <= 100, "absurdly high waist clamps at 100%");
  check(pct(16) >= 0, "absurdly low waist clamps at 0%");
  // Monotonic: a bigger waist never moves the marker left.
  let prev = -1;
  for (let w = 16; w <= 60; w += 0.25) {
    const v = pct(w);
    check(v >= prev - 1e-9, `marker monotonic at ${w}in (${v} after ${prev})`);
    prev = v;
  }
}

// --- WHO absolute cutoffs, as context only ---
{
  check(whoCutoff(null) === null, "no sex → no WHO cutoff");
  check(whoCutoff("") === null, "empty sex → no WHO cutoff");
  check(whoCutoff("nonbinary") === null, "unknown sex → no WHO cutoff");
  const m = whoCutoff("MALE");
  check(m !== null && m.sexLabel === "male", "sex is case-insensitive");
  check(approx(m.increasedIn, 37.0, 0.05), `94cm ≈ 37in, got ${m.increasedIn}`);
  check(approx(m.highIn, 40.2, 0.05), `102cm ≈ 40.2in, got ${m.highIn}`);
  const f = whoCutoff("female");
  check(approx(f.increasedIn, 31.5, 0.05), `80cm ≈ 31.5in, got ${f.increasedIn}`);
  check(approx(f.highIn, 34.6, 0.05), `88cm ≈ 34.6in, got ${f.highIn}`);
  check(f.increasedIn < m.increasedIn, "women's absolute cutoff is lower");
  check(waistCategory(34, 70, "male").who.increasedIn === m.increasedIn, "verdict carries WHO");
  check(waistCategory(34, 70).who === null, "verdict without sex carries no WHO");
}

// --- the two systems land in the same place at average adult height ---
// This is why WHtR can replace a sex table rather than contradict it: at 5'9"
// a man's half-height is 34.5in against WHO's 37in, at 5'4" a woman's is 32.0in
// against WHO's 31.5in. Which one is stricter flips with height — that is WHtR
// scaling with the body it is measuring, which the fixed cutoffs cannot do.
{
  const man = waistBands(69).healthyIn.high;
  const woman = waistBands(64).healthyIn.high;
  check(Math.abs(man - whoCutoff("male").increasedIn) < 3, `5'9" man: ${man} vs 37`);
  check(Math.abs(woman - whoCutoff("female").increasedIn) < 3, `5'4" woman: ${woman} vs 31.5`);
  check(man < whoCutoff("male").increasedIn, "WHtR stricter for a 5'9\" man");
  check(woman > whoCutoff("female").increasedIn, "WHO stricter for a 5'4\" woman");
}

// --- RATIO_BANDS is the published Ashwell table, not something invented here ---
{
  check(RATIO_BANDS[0].max === 0.4, "first boundary 0.40");
  check(RATIO_BANDS[1].max === 0.5, "second boundary 0.50");
  check(RATIO_BANDS[2].max === 0.6, "third boundary 0.60");
}

if (failed) {
  console.log(`${failed} failed, ${passed} passed`);
  process.exit(1);
}
console.log(`All waist_axis tests passed (${passed} checks).`);
