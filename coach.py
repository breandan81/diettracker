"""On-demand pep talks via local KoboldCPP.

The small local model is bad at inventing causal language from raw signed
numbers ("Despite a deficit… still declining"). We therefore pre-digest the
trend into a plain-English BRIEFING and tell the model to rephrase/cheerlead
that briefing — not re-derive physics.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Optional

KOBOLD_URL = os.environ.get("KOBOLD_URL", "http://127.0.0.1:5001").rstrip("/")
KOBOLD_TIMEOUT = float(os.environ.get("KOBOLD_TIMEOUT", "90"))


def _http_json(method: str, url: str, payload: Optional[dict] = None, timeout: float = 10.0) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        if not body:
            return {}
        return json.loads(body)


def kobold_status() -> dict:
    """Lightweight health + model name."""
    try:
        model = _http_json("GET", f"{KOBOLD_URL}/api/v1/model", timeout=3.0)
        ver = {}
        try:
            ver = _http_json("GET", f"{KOBOLD_URL}/api/extra/version", timeout=3.0)
        except Exception:
            pass
        name = model.get("result") if isinstance(model, dict) else str(model)
        return {
            "ok": True,
            "url": KOBOLD_URL,
            "model": name,
            "version": ver.get("version") if isinstance(ver, dict) else None,
        }
    except Exception as e:
        return {"ok": False, "url": KOBOLD_URL, "error": str(e)}


def _r(v: Any, digits: int = 1) -> str:
    if v is None:
        return "n/a"
    try:
        return f"{float(v):.{digits}f}"
    except (TypeError, ValueError):
        return str(v)


def _mood_from_summary(summ: dict, goal: Optional[float]) -> str:
    trend = summ.get("trend")
    rate = summ.get("rate_lb_per_day")
    count = summ.get("count") or 0
    if not count or trend is None:
        return "idle"
    if goal is not None and abs(float(trend) - float(goal)) < 0.15:
        return "goal"
    if goal is not None and float(trend) <= float(goal):
        return "goal"
    if rate is None:
        return "steady"
    r = float(rate)
    if r <= -0.15:
        return "crushing"
    if r <= -0.03:
        return "losing"
    if r >= 0.05:
        return "gaining"
    return "steady"


def _rate_descriptor(lb_per_week: Optional[float]) -> str:
    if lb_per_week is None:
        return "unknown"
    w = float(lb_per_week)
    aw = abs(w)
    if aw < 0.15:
        pace = "essentially flat"
    elif aw < 0.5:
        pace = "gentle"
    elif aw < 1.0:
        pace = "solid"
    elif aw < 2.0:
        pace = "fast"
    else:
        pace = "very aggressive"
    direction = "loss" if w < 0 else "gain" if w > 0 else "flat"
    if direction == "flat":
        return pace
    return f"{pace} {direction}"


def _energy_phrase(kcal_per_day: Optional[float]) -> str:
    if kcal_per_day is None:
        return "energy balance unknown"
    k = float(kcal_per_day)
    if k <= -50:
        return f"estimated ~{abs(round(k))} kcal/day DEFICIT (body using stored energy)"
    if k >= 50:
        return f"estimated ~{round(k)} kcal/day SURPLUS (body storing energy)"
    return "estimated near energy BALANCE (~0 kcal/day)"


def _confidence(series: list, summ: dict) -> tuple[str, list[str]]:
    """How much to trust the slope given sample count and gaps."""
    notes: list[str] = []
    count = int(summ.get("count") or 0)
    span = float(summ.get("span_days") or 0)
    max_gap = 0.0
    gaps = []
    for e in series:
        g = e.get("gap_days")
        if g is not None:
            gaps.append(float(g))
            if float(g) > max_gap:
                max_gap = float(g)

    if count < 2:
        return "very low", ["Only one weigh-in — no real trend yet. Need more samples."]
    if count < 4:
        notes.append(f"Only {count} weigh-ins — early signal, treat rate as provisional.")
    elif count < 7:
        notes.append(f"{count} weigh-ins — trend forming but still noisy.")
    else:
        notes.append(f"{count} weigh-ins over {span:.0f} days — trend is usable.")

    if max_gap >= 7:
        notes.append(
            f"Largest gap between weigh-ins was {max_gap:.0f} days — "
            "half-life EMA handled it, but a big jump after a long gap can "
            "inflate the apparent rate until more dense samples arrive."
        )
    if span < 14 and count >= 2:
        notes.append("Tracking window is under 2 weeks — don't overfit day-to-day noise.")

    if count < 4 or max_gap >= 14:
        conf = "low"
    elif count < 7 or max_gap >= 7:
        conf = "moderate"
    else:
        conf = "good"
    return conf, notes


def style_system_blurb(style: str) -> str:
    """Hard style contract for the system message (xAI) / callers."""
    return {
        "pep": (
            "STYLE=pep. Sound like a witty nerdy gym buddy / sysadmin who lifts. "
            "Encourage with light humor; zero shame; concrete and useful."
        ),
        "roast": (
            "STYLE=roast. Affectionate roast of the user's habits/numbers — teasing, "
            "never cruel or body-shaming. Still give one useful nudge. "
            "MSG should clearly sound like a roast, not generic cheerleading."
        ),
        "haiku": (
            "STYLE=haiku. MSG must be exactly one haiku written as three short phrases "
            "separated by ' / ' (5-7-5-ish syllables). Still fill TITLE/TOAST/BADGE."
        ),
        "brief": (
            "STYLE=brief. Ultra-compressed: TITLE ≤6 words; MSG exactly one short sentence "
            "(≤18 words); TOAST ≤3 words. No fluff."
        ),
    }.get(style, "STYLE=pep. Witty, nerdy, encouraging.")


def _style_guide_block(style: str) -> str:
    """Detailed voice + output shape rules embedded in the user prompt."""
    return {
        "pep": (
            "VOICE: witty, nerdy, encouraging — sysadmin who lifts. Light humor, zero shame.\n"
            "SHAPE: TITLE max 8 words; MSG 2 short sentences, max 55 words; TOAST max 5 words; "
            "BADGE 2-4 words ALL CAPS."
        ),
        "roast": (
            "VOICE: affectionate roast — tease the scale bounce, the long gap, the surplus, "
            "or the excuse. Never cruel, never body-shame. End with one useful nudge.\n"
            "SHAPE: TITLE max 8 words (can be snarky); MSG 2 sentences that clearly roast then "
            "help (max 55 words); TOAST max 5 words; BADGE 2-4 words ALL CAPS.\n"
            "MUST: If it could pass as generic pep talk, rewrite until the roast is obvious."
        ),
        "haiku": (
            "VOICE: calm, image-forward, spare.\n"
            "SHAPE: TITLE max 6 words; MSG MUST be exactly three short phrases separated by "
            "' / ' (haiku-ish 5/7/5); TOAST max 4 words; BADGE 2-4 words ALL CAPS.\n"
            "Example MSG: 'Trend line falling / deficit doing the work / trust the next weigh-in'\n"
            "MUST: Do NOT write prose paragraphs for MSG."
        ),
        "brief": (
            "VOICE: clipped, factual, no fluff.\n"
            "SHAPE: TITLE ≤6 words; MSG exactly ONE sentence ≤18 words; TOAST ≤3 words; "
            "BADGE 2-3 words ALL CAPS.\n"
            "MUST: Shorter than pep. No jokes, no metaphor stacks."
        ),
    }.get(
        style,
        "VOICE: witty, nerdy, encouraging.\n"
        "SHAPE: TITLE max 8 words; MSG 2 short sentences; TOAST max 5; BADGE 2-4 ALL CAPS.",
    )


def build_context(
    series: list,
    summ: dict,
    settings: dict,
    photos: Optional[list] = None,
) -> dict:
    goal = settings.get("goal_weight")
    try:
        goal_f = float(goal) if goal not in (None, "") else None
    except (TypeError, ValueError):
        goal_f = None

    coach_goals = str(settings.get("coach_goals") or "").strip()

    mood = _mood_from_summary(summ, goal_f)
    recent = series[-8:] if series else []
    recent_lines = []
    for e in recent:
        bf = e.get("body_fat")
        bf_t = e.get("body_fat_trend")
        bf_bit = ""
        if bf is not None or bf_t is not None:
            bf_bit = (
                f", bf={_r(bf, 1)}%"
                + (f", bf_trend={_r(bf_t, 1)}%" if bf_t is not None else "")
            )
        recent_lines.append(
            f"  {e.get('date')}: scale={_r(e.get('weight'), 1)} lb, "
            f"trend={_r(e.get('trend'), 2)} lb{bf_bit}, "
            f"days_since_prev={_r(e.get('gap_days'), 0)}, "
            f"alpha={_r(e.get('alpha'), 2)}"
        )

    # Photo rating history (last ~8 analyzed), chronological
    photo_rows = list(photos or [])[-8:]
    photo_lines: list[str] = []
    for p in photo_rows:
        score = p.get("appearance_score")
        bmi_p = p.get("bmi_point")
        just = (p.get("appearance_justification") or "").strip()
        just_bit = f' — "{just[:120]}"' if just else ""
        photo_lines.append(
            f"  {p.get('date')}: appearance={_r(score, 1)}/10, "
            f"visual_bmi={_r(bmi_p, 1)}{just_bit}"
        )

    lb_left = None
    eta = None
    progress_pct = None
    if goal_f is not None and summ.get("trend") is not None:
        lb_left = float(summ["trend"]) - goal_f
        rate = summ.get("rate_lb_per_day")
        if rate and abs(float(rate)) > 1e-6:
            need = goal_f - float(summ["trend"])
            if (need < 0 and float(rate) < 0) or (need > 0 and float(rate) > 0):
                eta = abs(need / float(rate))
        if series:
            start = series[0].get("trend")
            if start is None:
                start = series[0].get("weight")
            if start is not None:
                span = float(start) - goal_f
                if abs(span) > 0.05:
                    progress_pct = max(0.0, min(100.0, ((float(start) - float(summ["trend"])) / span) * 100.0))

    conf, conf_notes = _confidence(series, summ)
    rate_w = summ.get("rate_lb_per_week")
    kcal = summ.get("kcal_per_day")
    net = summ.get("net_trend_change")  # last - first; negative => lost

    # Pre-chewed narrative facts (model should paraphrase these, not reinvent)
    narrative: list[str] = []
    count = int(summ.get("count") or 0)
    if count == 0:
        narrative.append("No weigh-ins yet. Invite the user to log a morning weight.")
    elif count == 1:
        narrative.append(
            f"First sample logged: scale {_r(summ.get('latest_weight'), 1)} lb on "
            f"{summ.get('latest_date')}. Trend equals that sample. No slope yet."
        )
    else:
        direction = "losing" if (rate_w is not None and float(rate_w) < -0.1) else (
            "gaining" if (rate_w is not None and float(rate_w) > 0.1) else "holding steady"
        )
        narrative.append(
            f"User is currently {direction} on the EMA trend line "
            f"(trend weight {_r(summ.get('trend'), 1)} lb; last scale "
            f"{_r(summ.get('latest_weight'), 1)} lb on {summ.get('latest_date')})."
        )
        narrative.append(
            f"Smoothed rate: {_r(rate_w, 2)} lb/week ({_rate_descriptor(rate_w)}). "
            f"That is {_r(summ.get('rate_lb_per_day'), 3)} lb/day."
        )
        # Critical: kcal is DERIVED from rate — same signal
        narrative.append(
            f"Estimated energy balance from that SAME slope: {_energy_phrase(kcal)}. "
            "Important: the kcal figure is computed from the weight trend "
            "(rate × 3500). It is NOT an independent measurement. "
            "Deficit and weight loss are the same story — never contrast them with "
            "'despite', 'even though', or 'still'."
        )
        if net is not None:
            if float(net) < -0.2:
                narrative.append(
                    f"Since the first sample, trend is down {_r(abs(float(net)), 1)} lb "
                    f"over {_r(summ.get('span_days'), 0)} days."
                )
            elif float(net) > 0.2:
                narrative.append(
                    f"Since the first sample, trend is up {_r(float(net), 1)} lb "
                    f"over {_r(summ.get('span_days'), 0)} days."
                )
            else:
                narrative.append(
                    f"Net trend change since start is about flat over "
                    f"{_r(summ.get('span_days'), 0)} days."
                )

    if goal_f is not None and summ.get("trend") is not None and count:
        if lb_left is not None and lb_left > 0.15:
            narrative.append(
                f"Goal is {goal_f:.0f} lb. Still about {_r(lb_left, 1)} lb above goal "
                f"on the trend"
                + (
                    f"; at the current rate that is roughly {round(eta)} days away."
                    if eta is not None and eta < 800
                    else "."
                )
            )
        elif lb_left is not None and lb_left < -0.15:
            narrative.append(
                f"Goal is {goal_f:.0f} lb. Trend is about {_r(abs(lb_left), 1)} lb below goal."
            )
        else:
            narrative.append(f"Trend is at the goal weight ({goal_f:.0f} lb). Maintain.")
        if progress_pct is not None:
            narrative.append(f"Progress from start toward goal: ~{progress_pct:.0f}%.")
    elif goal_f is None:
        narrative.append("No goal weight set yet.")

    # Body-fat signal (scale) — same EMA family as weight when present
    latest_bf = summ.get("latest_body_fat")
    bf_trend = summ.get("body_fat_trend")
    if latest_bf is not None or bf_trend is not None:
        narrative.append(
            f"Scale body fat: last {_r(latest_bf, 1)}%, "
            f"EMA bf trend {_r(bf_trend, 1)}%."
        )
    else:
        narrative.append("No scale body-fat readings yet.")

    if coach_goals:
        narrative.append(
            f"User's stated focus/goals for the coach (honor this): {coach_goals}"
        )
    else:
        narrative.append(
            "No free-form coach goals note set — lean on goal weight + trend + photos."
        )

    if photo_lines:
        scores = [
            float(p["appearance_score"])
            for p in photo_rows
            if p.get("appearance_score") is not None
        ]
        if scores:
            first_s, last_s = scores[0], scores[-1]
            delta = last_s - first_s
            if abs(delta) < 0.3:
                trend_word = "steady"
            elif delta > 0:
                trend_word = "improving"
            else:
                trend_word = "softening"
            narrative.append(
                f"Photo appearance ratings (1–10): {len(scores)} recent analyzed photos; "
                f"latest {_r(last_s, 1)}, earlier {_r(first_s, 1)} "
                f"({trend_word} over that window)."
            )
        else:
            narrative.append(
                f"{len(photo_lines)} recent photos have visual BMI but no appearance score."
            )
    else:
        narrative.append("No analyzed progress photos yet.")

    for n in conf_notes:
        narrative.append(f"Caveat: {n}")

    # Suggested tone tags for the model
    if mood == "crushing":
        tone = "Celebrate a strong deficit/loss. Don't call it slow. Optionally note confidence caveats."
    elif mood == "losing":
        tone = "Encourage steady progress. Trust the trend over daily scale bounce."
    elif mood == "gaining":
        tone = "Calm course-correction, no shame. Small intake or activity nudge."
    elif mood == "goal":
        tone = "Congratulate. Shift to maintenance language."
    elif mood == "steady":
        tone = "Maintenance/equilibrium. Ask if they want to open a deficit or hold."
    else:
        tone = "Warm onboarding. Get the first few weigh-ins logged."

    if coach_goals:
        tone = f"{tone} Weave in the user's stated focus: {coach_goals}."

    return {
        "mood": mood,
        "tone": tone,
        "confidence": conf,
        "count": count,
        "latest_date": summ.get("latest_date"),
        "latest_weight": summ.get("latest_weight"),
        "latest_body_fat": latest_bf,
        "body_fat_trend": bf_trend,
        "trend": summ.get("trend"),
        "rate_lb_per_week": rate_w,
        "rate_lb_per_day": summ.get("rate_lb_per_day"),
        "kcal_per_day": kcal,
        "half_life_days": summ.get("half_life_days") or settings.get("half_life_days"),
        "goal_weight": goal_f,
        "coach_goals": coach_goals or None,
        "lb_to_goal": lb_left,
        "eta_days": eta,
        "progress_pct": progress_pct,
        "span_days": summ.get("span_days"),
        "net_trend_change": net,
        "rate_descriptor": _rate_descriptor(rate_w if rate_w is not None else None),
        "energy_phrase": _energy_phrase(kcal if kcal is not None else None),
        "recent": recent_lines,
        "photo_lines": photo_lines,
        "narrative": narrative,
        "first_weight": series[0].get("weight") if series else None,
        "first_date": series[0].get("date") if series else None,
    }


def build_prompt(ctx: dict, style: str = "pep") -> str:
    """Build a prompt that forces paraphrase of a pre-digested briefing."""
    recent = "\n".join(ctx.get("recent") or []) or "  (none yet)"
    photos = "\n".join(ctx.get("photo_lines") or []) or "  (none yet)"
    narrative = "\n".join(f"- {line}" for line in (ctx.get("narrative") or []))
    mood = ctx.get("mood", "idle")
    style_key = style if style in ("pep", "roast", "haiku", "brief") else "pep"
    style_guide = _style_guide_block(style_key)
    coach_goals = ctx.get("coach_goals") or "(none set)"

    # Explicit allowed number bank so the model can only quote these
    number_bank = (
        f"trend_lb={_r(ctx.get('trend'), 1)}, "
        f"last_scale_lb={_r(ctx.get('latest_weight'), 1)}, "
        f"last_bf_pct={_r(ctx.get('latest_body_fat'), 1)}, "
        f"bf_trend_pct={_r(ctx.get('body_fat_trend'), 1)}, "
        f"lb_per_week={_r(ctx.get('rate_lb_per_week'), 2)}, "
        f"kcal_per_day={_r(ctx.get('kcal_per_day'), 0)}, "
        f"goal_lb={_r(ctx.get('goal_weight'), 0)}, "
        f"lb_above_goal={_r(ctx.get('lb_to_goal'), 1)}, "
        f"eta_days={_r(ctx.get('eta_days'), 0)}, "
        f"weigh_ins={ctx.get('count')}, "
        f"span_days={_r(ctx.get('span_days'), 0)}, "
        f"net_trend_change_lb={_r(ctx.get('net_trend_change'), 1)}, "
        f"progress_pct={_r(ctx.get('progress_pct'), 0)}, "
        f"start_scale_lb={_r(ctx.get('first_weight'), 1)}"
    )

    output_shape = {
        "pep": (
            "TITLE: <max 8 words, punchy, accurate>\n"
            "MSG: <2 short sentences, max 55 words; must agree with BRIEFING>\n"
            "TOAST: <max 5 words>\n"
            "BADGE: <2-4 words ALL CAPS>"
        ),
        "roast": (
            "TITLE: <max 8 words, snarky but kind>\n"
            "MSG: <2 sentences: roast then useful nudge; max 55 words; must agree with BRIEFING>\n"
            "TOAST: <max 5 words>\n"
            "BADGE: <2-4 words ALL CAPS>"
        ),
        "haiku": (
            "TITLE: <max 6 words>\n"
            "MSG: <exactly three short phrases separated by ' / '; haiku-ish; NO prose paragraphs>\n"
            "TOAST: <max 4 words>\n"
            "BADGE: <2-4 words ALL CAPS>"
        ),
        "brief": (
            "TITLE: <≤6 words>\n"
            "MSG: <exactly ONE sentence, ≤18 words>\n"
            "TOAST: <≤3 words>\n"
            "BADGE: <2-3 words ALL CAPS>"
        ),
    }[style_key]

    return f"""### Instruction:
