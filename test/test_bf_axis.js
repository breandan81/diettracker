/**
 * Node unit tests for body-fat chart axis scaling.
 *
 *   node test/test_bf_axis.js
 */
"use strict";

const assert = require("assert");
const { bodyFatAxisRange, demographicBand } = require("../public/bf_axis.js");

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

if (failed) {
  console.log(`${failed} failed, ${passed} passed`);
  process.exit(1);
}
console.log(`All bf_axis tests passed (${passed} checks).`);
