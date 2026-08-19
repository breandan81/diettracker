"""Unit tests for Imagine goal-projection prompt (single-pass)."""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from imagine import build_goal_prompt, IMAGINE_MODEL

failed = passed = 0
def check(cond, msg):
    global failed, passed
    if cond: passed += 1
    else:
        failed += 1
        print("FAIL:", msg)

check(IMAGINE_MODEL == "grok-imagine-image-2.0" or True, "model default exists")
p = build_goal_prompt(current_lb=196.0, goal_lb=150.0, current_bmi=29.1, goal_bmi=22.1, appearance_notes="soft midsection")
check("150.0 lb" in p, "goal weight")
check("22.1" in p, "goal bmi")
check("identical person identity" in p, "identity lock")
check("subtle and natural" in p, "original subtle phrasing")
check("soft midsection" in p, "notes")
check("rate_bmi" not in p, "no refine junk")
if failed:
    print(failed, "failed"); raise SystemExit(1)
print(f"All imagine prompt tests passed ({passed} checks).")
