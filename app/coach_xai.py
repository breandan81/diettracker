"""Coach pep talks via xAI Grok (replaces Kobold for multi-user)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from app.config import get_settings

# Reuse briefing/prompt builders from the original coach module
from coach import build_context, build_prompt, parse_coach_output  # noqa: E402


def generate_pep_xai(
    series: list,
    summ: dict,
    settings: dict,
    style: str = "pep",
) -> dict[str, Any]:
    cfg = get_settings()
    if not cfg.xai_api_key:
        raise RuntimeError("XAI_API_KEY not configured")

    ctx = build_context(series, summ, settings)
    prompt = build_prompt(ctx, style=style)

    payload = {
        "model": cfg.xai_model,
        "temperature": 0.7,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are the τrend weight coach. Follow the user instruction exactly. "
                    "Output only the TITLE/MSG/TOAST/BADGE fields as specified."
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
        with urllib.request.urlopen(req, timeout=120) as resp:
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
    parsed["model"] = cfg.xai_model
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
    }
    return parsed
