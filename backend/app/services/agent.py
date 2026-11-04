"""AI investigation agent.

The configured chat model chooses which tool to call next; the backend executes real functions, enforces
prerequisites and limits in code, logs every call to `agent_steps`, and validates the final
interpretation. Models never produce a measurement: numbers come from comparison.py and are
rendered by the backend.

Failure semantics:
  failed           system error (provider unavailable, undecodable file, ...). No report.
  review_required  ran, but the pair/masks are not trustworthy (or the pair was rejected).
  completed        ran and passed every check.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import numpy as np
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import DomainError, MetaApiError
from app.db.models import (
    AgentStep, AnalysisRun, Image, InspectionTask, Investigation, SegmentationResult,
)
from app.services.comparison import (
    Georef, area_m2, compare_masks, diff_overlay, m2_supported,
)
from app.services import quality, report as report_service, segmentation, validation
from app.services.meta_client import MetaClient
from app.services.storage import Storage

log = logging.getLogger("greenwatch.agent")

ROLES = ("earlier", "newer")
FORBIDDEN_TERMS = (
    "illegal", "unlawful", "criminal", "violat", "fraud", "guilty", "offend", "trespass", "crime",
)


class ToolError(Exception):
    """A recoverable problem reported back to the model (prerequisite not met, bad argument)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- Tool argument models ------------------------------------------------------------------------

class NoArgs(BaseModel):
    pass


class SegmentArgs(BaseModel):
    role: Literal["earlier", "newer"]
    category: str
    prompt: str | None = Field(default=None, max_length=60)


class SegIdArgs(BaseModel):
    seg_id: str


class CategoryArgs(BaseModel):
    category: str


class InterpretationArgs(BaseModel):
    category: str
    summary: str = Field(max_length=700)
    change_direction: Literal["increase", "decrease", "unchanged", "undetermined"]
    evidence_ids: list[str]
    limitations: list[str]
    recommended_action: str = Field(max_length=400)
    requires_human_review: bool


class TaskArgs(BaseModel):
    title: str = Field(max_length=200)
    reason: str = Field(max_length=800)
    priority: Literal["low", "medium", "high"] = "medium"


# ---- Run context -----------------------------------------------------------------------------------

@dataclass
class RunContext:
    db: Session
    run: AnalysisRun
    inv: Investigation
    meta: MetaClient
    storage: Storage
    settings: Settings
    prepared: validation.PreparedPair | None = None
    prepared_png: dict[str, bytes] = field(default_factory=dict)
    prepared_rgb: dict[str, np.ndarray] = field(default_factory=dict)
    segs: dict[tuple[str, str], list[SegmentationResult]] = field(default_factory=dict)
    masks: dict[str, np.ndarray] = field(default_factory=dict)
    overlays: dict[str, bytes] = field(default_factory=dict)
    comparisons: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, dict] = field(default_factory=dict)
    interpretations: dict[str, dict] = field(default_factory=dict)
    saved: bool = False
    report_id: str | None = None
    qc_flagged: bool = False
    prompt_mismatch: bool = False  # earlier/newer ended on different prompts: not like-for-like
    step_idx: int = 0
    prompts_used: list[str] = field(default_factory=list)

    @property
    def categories(self) -> list[str]:
        return list(self.run.categories or [])

    @property
    def rejected(self) -> bool:
        return bool(self.prepared and self.prepared.validation.verdict == "reject")

    def images(self) -> dict[str, Image]:
        return {i.role: i for i in self.inv.images}

    def georef(self) -> Georef | None:
        """Georeference of the *analysis grid* (pixel size rescaled for resampling), not of the
        original upload: areas must be computed on the grid the masks live on."""
        if self.prepared is not None:
            return self.prepared.validation.georef
        img = self.images().get("earlier")
        return validation._georef_from_meta(img.georef_meta) if img else None


# ---- Tool handlers ---------------------------------------------------------------------------------

_SAFE_PROMPT = re.compile(r"^[A-Za-z][A-Za-z \-]{0,58}$")


