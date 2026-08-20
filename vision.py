"""Progress-photo analysis via xAI Grok (vision).

Uses the same api.x.ai chat/completions path as the other local webapps
(GROK_API_KEY / XAI_API_KEY). Returns structured JSON for BMI estimate +
appearance rating.
"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple

ROOT = Path(__file__).resolve().parent

def subject_phrase(sex: Optional[str] = None, age: Optional[int] = None) -> str:
    """Human-readable subject for prompts, e.g. 'a 44-year-old man'."""
    sex_n = (sex or "").strip().lower()
    if sex_n in ("m", "male", "man"):
        noun = "man"
    elif sex_n in ("f", "female", "woman"):
        noun = "woman"
    else:
        noun = "adult"
    if age is not None:
        try:
            a = int(age)
            if 5 <= a <= 120:
                article = "an" if noun == "adult" else "a"
                return f"{article} {a}-year-old {noun}"
        except (TypeError, ValueError):
            pass
    if noun == "adult":
        return "an adult"
    return f"an adult {noun}"


def subject_possessive(sex: Optional[str] = None) -> str:
    sex_n = (sex or "").strip().lower()
    if sex_n in ("m", "male", "man"):
        return "his"
    if sex_n in ("f", "female", "woman"):
        return "her"
    return "their"


def build_analysis_prompt(
    *, sex: Optional[str] = None, age: Optional[int] = None
) -> str:
    subject = subject_phrase(sex, age)
    return f"""You are a fitness progress photo analyzer for a personal diet tracker app.

When given a photo of a person, analyze their physical appearance and return a structured assessment.
The subject is {subject} — judge appearance and body composition with realistic standards for that demographic.

Rules:
- Be honest and consistent across photos so trends over time are meaningful.
- Account for clothing, lighting, camera angle, and pose — note when these factors reduce confidence.
- Separate overall appearance from pure body-composition assessment. Clothing, grooming, posture, and facial structure all matter.
- Do not be overly flattering or harsh. Aim for realistic standards for {subject}.
- Always respond with valid JSON only. No extra text before or after the JSON.

Output this exact JSON structure:

{{
  "bmi_estimate": {{
    "point": 29.5,
    "range_low": 28.0,
    "range_high": 31.0,
    "confidence": "medium"
  }},
  "appearance_rating": {{
    "score": 6.0,
    "scale": "1-10",
    "justification": "Short 1-2 sentence explanation of the score."
  }},
  "observations": {{
    "face_softness": "moderate",
    "midsection": "noticeable soft tissue under shirt",
    "overall_build": "solid / soft overweight",
    "notes": "Any relevant notes about lighting, clothing, angle, or other factors affecting the assessment."
  }},
  "confidence_overall": "medium"
}}"""


# Back-compat default (no demographics) — prefer build_analysis_prompt(...)
ANALYSIS_PROMPT = build_analysis_prompt()


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip("'").strip('"')
        if k and k not in os.environ:
            os.environ[k] = v


def load_xai_credentials() -> Tuple[Optional[str], str]:
    """Return (api_key, model). Searches env + local secrets files + todo secrets.php."""
    for p in (
        ROOT / "secrets.env",
        ROOT / ".env",
        ROOT / "data" / "secrets.env",
        Path.home() / ".secrets.env",
    ):
        _load_env_file(p)

    # PHP define from sibling webapps
    for php in (
        ROOT / "secrets.php",
        Path.home() / "AIML/claude/todo/secrets.php",
        Path("/shared/html/todo/secrets.php"),
    ):
        if not php.is_file():
            continue
        text = php.read_text(errors="replace")
        m = re.search(r"define\(\s*'GROK_API_KEY'\s*,\s*'([^']+)'\s*\)", text)
        if m and m.group(1) and "REPLACE" not in m.group(1):
            os.environ.setdefault("XAI_API_KEY", m.group(1))
            os.environ.setdefault("GROK_API_KEY", m.group(1))
        m2 = re.search(r"define\(\s*'GROK_MODEL'\s*,\s*'([^']+)'\s*\)", text)
        if m2:
            os.environ.setdefault("XAI_MODEL", m2.group(1))

    key = os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY")
    model = os.environ.get("XAI_MODEL") or os.environ.get("GROK_MODEL") or "grok-4.6"
    if key in (None, "", "xai-REPLACE_ME"):
        return None, model
    return key, model


def xai_status() -> dict:
    key, model = load_xai_credentials()
    return {
        "ok": bool(key),
        "model": model,
        "configured": bool(key),
        "base_url": "https://api.x.ai/v1",
    }


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


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def _normalize(analysis: dict) -> dict:
    bmi = analysis.get("bmi_estimate") or {}
    app = analysis.get("appearance_rating") or {}
    obs = analysis.get("observations") or {}
    return {
        "bmi_estimate": {
            "point": float(bmi.get("point")) if bmi.get("point") is not None else None,
            "range_low": float(bmi["range_low"]) if bmi.get("range_low") is not None else None,
            "range_high": float(bmi["range_high"]) if bmi.get("range_high") is not None else None,
            "confidence": bmi.get("confidence"),
        },
        "appearance_rating": {
            "score": float(app.get("score")) if app.get("score") is not None else None,
            "scale": app.get("scale") or "1-10",
            "justification": app.get("justification") or "",
        },
        "observations": {
            "face_softness": obs.get("face_softness"),
            "midsection": obs.get("midsection"),
            "overall_build": obs.get("overall_build"),
            "notes": obs.get("notes"),
        },
        "confidence_overall": analysis.get("confidence_overall"),
    }


def analyze_image_bytes(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    *,
    sex: Optional[str] = None,
    age: Optional[int] = None,
) -> dict:
    """Call Grok vision and return normalized analysis + raw meta."""
    api_key, model = load_xai_credentials()
    if not api_key:
        raise RuntimeError(
            "No XAI/GROK API key configured. Set XAI_API_KEY or create secrets.env"
        )

    if mime not in ("image/jpeg", "image/jpg", "image/png"):
        # convert-ish: still send; API prefers jpeg/png
        if mime == "image/webp":
            raise RuntimeError("webp not supported by xAI vision — use jpeg or png")
        mime = "image/jpeg"

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    system_prompt = build_analysis_prompt(sex=sex, age=age)

    # Chat Completions style (matches other local apps) with image_url content parts
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url, "detail": "high"}},
                    {
                        "type": "text",
                        "text": "Analyze this progress photo and return JSON only.",
                    },
                ],
            },
        ],
    }

    try:
        result = _http_json(
            "https://api.x.ai/v1/chat/completions",
            payload,
            api_key,
            timeout=float(os.environ.get("XAI_TIMEOUT", "180")),
        )
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        raise RuntimeError(f"xAI HTTP {e.code}: {body[:400]}") from e

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"Unexpected xAI response: {str(result)[:300]}")
    text = (choices[0].get("message") or {}).get("content") or ""
    parsed = _normalize(_extract_json(text))
    parsed["_meta"] = {
        "model": model,
        "raw_text": text,
        "usage": result.get("usage"),
    }
    return parsed


def analyze_image_file(
    path: Path,
    mime: Optional[str] = None,
    *,
    sex: Optional[str] = None,
    age: Optional[int] = None,
) -> dict:
    data = path.read_bytes()
    if mime is None:
        suf = path.suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}.get(
            suf, "image/jpeg"
        )
    return analyze_image_bytes(data, mime, sex=sex, age=age)