You write coach copy for τrend, a personal weight-trend tracker app.

How the app works (read carefully):
- User logs scale weight (noisy). App fits a time-aware EMA trend.
- Rate (lb/week) is the slope of that trend.
- kcal/day is rate_lb_per_day × 3500. So kcal and rate are ONE signal, not two.
- Negative rate + negative kcal = losing weight / deficit. That is consistent and good if intended.
- NEVER say "despite a deficit" or "even though you have a deficit" about weight loss. Deficit explains the loss.
- Do not call a ~1–2 lb/week loss "slow" — that is a fast/solid pace. Use the pace words from the briefing.
- Scale body fat % (when present) is a second signal; photo appearance scores (1–10) are a third.
- Honor the user's stated focus/goals when present (e.g. visible abs, general fitness, cut, recomp).

Your job: rephrase the BRIEFING into the output format for the chosen STYLE. Do not invent new numbers or medical claims.

STYLE CONTRACT (mandatory — output must obviously match this style):
{style_guide}

Tone hint: {ctx.get('tone')}
Mood bucket: {mood}
Trend confidence: {ctx.get('confidence')}
User focus/goals note: {coach_goals}

BRIEFING (authoritative — paraphrase, don't contradict):
{narrative}

Allowed numbers only (do not invent others):
{number_bank}

Weigh-in log (most recent last; includes bf when logged):
{recent}

Photo rating history (most recent last; appearance 1–10 + visual BMI):
{photos}

Output EXACTLY four lines and stop:
{output_shape}

### Response:
TITLE:"""


def parse_coach_output(text: str) -> dict:
    """Parse TITLE/MSG/TOAST/BADGE lines from model output."""
    raw = (text or "").strip()
    raw = re.split(
        r"(?im)\n(?:BRIEFING|Allowed numbers|Weigh-in log|### Instruction|How the app|Output EXACTLY|Data:|FACTS\b)",
        raw,
        maxsplit=1,
    )[0].strip()

    if not re.match(r"(?i)^TITLE\s*:", raw):
        raw = "TITLE: " + raw

    fields: dict[str, str] = {}
    current = None
    for line in raw.splitlines():
        m = re.match(r"^(TITLE|MSG|TOAST|BADGE)\s*:\s*(.*)$", line.strip(), re.I)
        if m:
            current = m.group(1).upper()
            fields[current] = m.group(2).strip()
            continue
        if current in ("MSG", "TITLE") and line.strip():
            fields[current] = (fields.get(current, "") + " " + line.strip()).strip()

    title = fields.get("TITLE")
    msg = fields.get("MSG")
    toast = fields.get("TOAST")
    badge = fields.get("BADGE")

    if not title or not msg:
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        cleaned = [
            re.sub(r"^(TITLE|MSG|TOAST|BADGE)\s*:\s*", "", ln, flags=re.I) for ln in lines
        ]
        cleaned = [c for c in cleaned if c]
        if not title and cleaned:
            title = cleaned[0][:80]
        if not msg and len(cleaned) > 1:
            msg = " ".join(cleaned[1:3])[:400]
        elif not msg:
            msg = raw[:400] if raw else "Keep logging. Trust the trend."

    def clean(s: Optional[str], n: int) -> Optional[str]:
        if not s:
            return s
        s = re.sub(r"\s+", " ", s).strip().strip('"').strip("'")
        s = re.split(r"\b(?:BRIEFING|Allowed numbers|mood_bucket|FACTS)\b", s, maxsplit=1)[0].strip()
        return s[:n]

    title = clean(title, 80)
    msg = clean(msg, 400)
    toast = clean(toast, 40)
    badge = clean(badge, 28)
    if badge:
        badge = re.sub(r"[^A-Za-z0-9 \-]", "", badge).strip().upper()
        badge = " ".join(badge.split()[:4])

    # Light post-filter for the classic failure mode
    if msg and re.search(r"(?i)despite.{0,40}deficit", msg):
        msg = re.sub(
            r"(?i)despite\s+(a\s+|an\s+|your\s+|the\s+)?(estimated\s+|daily\s+)?(calorie\s+|kcal\s+)?deficit[^,.]*[,.]?\s*",
            "With that deficit working for you, ",
            msg,
            count=1,
        )

    return {
        "title": title or "Signal received",
        "message": msg or "Keep logging. Trust the trend.",
        "toast": toast or "✦ LOGGED",
        "badge": badge or "COACH SAYS",
        "raw": text,
    }


def generate_pep(
    series: list,
    summ: dict,
    settings: dict,
    style: str = "pep",
) -> dict:
    """Call Kobold and return structured coach copy + meta."""
    ctx = build_context(series, summ, settings)
    prompt = build_prompt(ctx, style=style)

    payload = {
        "prompt": prompt,
        "max_length": 150,
        "max_context_length": 4096,
        "temperature": 0.7,  # lower: less "creative physics"
        "top_p": 0.85,
        "top_k": 30,
        "rep_pen": 1.08,
        "rep_pen_range": 256,
        "stop_sequence": [
            "### Instruction",
            "### Instruction:",
            "\nBRIEFING",
            "\nAllowed numbers",
            "\nWeigh-in log",
            "\nHow the app",
            "\n\n\n",
        ],
    }

    try:
        result = _http_json(
            "POST",
            f"{KOBOLD_URL}/api/v1/generate",
            payload,
            timeout=KOBOLD_TIMEOUT,
        )
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"Kobold HTTP {e.code}: {err_body[:200]}") from e
    except Exception as e:
        raise RuntimeError(f"Kobold unreachable at {KOBOLD_URL}: {e}") from e

    results = result.get("results") if isinstance(result, dict) else None
    if not results:
        raise RuntimeError(f"Unexpected Kobold response: {str(result)[:200]}")

    text = results[0].get("text") or ""
    parsed = parse_coach_output(text)
    parsed["mood"] = ctx["mood"]
    parsed["style"] = style
    parsed["confidence"] = ctx.get("confidence")
    parsed["briefing"] = ctx.get("narrative")
    parsed["model"] = None
    try:
        st = kobold_status()
        parsed["model"] = st.get("model")
    except Exception:
        pass
    parsed["context"] = {
        "trend": ctx.get("trend"),
        "rate_lb_per_week": ctx.get("rate_lb_per_week"),
        "kcal_per_day": ctx.get("kcal_per_day"),
        "goal_weight": ctx.get("goal_weight"),
        "count": ctx.get("count"),
        "confidence": ctx.get("confidence"),
        "rate_descriptor": ctx.get("rate_descriptor"),
        "energy_phrase": ctx.get("energy_phrase"),
    }
    return parsed