def _require_prepared(ctx: RunContext) -> None:
    if ctx.prepared is None:
        raise ToolError("Call validate_image_pair first.")
    if ctx.rejected:
        raise ToolError("The image pair was rejected. Stop and explain why to the user.")


def _check_category(ctx: RunContext, category: str) -> None:
    if category not in ctx.categories:
        raise ToolError(f"Category '{category}' is not part of this run. Allowed: {ctx.categories}.")


def t_get_investigation(ctx: RunContext, _: NoArgs) -> dict:
    imgs = ctx.images()
    return {
        "title": ctx.inv.title,
        "location": ctx.inv.location_name,
        "objective": ctx.inv.objective,
        "categories": ctx.categories,
        "images": [
            {
                "role": r,
                "capture_date": str(imgs[r].capture_date) if imgs[r].capture_date else None,
                "source": imgs[r].source,
                "width": imgs[r].width,
                "height": imgs[r].height,
                "georeferenced": bool(imgs[r].georef_meta),
            }
            for r in ROLES if r in imgs
        ],
    }


def t_validate_image_pair(ctx: RunContext, _: NoArgs) -> dict:
    imgs = ctx.images()
    if "earlier" not in imgs or "newer" not in imgs:
        raise ToolError("Both an earlier and a newer image are required.")
    e, n = imgs["earlier"], imgs["newer"]
    pair = validation.prepare_pair(
        ctx.storage.download_bytes(e.storage_path),
        ctx.storage.download_bytes(n.storage_path),
        e.georef_meta, n.georef_meta,
        has_dates=(e.capture_date is not None, n.capture_date is not None),
    )
    ctx.prepared = pair
    for role, arr in (("earlier", pair.earlier), ("newer", pair.newer)):
        png = validation.encode_png(arr)
        ctx.prepared_png[role], ctx.prepared_rgb[role] = png, arr
        ctx.storage.upload_bytes(f"runs/{ctx.run.id}/prepared_{role}.png", png, "image/png")
    ctx.run.validation = pair.validation.model_dump(mode="json")
    v = pair.validation
    return {
        "verdict": v.verdict, "aligned": v.aligned, "method": v.method,
        "overlap_ratio": v.overlap_ratio, "inlier_ratio": v.inlier_ratio,
        "reasons": v.reasons, "warnings": v.warnings,
        "next": "STOP: explain the rejection." if v.verdict == "reject" else "Proceed to segmentation.",
    }


def t_segment_image(ctx: RunContext, a: SegmentArgs) -> dict:
    _require_prepared(ctx)
    _check_category(ctx, a.category)
    attempts = ctx.segs.get((a.role, a.category), [])
    if attempts:
        last = attempts[-1]
        if last.qc_status == "accepted":
            raise ToolError(f"{a.role}/{a.category} is already accepted.")
        if last.qc_status is None:
            raise ToolError("Call check_mask_quality on the previous segmentation before retrying.")
        if last.qc_status == "flagged" or len(attempts) > ctx.settings.max_qc_retries:
            raise ToolError("Retry limit reached; this mask is flagged for human review.")
    prompt = (a.prompt or a.category).strip()
    if not _SAFE_PROMPT.match(prompt):
        raise ToolError("Prompt must be a short plain-text phrase (letters, spaces, hyphens).")
    result = segmentation.segment_image(
        ctx.meta, ctx.storage, ctx.prepared_png[a.role], ctx.prepared_rgb[a.role], prompt
    )
    attempt = len(attempts) + 1
    base = f"runs/{ctx.run.id}/{a.role}_{a.category}_a{attempt}"
    overlay = segmentation.make_overlay(ctx.prepared_rgb[a.role], result.mask, a.category)
    ctx.storage.upload_bytes(f"{base}_mask.png", segmentation.mask_to_png(result.mask), "image/png")
    ctx.storage.upload_bytes(f"{base}_overlay.png", overlay, "image/png")
    row = SegmentationResult(
        run_id=ctx.run.id, image_id=ctx.images()[a.role].id, role=a.role, category=a.category,
        prompt_used=prompt, attempt=attempt, mask_path=f"{base}_mask.png",
        overlay_path=f"{base}_overlay.png", pixel_count=result.pixel_count,
        coverage=result.coverage, from_cache=result.from_cache,
    )
    ctx.db.add(row)
    ctx.db.flush()
    ctx.segs.setdefault((a.role, a.category), []).append(row)
    ctx.masks[str(row.id)] = result.mask
    ctx.overlays[str(row.id)] = overlay
    ctx.prompts_used.append(f"{a.role}/{a.category}:{prompt}")
    return {
        "seg_id": str(row.id), "role": a.role, "category": a.category, "prompt": prompt,
        "attempt": attempt, "pixel_count": result.pixel_count,
        "coverage": round(result.coverage, 5), "from_cache": result.from_cache,
        "next": "Call check_mask_quality with this seg_id.",
    }


