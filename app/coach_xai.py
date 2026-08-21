"""Coach pep talks via xAI Grok (replaces Kobold for multi-user)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

from app.config import get_settings

# Reuse briefing/prompt builders from the original coach module
from coach import build_context, build_prompt, parse_coach_output, style_system_blurb  # noqa: E402


_STYLE_TEMPERATURE = {
    "pep": 0.85,
    "roast": 0.95,
    "haiku": 0.55,
    "brief": 0.45,
}


def generate_pep_xai(
    series: list,
    summ: dict,
    settings: dict,
    style: str = "pep",
    photos: Optional[list] = None,
) -> dict[str, Any]:
    cfg = get_settings()
    if not cfg.xai_api_key:
        raise RuntimeError("XAI_API_KEY not configured")

    ctx = build_context(series, summ, settings, photos=photos)
    prompt = build_prompt(ctx, style=style)
    style_key = style if style in _STYLE_TEMPERATURE else "pep"
    model = (cfg.xai_coach_model or cfg.xai_model or "grok-4.20-0309-non-reasoning").strip()

    payload = {
        "model": model,
        "temperature": _STYLE_TEMPERATURE[style_key],
        "max_tokens": 220,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the τrend weight coach. Follow the user instruction exactly. "
                    "Output only the TITLE/MSG/TOAST/BADGE fields as specified. "
                    f"{style_system_blurb(style_key)}"
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.x.ai/v1/chat/completions",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.xai_api_key}",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"xAI HTTP {e.code}: {body[:300]}") from e

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"Unexpected xAI response: {str(result)[:300]}")
    text = (choices[0].get("message") or {}).get("content") or ""

    parsed = parse_coach_output(text)
    parsed["mood"] = ctx["mood"]
    parsed["style"] = style
    parsed["confidence"] = ctx.get("confidence")
    parsed["briefing"] = ctx.get("narrative")
    parsed["model"] = model
    parsed["provider"] = "xai"
    parsed["context"] = {
        "trend": ctx.get("trend"),
        "rate_lb_per_week": ctx.get("rate_lb_per_week"),
        "kcal_per_day": ctx.get("kcal_per_day"),
        "goal_weight": ctx.get("goal_weight"),
        "count": ctx.get("count"),
        "confidence": ctx.get("confidence"),
        "rate_descriptor": ctx.get("rate_descriptor"),
        "energy_phrase": ctx.get("energy_phrase"),
        "coach_goals": ctx.get("coach_goals"),
        "body_fat_trend": ctx.get("body_fat_trend"),
        "latest_body_fat": ctx.get("latest_body_fat"),
        "photo_count": len(ctx.get("photo_lines") or []),
    }
    return parsed
