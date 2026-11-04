"""Segmentation quality control: backend statistics first, then a configured AI vision review.

This is AI-assisted QC. It is NOT a guarantee that a mask is accurate, and the UI/report must say so.
A mask is only `accepted` when the statistics gate passes AND the vision review says it is acceptable;
any doubt becomes a retry with an alternate prompt, and after the retry limit a `flagged` result that
forces human review.
"""
from __future__ import annotations

import json
import re
from typing import Any

import numpy as np
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.services.meta_client import MetaClient

ALT_PROMPTS: dict[str, list[str]] = {
    "trees": ["vegetation", "tree canopy"],
    "vegetation": ["trees", "green vegetation"],
    "buildings": ["roofs", "houses"],
}

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "acceptable": {"type": "boolean"},
        "issues": {"type": "array", "items": {"type": "string"}},
        "suggested_prompt": {"type": ["string", "null"]},
    },
    "required": ["acceptable", "issues", "suggested_prompt"],
    "additionalProperties": False,
}

MAX_ISSUES = 5
_PLAIN_PHRASE = re.compile(r"^[A-Za-z][A-Za-z \-]{0,38}$")


class MaskStats(BaseModel):
    pixel_count: int
    coverage: float
    empty: bool
    suspicious: bool
    reasons: list[str] = Field(default_factory=list)


class QCDecision(BaseModel):
    status: str  # accepted | retry | flagged
    issues: list[str]
    suggested_prompt: str | None = None
    attempt: int
    prompt: str
    coverage: float
    stats_suspicious: bool
    vision_acceptable: bool | None = None


def mask_stats(mask: np.ndarray, category: str, settings: Settings | None = None) -> MaskStats:
    s = settings or get_settings()
    m = np.asarray(mask).astype(bool)
    count = int(np.count_nonzero(m))
    coverage = count / m.size if m.size else 0.0
    lo, hi = s.qc_bounds.get(category, s.qc_bounds_default)
    if count == 0:
        return MaskStats(pixel_count=0, coverage=0.0, empty=True, suspicious=True,
                         reasons=["Mask is empty: nothing was detected for this prompt."])
    if coverage < lo:
        return MaskStats(pixel_count=count, coverage=coverage, empty=False, suspicious=True,
                         reasons=[f"Coverage {coverage:.2%} is below the expected minimum {lo:.2%} for {category}."])
    if coverage > hi:
        return MaskStats(pixel_count=count, coverage=coverage, empty=False, suspicious=True,
                         reasons=[f"Coverage {coverage:.2%} is above the expected maximum {hi:.2%} for {category}."])
    return MaskStats(pixel_count=count, coverage=coverage, empty=False, suspicious=False)


def next_prompt(category: str, current: str, suggested: str | None = None) -> str | None:
    """The next alternate SAM prompt to try, or None when none remain.

    A model-suggested prompt is accepted only if it is a short plain phrase (so a model cannot smuggle
    instructions into the SAM prompt), different from the current prompt, and not the original category.
    """
    cur = current.strip().lower()
    if suggested:
        cand = suggested.strip().lower()
        if _PLAIN_PHRASE.match(cand) and cand not in (cur, category):
            return cand
    sequence = [category] + ALT_PROMPTS.get(category, [])
    if cur in sequence:
        i = sequence.index(cur)
        return sequence[i + 1] if i + 1 < len(sequence) else None
    return next((p for p in ALT_PROMPTS.get(category, []) if p != cur), None)


def _parse_json(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I)
    try:
        data = json.loads(t)
    except ValueError:
        m = re.search(r"\{.*\}", t, flags=re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def vision_review(
    meta: MetaClient, model: str, scene_png: bytes, overlay_png: bytes, category: str, prompt: str
) -> tuple[bool | None, list[str], str | None]:
    """Ask the vision model whether the tinted region plausibly covers `category`.

    Returns (acceptable, issues, suggested_prompt). An unparseable answer counts as NOT acceptable,
    so a broken review can never silently pass QC.
    """
    question = (
        f"An automatic segmentation model was prompted with '{prompt}' to find '{category}'. "
        "Image 1 is the original scene; image 2 is the same scene with the detected region tinted. "
        "Judge whether the tinted region plausibly covers the target without large obvious errors "
        "(large missed areas, or large tinted areas that are clearly something else). "
        'Respond ONLY with JSON: {"acceptable": true|false, "issues": [short strings], '
        '"suggested_prompt": a short plain phrase or null}.'
    )
    resp = meta.chat(
        [
            {"role": "system", "content": "You are a careful remote-sensing image reviewer. You never guess measurements."},
            {"role": "user", "content": question},
        ],
        model=model,
        images=[scene_png, overlay_png],
        json_schema={"name": "mask_review", "schema": VISION_SCHEMA, "strict": True},
    )
    data = _parse_json(resp.content)
    if data is None or not isinstance(data.get("acceptable"), bool):
        return False, ["The vision review returned an unparseable answer."], None
    issues = [str(i) for i in (data.get("issues") or [])][:MAX_ISSUES]
    suggested = data.get("suggested_prompt")
    return data["acceptable"], issues, suggested if isinstance(suggested, str) and suggested.strip() else None


def decide(
    stats: MaskStats,
    vision_ok: bool | None,
    issues: list[str],
    suggested: str | None,
    category: str,
    prompt: str,
    attempt: int,
    max_retries: int,
) -> QCDecision:
    all_issues = list(dict.fromkeys([*stats.reasons, *issues]))[:MAX_ISSUES + 3]
    base = dict(attempt=attempt, prompt=prompt, coverage=stats.coverage,
                stats_suspicious=stats.suspicious, vision_acceptable=vision_ok)
    if not stats.suspicious and vision_ok is True:
        return QCDecision(status="accepted", issues=all_issues, **base)
    alt = next_prompt(category, prompt, suggested)
    if attempt - 1 < max_retries and alt is not None:
        return QCDecision(status="retry", issues=all_issues, suggested_prompt=alt, **base)
    return QCDecision(status="flagged", issues=all_issues, **base)