def _find_seg(ctx: RunContext, seg_id: str) -> SegmentationResult:
    for rows in ctx.segs.values():
        for r in rows:
            if str(r.id) == seg_id.removeprefix("seg:"):
                return r
    raise ToolError(f"Unknown seg_id '{seg_id}'.")


def t_check_mask_quality(ctx: RunContext, a: SegIdArgs) -> dict:
    row = _find_seg(ctx, a.seg_id)
    mask = ctx.masks[str(row.id)]
    stats = quality.mask_stats(mask, row.category)
    vision_ok: bool | None = None
    issues: list[str] = []
    suggested: str | None = None
    if stats.suspicious:
        issues.append("Vision review skipped: the statistics gate already flagged this mask.")
    else:
        vision_ok, issues, suggested = quality.vision_review(
            ctx.meta, ctx.settings.vision_qc_model or ctx.settings.agent_model,
            ctx.prepared_png[row.role], ctx.overlays[str(row.id)], row.category, row.prompt_used,
        )
    d = quality.decide(
        stats, vision_ok, issues, suggested, row.category, row.prompt_used, row.attempt,
        ctx.settings.max_qc_retries,
    )
    row.qc_status = d.status
    row.qc_detail = d.model_dump(mode="json")
    if d.status == "flagged":
        ctx.qc_flagged = True
    out = {
        "seg_id": str(row.id), "status": d.status, "coverage": round(stats.coverage, 5),
        "vision_acceptable": vision_ok, "issues": d.issues,
        "label": "AI-assisted segmentation QC (not a guarantee of accuracy)",
    }
    if d.status == "retry":
        out["next"] = f"Call segment_image again for role={row.role}, category={row.category} with prompt='{d.suggested_prompt}'."
        out["suggested_prompt"] = d.suggested_prompt
    return out


def _final_seg(ctx: RunContext, role: str, category: str) -> SegmentationResult:
    rows = ctx.segs.get((role, category), [])
    if not rows or rows[-1].qc_status not in ("accepted", "flagged"):
        raise ToolError(f"{role}/{category} needs a finished segmentation and quality check first.")
    return rows[-1]


def t_compare_masks(ctx: RunContext, a: CategoryArgs) -> dict:
    _require_prepared(ctx)
    _check_category(ctx, a.category)
    old, new = _final_seg(ctx, "earlier", a.category), _final_seg(ctx, "newer", a.category)
    res = compare_masks(ctx.masks[str(old.id)], ctx.masks[str(new.id)], ctx.prepared.valid)
    ctx.comparisons[a.category] = res
    ov = diff_overlay(ctx.masks[str(old.id)], ctx.masks[str(new.id)])
    from PIL import Image as PILImage
    import io
    buf = io.BytesIO()
    PILImage.fromarray(ov, mode="RGBA").save(buf, format="PNG")
    ctx.storage.upload_bytes(f"runs/{ctx.run.id}/diff_{a.category}.png", buf.getvalue(), "image/png")
    return {"evidence_id": f"cmp:{a.category}", **res.model_dump(mode="json")}


