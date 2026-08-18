"""Grok Imagine: project a progress photo to goal-weight appearance."""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple

from vision import load_xai_credentials

IMAGINE_MODEL = os.environ.get("XAI_IMAGINE_MODEL", "grok-imagine-image-2.0")


def build_goal_prompt(
    *,
    current_lb: Optional[float],
    goal_lb: float,
    current_bmi: Optional[float],
    goal_bmi: Optional[float],
    appearance_notes: Optional[str] = None,
) -> str:
    cur = f"{current_lb:.1f} lb" if current_lb is not None else "his current weight"
    cur_bmi = f" (approx BMI {current_bmi:.1f})" if current_bmi is not None else ""
    goal_bmi_s = f" (approx BMI {goal_bmi:.1f})" if goal_bmi is not None else ""
    extra = ""
    if appearance_notes:
        extra = f" Context from a prior assessment: {appearance_notes[:240]}"

    return (
        "Edit this realistic personal progress photo to show a believable projection "
        f"of the same person at a goal body weight of {goal_lb:.1f} lb{goal_bmi_s}, "
        f"down from {cur}{cur_bmi}. "
        "Keep the identical person identity, face structure, hair, clothing, pose, "
        "framing, background, and lighting. "
        "Realistically reduce soft tissue in the midsection, face, and overall build "
        "in proportion to that weight change for a mid-40s male — subtle and natural, "
        "not extreme, not bodybuilder, not plastic surgery. "
        "Photorealistic continuity with the source photo."
        f"{extra}"
    )


def _http_json(url: str, payload: dict, api_key: str, timeout: float = 180.0) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, timeout: float = 120.0) -> Tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": "hackers-diet/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        ctype = resp.headers.get("Content-Type") or "image/jpeg"
        mime = ctype.split(";")[0].strip()
        return data, mime


def edit_image_to_goal(
    image_bytes: bytes,
    mime: str,
    *,
    current_lb: Optional[float],
    goal_lb: float,
    current_bmi: Optional[float] = None,
    goal_bmi: Optional[float] = None,
    appearance_notes: Optional[str] = None,
) -> dict[str, Any]:
    """Call Imagine edits API; return {bytes, mime, prompt, model, url?}."""
    api_key, _chat_model = load_xai_credentials()
    if not api_key:
        raise RuntimeError("No XAI_API_KEY configured for Imagine")

    if mime not in ("image/jpeg", "image/jpg", "image/png"):
        mime = "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    prompt = build_goal_prompt(
        current_lb=current_lb,
        goal_lb=goal_lb,
        current_bmi=current_bmi,
        goal_bmi=goal_bmi,
        appearance_notes=appearance_notes,
    )

    payload = {
        "model": IMAGINE_MODEL,
        "prompt": prompt,
        "image": {"url": data_url, "type": "image_url"},
        "n": 1,
    }

    try:
        result = _http_json(
            "https://api.x.ai/v1/images/edits",
            payload,
            api_key,
            timeout=float(os.environ.get("XAI_IMAGINE_TIMEOUT", "180")),
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"Imagine HTTP {e.code}: {body[:500]}") from e

    data = result.get("data") or []
    if not data:
        raise RuntimeError(f"Unexpected Imagine response: {str(result)[:400]}")

    item = data[0]
    out_bytes: Optional[bytes] = None
    out_mime = "image/jpeg"
    url = item.get("url")
    if item.get("b64_json"):
        out_bytes = base64.b64decode(item["b64_json"])
    elif url:
        out_bytes, out_mime = _download(url)
    else:
        raise RuntimeError("Imagine returned no url or b64_json")

    return {
        "bytes": out_bytes,
        "mime": out_mime if out_mime.startswith("image/") else "image/jpeg",
        "prompt": prompt,
        "model": IMAGINE_MODEL,
        "url": url,
        "raw": {k: item.get(k) for k in ("url", "revised_prompt") if k in item},
    }