def t_calculate_change_metrics(ctx: RunContext, a: CategoryArgs) -> dict:
    res = ctx.comparisons.get(a.category)
    if res is None:
        raise ToolError("Call compare_masks for this category first.")
    geo = ctx.georef()
    v = ctx.prepared.validation
    old, new = _final_seg(ctx, "earlier", a.category), _final_seg(ctx, "newer", a.category)
    m: dict[str, Any] = {
        "category": a.category, **res.model_dump(mode="json"),
        "real_world_area_available": m2_supported(geo),
        "alignment_verified": v.aligned,
        "qc": {"earlier": old.qc_status, "newer": new.qc_status},
        "prompts": {"earlier": old.prompt_used, "newer": new.prompt_used},
    }
    if m2_supported(geo):
        m["area_m2"] = {
            "earlier": area_m2(res.old_pixels, geo), "newer": area_m2(res.new_pixels, geo),
            "difference": area_m2(abs(res.difference_pixels), geo) * (1 if res.difference_pixels >= 0 else -1),
        }
    if not v.aligned:
        # Counts are still shown, but without verified alignment they do not support a direction.
        m["change_direction_raw"] = m["change_direction"]
        m["change_direction"] = "undetermined"
    if old.prompt_used != new.prompt_used:
        # A retry changed the prompt for one image only: the two masks are not like-for-like.
        m["prompt_mismatch"] = True
        ctx.prompt_mismatch = True
    ctx.metrics[a.category] = m
    return m


def _limitations(ctx: RunContext, category: str) -> list[str]:
    v = ctx.prepared.validation
    lim: list[str] = []
    if not m2_supported(ctx.georef()):
        lim.append("Real-world area is not established; results are detected-pixel counts only.")
    if not v.aligned:
        lim.append("Image alignment was not verified; the comparison may be unreliable.")
    lim.extend(v.reasons)
    lim.extend(v.warnings)
    m = ctx.metrics[category]
    if m["change_percent"] is None:
        lim.append("The earlier mask has no detected pixels, so a percentage change is undefined.")
    if m.get("prompt_mismatch"):
        lim.append(
            f"Different segmentation prompts were used for the earlier ('{m['prompts']['earlier']}') and newer "
            f"('{m['prompts']['newer']}') images, so the masks may not be like-for-like."
        )
    if m.get("change_direction_raw") is not None:
        lim.append("Alignment was not verified, so the direction of change is reported as undetermined.")
    for role in ROLES:
        r = ctx.segs[(role, category)][-1]
        if r.qc_status == "flagged":
            lim.append(f"The {role} {category} mask was flagged by quality control and needs human review.")
    lim.append("Segmentation is model-generated and can include errors.")
    return list(dict.fromkeys(lim))


def t_save_analysis_results(ctx: RunContext, _: NoArgs) -> dict:
    _require_prepared(ctx)
    missing = [c for c in ctx.categories if c not in ctx.metrics]
    if missing:
        raise ToolError(f"Metrics missing for categories: {missing}.")
    imgs = ctx.images()
    findings = []
    for c in ctx.categories:
        m = ctx.metrics[c]
        refs = [f"image:{imgs[r].id}" for r in ROLES] + [
            f"seg:{ctx.segs[(r, c)][-1].id}" for r in ROLES
        ] + [f"cmp:{c}"]
        findings.append({
            "category": c, "change_direction": m["change_direction"],
            "metric_type": m["metric_type"], "old_value": m["old_pixels"],
            "new_value": m["new_pixels"], "change_value": m["difference_pixels"],
            "change_percent": m["change_percent"], "evidence_refs": refs,
            "limitations": _limitations(ctx, c), "review_status": "pending",
        })
    ctx.run.metrics = ctx.metrics
    ctx.run.findings = findings
    ctx.saved = True
    return {"saved": True, "categories": ctx.categories,
            "evidence_ids": sorted({r for f in findings for r in f["evidence_refs"]})}


def allowed_evidence_ids(ctx: RunContext) -> set[str]:
    ids = {f"image:{i.id}" for i in ctx.inv.images}
    for rows in ctx.segs.values():
        ids |= {f"seg:{r.id}" for r in rows}
    ids |= {f"cmp:{c}" for c in ctx.comparisons}
    return ids


_NUM = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)\s*(%?)")


def _allowed_numbers(m: dict) -> tuple[set[str], set[str]]:
    plain: set[str] = set()
    pct: set[str] = set()

    def add_num(v: float | int, into: set[str]) -> None:
        for fmt in (f"{v:,.0f}", f"{v:.0f}", f"{v:.1f}", f"{v:.2f}", f"{v:.4f}"):
            into.add(fmt.replace(",", "").rstrip("0").rstrip(".") if "." in fmt else fmt.replace(",", ""))
        into.add(f"{v:,.0f}")

    for k in ("old_pixels", "new_pixels", "valid_pixels"):
        add_num(m[k], plain)
    add_num(abs(m["difference_pixels"]), plain)
    if m.get("change_percent") is not None:
        add_num(abs(m["change_percent"]), pct)
        add_num(abs(m["change_percent"]), plain)
    for k in ("coverage_old", "coverage_new"):
        add_num(m[k] * 100, pct)
    return plain, pct


def check_interpretation_text(text: str, metrics: dict) -> str | None:
    low = text.lower()
    for t in FORBIDDEN_TERMS:
        if t in low:
            return f"Do not use the word '{t}': imagery cannot establish unlawful activity."
    plain, pct = _allowed_numbers(metrics)
    for num, is_pct in _NUM.findall(text):
        n = num.replace(",", "")
        norm = n.rstrip("0").rstrip(".") if "." in n else n
        pool = pct if is_pct else plain
        if norm in pool or n in pool:
            continue
        if not is_pct and "." not in n and int(n) <= 10:
            continue  # small counts such as "2 retries"
        return f"The number '{num}{'%' if is_pct else ''}' does not appear in the computed metrics."
    return None


def t_submit_interpretation(ctx: RunContext, a: InterpretationArgs) -> dict:
    if not ctx.saved:
        raise ToolError("Call save_analysis_results before submit_interpretation.")
    _check_category(ctx, a.category)
    m = ctx.metrics[a.category]
    if a.change_direction != m["change_direction"]:
        raise ToolError(
            f"change_direction '{a.change_direction}' contradicts the computed result "
            f"'{m['change_direction']}'."
        )
    allowed = allowed_evidence_ids(ctx)
    bad = [e for e in a.evidence_ids if e not in allowed]
    if bad:
        raise ToolError(f"Unknown evidence ids: {bad}. Use ids returned by earlier tools.")
    if f"cmp:{a.category}" not in a.evidence_ids:
        raise ToolError(f"evidence_ids must include 'cmp:{a.category}'.")
    for text in [a.summary, a.recommended_action, *a.limitations]:
        err = check_interpretation_text(text, m)
        if err:
            raise ToolError(err)
    item = a.model_dump()
    item["requires_human_review"] = True  # always true: imagery is not proof
    item["source"] = "gemini" if ctx.settings.ai_provider.strip().lower() == "gemini" else "muse_spark"
    ctx.interpretations[a.category] = item
    return {"accepted": True, "category": a.category}


def backend_interpretation(ctx: RunContext, category: str) -> dict:
    """Deterministic fallback when the model never submits a valid interpretation."""
    m = ctx.metrics[category]
    verb = {"increase": "an increase", "decrease": "a decrease", "unchanged": "no change",
            "undetermined": "no determinable change"}[m["change_direction"]]
    return {
        "category": category,
        "summary": f"Potential change detected: {verb} in detected {category} pixels between the two images.",
        "change_direction": m["change_direction"],
        "evidence_ids": [f"cmp:{category}"],
        "limitations": _limitations(ctx, category),
        "recommended_action": "Have a human reviewer inspect the highlighted regions and confirm on site.",
        "requires_human_review": True,
        "source": "backend_template",
    }


def t_generate_report(ctx: RunContext, _: NoArgs) -> dict:
    if not ctx.saved:
        raise ToolError("Call save_analysis_results before generate_report.")
    missing = [c for c in ctx.categories if c not in ctx.interpretations]
    if missing:
        raise ToolError(f"Call submit_interpretation for: {missing}.")
    _persist_interpretation(ctx)
    ctx.run.status = _final_status(ctx)  # report kind depends on the final status
    ctx.db.flush()
    rep = report_service.generate(ctx.db, ctx.storage, ctx.run)
    ctx.report_id = str(rep.id)
    return {"report_id": str(rep.id), "kind": rep.kind}


def t_create_inspection_task(ctx: RunContext, a: TaskArgs) -> dict:
    if not ctx.run.approval:
        raise ToolError("The user has not approved creating an inspection task.")
    task = create_task(ctx.db, ctx.run, a.title, a.reason, a.priority)
    return {"task_id": str(task.id)}


def create_task(db: Session, run: AnalysisRun, title: str, reason: str, priority: str) -> InspectionTask:
    task = InspectionTask(
        investigation_id=run.investigation_id, run_id=run.id, title=title, reason=reason, priority=priority
    )
    db.add(task)
    db.flush()
    return task


@dataclass
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    handler: Callable[[RunContext, Any], dict]

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.args.model_json_schema()}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("get_investigation", "Read the investigation metadata and image info.", NoArgs, t_get_investigation),
    Tool("validate_image_pair", "Check the two images are comparable and align them. Must run first.", NoArgs, t_validate_image_pair),
    Tool("segment_image", "Segment one image (role earlier|newer) for a category with SAM. Optional alternate prompt for retries.", SegmentArgs, t_segment_image),
    Tool("check_mask_quality", "Run AI-assisted quality control on a segmentation (seg_id). May tell you to retry with a different prompt.", SegIdArgs, t_check_mask_quality),
    Tool("compare_masks", "Compare the earlier and newer masks for a category (backend computed).", CategoryArgs, t_compare_masks),
    Tool("calculate_change_metrics", "Compute supported metrics for a category (backend computed).", CategoryArgs, t_calculate_change_metrics),
    Tool("save_analysis_results", "Persist findings with evidence references once metrics exist for all categories.", NoArgs, t_save_analysis_results),
    Tool("submit_interpretation", "Submit your structured interpretation for one category. Validated against stored results.", InterpretationArgs, t_submit_interpretation),
    Tool("generate_report", "Generate the PDF report after interpretations are submitted.", NoArgs, t_generate_report),
    Tool("create_inspection_task", "Create a follow-up inspection task. Only available after user approval.", TaskArgs, t_create_inspection_task),
]}

SYSTEM_PROMPT = """You are GreenWatch's investigation agent, helping an environmental officer examine \
potential change between two images of one site. Use ONLY the provided tools; they are real backend functions.

Required order:
1. get_investigation, then validate_image_pair. If the verdict is 'reject', stop and explain why; do nothing else.
2. For each category: segment_image for role 'earlier' and 'newer', then check_mask_quality for each seg_id. \
If a check returns status 'retry', call segment_image again for that role with the suggested prompt, then check again.
3. compare_masks and calculate_change_metrics for each category.
4. save_analysis_results, then submit_interpretation once per category, then generate_report.

Rules: never state a number a tool did not return. Never claim illegality, causation or real-world area \
(only if a tool reports area_m2). Use the wording 'potential change detected; requires human review'. \
Tool errors tell you what to fix; act on them. Finish with one plain sentence summarising the outcome."""


# ---- Orchestration ---------------------------------------------------------------------------------

def _persist_interpretation(ctx: RunContext) -> None:
    ctx.run.interpretation = {"items": list(ctx.interpretations.values())}


def _final_status(ctx: RunContext) -> str:
    v = ctx.prepared.validation if ctx.prepared else None
    if (v and v.verdict != "ok") or ctx.qc_flagged or ctx.prompt_mismatch:
        return "review_required"
    return "completed"


def _log_step(ctx: RunContext, name: str, args: dict) -> AgentStep:
    if ctx.step_idx == 0:  # a rebuilt context must continue the persisted numbering
        from sqlalchemy import func
        ctx.step_idx = ctx.db.query(func.coalesce(func.max(AgentStep.idx), 0)).filter(
            AgentStep.run_id == ctx.run.id).scalar() or 0
    ctx.step_idx += 1
    step = AgentStep(run_id=ctx.run.id, idx=ctx.step_idx, tool=name, input=args, status="running")
    ctx.db.add(step)
    ctx.db.commit()
    return step


def run_tool(ctx: RunContext, name: str, raw_args: dict) -> dict:
    step = _log_step(ctx, name, raw_args)
    t0 = time.perf_counter()
    try:
        tool = TOOLS.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool '{name}'.")
        if name == "create_inspection_task" and not ctx.run.approval:
            raise ToolError("Tool not available: no user approval.")
        try:
            args = tool.args.model_validate(raw_args)
        except ValidationError as e:
            raise ToolError(f"Invalid arguments: {e.errors()[0]['msg']} ({e.errors()[0]['loc']}).") from e
        out = tool.handler(ctx, args)
        step.status, step.output = "ok", out
    except ToolError as e:
        out = {"error": str(e)}
        step.status, step.output = "error", out
    except Exception as e:
        step.status, step.output = "error", {"error": str(e)}
        step.duration_ms = int((time.perf_counter() - t0) * 1000)
        ctx.db.commit()
        raise
    step.duration_ms = int((time.perf_counter() - t0) * 1000)
    ctx.db.commit()
    return out


def _tool_schemas(ctx: RunContext) -> list[dict]:
    return [t.schema() for n, t in TOOLS.items() if n != "create_inspection_task" or ctx.run.approval]


def _missing_steps(ctx: RunContext) -> tuple[list[str], list[str]]:
    """(required, expected). Required = analysis steps; the run fails if they never happen.
    Expected = interpretation + report; the backend completes these (logged as backend steps)
    if the model does not, after being nudged."""
    if ctx.prepared is None:
        return ["validate_image_pair"], []
    if ctx.rejected:
        return [], []
    req: list[str] = []
    for c in ctx.categories:
        for r in ROLES:
            rows = ctx.segs.get((r, c), [])
            if not rows or rows[-1].qc_status not in ("accepted", "flagged"):
                req.append(f"segment_image + check_mask_quality for {r}/{c}")
        if c not in ctx.metrics:
            req.append(f"compare_masks + calculate_change_metrics for {c}")
    if not req and not ctx.saved:
        req.append("save_analysis_results")
    exp: list[str] = []
    if not req:
        exp += [f"submit_interpretation for {c}" for c in ctx.categories if c not in ctx.interpretations]
        if ctx.report_id is None:
            exp.append("generate_report")
    return req, exp


def agent_loop(ctx: RunContext) -> None:
    s = ctx.settings
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Investigation: {ctx.inv.title} ({ctx.inv.location_name}).\n"
            f"Objective: {ctx.inv.objective}\nCategories to analyse: {', '.join(ctx.categories)}."
        )},
    ]
    calls, nudges = 0, 0
    while calls < s.max_tool_calls_per_run:
        resp = ctx.meta.chat(messages, model=s.agent_model, tools=_tool_schemas(ctx))
        messages.append({
            "role": "assistant", "content": resp.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)}}
                for tc in resp.tool_calls
            ] or None,
        })
        if not resp.tool_calls:
            req, exp = _missing_steps(ctx)
            if ctx.rejected or not (req or exp):
                return
            if nudges >= 2:
                if req:
                    raise DomainError("The agent stopped before completing required steps: " + "; ".join(req))
                return  # backend completes interpretation/report below
            nudges += 1
            messages.append({"role": "user", "content": "Required steps are not finished: " + "; ".join(req + exp) + ". Continue."})
            continue
        for tc in resp.tool_calls:
            if calls >= s.max_tool_calls_per_run:
                break
            calls += 1
            out = run_tool(ctx, tc.name, tc.arguments)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(out, default=str)})
        req, exp = _missing_steps(ctx)
        if ctx.rejected or not (req or exp):
            return  # everything done; no further model turn needed
    req, _ = _missing_steps(ctx)
    if req:
        raise DomainError(f"Tool-call limit ({s.max_tool_calls_per_run}) reached before the run completed.")


def rejection_explanation(ctx: RunContext) -> dict:
    v = ctx.prepared.validation
    return {"items": [{
        "category": None,
        "summary": "Analysis stopped: the image pair is not suitable for comparison.",
        "change_direction": "undetermined", "evidence_ids": ["validation"],
        "limitations": v.reasons or ["The images could not be validated as comparable."],
        "recommended_action": "Provide a better-aligned pair of images of the same site and re-run.",
        "requires_human_review": True, "source": "backend_template",
    }]}


def execute_run(db: Session, run_id: uuid.UUID, meta: MetaClient, storage: Storage) -> None:
    """Run the agent for one analysis run. Never raises: failures are recorded on the run."""
    s = get_settings()
    run = db.get(AnalysisRun, run_id)
    if run is None:
        return
    inv = db.get(Investigation, run.investigation_id)
    ctx = RunContext(db=db, run=run, inv=inv, meta=meta, storage=storage, settings=s)
    run.status, run.started_at = "processing", _now()
    inv.status = "processing"
    run.model_config_json = {
        "ai_provider": s.ai_provider,
        "segmentation_provider": "meta",
        "sam_model": s.sam_model, "agent_model": s.agent_model,
        "vision_qc_model": s.vision_qc_model or s.agent_model, "qc_bounds": s.qc_bounds,
        "max_qc_retries": s.max_qc_retries, "max_tool_calls": s.max_tool_calls_per_run,
    }
    db.commit()
    try:
        agent_loop(ctx)
        if ctx.prepared is None:
            raise DomainError("The agent finished without validating the image pair.")
        if ctx.rejected:
            run.interpretation = rejection_explanation(ctx)
            run.error = "; ".join(ctx.prepared.validation.reasons)
            run.status = "review_required"
            db.commit()
            report_service.generate(db, storage, run)  # diagnostic report
        else:
            for c in ctx.categories:  # backend fallback only if the model never produced one
                if c not in ctx.interpretations and c in ctx.metrics and ctx.saved:
                    step = _log_step(ctx, "backend_fallback_interpretation", {"category": c})
                    ctx.interpretations[c] = backend_interpretation(ctx, c)
                    step.status, step.output = "ok", {"note": "Model did not submit a valid interpretation."}
                    db.commit()
            _persist_interpretation(ctx)
            run.status = _final_status(ctx)
            if ctx.report_id is None and ctx.saved:
                rep = report_service.generate(db, storage, run)
                ctx.report_id = str(rep.id)
        run.model_config_json = {**run.model_config_json, "prompts_used": ctx.prompts_used}
    except Exception as e:  # noqa: BLE001
        db.rollback()
        run = db.get(AnalysisRun, run_id)
        run.status = "failed"
        run.error = (e.detail if isinstance(e, DomainError) else f"{type(e).__name__}: {e}")[:1000]
        if isinstance(e, MetaApiError):
            log.error(
                "analysis run failed run_id=%s provider_status=%s provider_code=%s error_type=%s",
                run_id, e.status, e.provider_code, type(e).__name__,
            )
        else:
            log.error(
                "analysis run failed run_id=%s error_type=%s",
                run_id, type(e).__name__,
            )
    run.completed_at = _now()
    inv = db.get(Investigation, run.investigation_id)
    inv.status = run.status
    db.commit()


def mark_stale_runs(db: Session, minutes: int) -> int:
    """Background tasks die on restarts; fail anything stuck in processing/queued too long."""
    from datetime import timedelta
    cutoff = _now() - timedelta(minutes=minutes)
    n = 0
    for run in db.query(AnalysisRun).filter(AnalysisRun.status.in_(["processing", "queued"])).all():
        ts = run.started_at or run.created_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            run.status, run.error, run.completed_at = "failed", "Run interrupted (server restarted or timed out).", _now()
            inv = db.get(Investigation, run.investigation_id)
            if inv:
                inv.status = "failed"
            n += 1
    db.commit()
    return n
