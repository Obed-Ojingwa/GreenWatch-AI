"""PDF report generation behind a validation gate.

Rules enforced here (not in prompts):
- A `failed` or unfinished run never produces a report.
- Every number printed comes from stored metrics; model text is only the validated narrative.
- If any gate check fails, the output is a DIAGNOSTIC report that cannot be mistaken for a
  successful assessment.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from app.core.errors import ReportGateFailed
from app.db.models import AnalysisRun, Investigation, Report
from app.services.storage import Storage

KIND_STYLE = {
    "full": ("GREENWATCH AI: ENVIRONMENTAL CHANGE INVESTIGATION", colors.HexColor("#166534")),
    "needs_review": ("GREENWATCH AI: INVESTIGATION (NEEDS HUMAN REVIEW)", colors.HexColor("#b45309")),
    "diagnostic": ("DIAGNOSTIC REPORT: ANALYSIS NOT COMPLETED", colors.HexColor("#991b1b")),
}


def gate_checks(db: Session, run: AnalysisRun) -> list[str]:
    """Return a list of failed checks; empty means the run supports a real report."""
    fails: list[str] = []
    cats = list(run.categories or [])
    metrics = run.metrics or {}
    if not metrics:
        return ["No measurements were produced for this run."]
    for c in cats:
        m = metrics.get(c)
        if not m or any(k not in m for k in ("old_pixels", "new_pixels", "difference_pixels", "metric_type")):
            fails.append(f"Required measurements missing for '{c}'.")
        elif "area_m2" in m and not m.get("real_world_area_available"):
            fails.append(f"Square-metre values present for '{c}' without georeferencing support.")
    seg_ids = {f"seg:{s.id}" for s in run.segmentations}
    img_ids = {f"image:{i.id}" for i in run.investigation.images}
    for f in run.findings or []:
        for ref in f.get("evidence_refs", []):
            ok = (
                (ref.startswith("seg:") and ref in seg_ids)
                or (ref.startswith("image:") and ref in img_ids)
                or (ref.startswith("cmp:") and ref.removeprefix("cmp:") in metrics)
            )
            if not ok:
                fails.append(f"Evidence reference '{ref}' does not resolve to a stored record.")
    if not run.findings:
        fails.append("No findings were saved.")
    items = (run.interpretation or {}).get("items") or []
    have = {i.get("category") for i in items}
    for c in cats:
        if c not in have:
            fails.append(f"No validated interpretation for '{c}'.")
    return fails


def _styles():
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontSize=15, textColor=colors.white, leading=19),
        "h": ParagraphStyle("h", parent=ss["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4),
        "p": ParagraphStyle("p", parent=ss["BodyText"], fontSize=9.5, leading=13),
        "small": ParagraphStyle("s", parent=ss["BodyText"], fontSize=8, leading=10, textColor=colors.HexColor("#475569")),
    }


def _img(data: bytes, width_mm: float) -> Image:
    w, h = PILImage.open(io.BytesIO(data)).size
    width = width_mm * mm
    return Image(io.BytesIO(data), width=width, height=width * h / w)


def _blend(base_png: bytes, diff_png: bytes) -> bytes:
    base = PILImage.open(io.BytesIO(base_png)).convert("RGBA")
    diff = PILImage.open(io.BytesIO(diff_png)).convert("RGBA")
    if diff.size != base.size:
        diff = diff.resize(base.size)
    out = io.BytesIO()
    PILImage.alpha_composite(base, diff).convert("RGB").save(out, format="PNG")
    return out.getvalue()


def _fmt_pct(v: float | None) -> str:
    return "undefined (earlier = 0)" if v is None else f"{v:+.2f}%"


def build_pdf(
    kind: str, inv: Investigation, run: AnalysisRun, storage: Storage, failures: list[str]
) -> bytes:
    st = _styles()
    title, banner = KIND_STYLE[kind]
    story: list = []
    head = Table([[Paragraph(escape(title), st["title"])]], colWidths=[180 * mm])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), banner), ("LEFTPADDING", (0, 0), (-1, -1), 10),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [head, Spacer(1, 6)]

    imgs = {i.role: i for i in inv.images}
    meta_rows = [
        ["Investigation ID", str(inv.id)], ["Analysis run ID", str(run.id)],
        ["Title", inv.title], ["Location", inv.location_name],
        ["Objective", inv.objective],
        ["Analysis date", (run.completed_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")],
        ["Earlier image", f"{imgs['earlier'].capture_date or 'date unknown'} | {imgs['earlier'].source or 'source unknown'}" if "earlier" in imgs else "n/a"],
        ["Newer image", f"{imgs['newer'].capture_date or 'date unknown'} | {imgs['newer'].source or 'source unknown'}" if "newer" in imgs else "n/a"],
        ["Run status", run.status],
    ]
    t = Table([[Paragraph(f"<b>{escape(a)}</b>", st["p"]), Paragraph(escape(str(b)), st["p"])] for a, b in meta_rows],
              colWidths=[38 * mm, 142 * mm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [Paragraph("A. Investigation details", st["h"]), t]

    v = run.validation or {}
    if kind == "diagnostic":
        story.append(Paragraph("B. Why no assessment was produced", st["h"]))
        reasons = failures or v.get("reasons") or ([run.error] if run.error else ["The analysis did not complete."])
        for r in reasons:
            story.append(Paragraph("&bull; " + escape(str(r)), st["p"]))
        story.append(Paragraph(
            "This is a diagnostic document. It does not state any finding about change at the site.", st["p"]))
        if v:
            story += [Paragraph("Validation details", st["h"]),
                      Paragraph(escape(f"method={v.get('method')}, aligned={v.get('aligned')}, "
                                       f"overlap={v.get('overlap_ratio')}, inlier_ratio={v.get('inlier_ratio')}"), st["small"]),
                      ]
        return _finish(story, st, kind, run)

    # Executive summary (validated narrative only)
    items = (run.interpretation or {}).get("items") or []
    story.append(Paragraph("B. Executive summary", st["h"]))
    if kind == "needs_review":
        story.append(Paragraph(
            "<b>This analysis requires human review before any use:</b> alignment or segmentation quality "
            "checks did not fully pass.", st["p"]))
    for it in items:
        story.append(Paragraph(escape(it.get("summary", "")), st["p"]))
    story.append(Paragraph("Potential change detected; requires human review. Imagery alone cannot establish "
                           "cause, legality or responsibility.", st["small"]))

    # Visual evidence
    story.append(Paragraph("C. Images and visual evidence", st["h"]))
    try:
        e_png = storage.download_bytes(f"runs/{run.id}/prepared_earlier.png")
        n_png = storage.download_bytes(f"runs/{run.id}/prepared_newer.png")
        story.append(Table([[_img(e_png, 85), _img(n_png, 85)],
                            [Paragraph("Earlier (analysis grid)", st["small"]), Paragraph("Newer (aligned to earlier)", st["small"])]],
                           colWidths=[90 * mm, 90 * mm]))
        for c in run.categories:
            finals = {}
            for s in run.segmentations:
                if s.category == c and (s.role not in finals or s.attempt > finals[s.role].attempt):
                    finals[s.role] = s
            cells, caps = [], []
            for role in ("earlier", "newer"):
                s = finals.get(role)
                if s and s.overlay_path:
                    cells.append(_img(storage.download_bytes(s.overlay_path), 85))
                    caps.append(Paragraph(escape(f"{role.title()} {c} mask (prompt '{s.prompt_used}', QC {s.qc_status})"), st["small"]))
            if cells:
                story.append(Table([cells, caps], colWidths=[90 * mm] * len(cells)))
            if storage.exists(f"runs/{run.id}/diff_{c}.png"):
                story.append(Spacer(1, 4))
                story.append(_img(_blend(n_png, storage.download_bytes(f"runs/{run.id}/diff_{c}.png")), 85))
                story.append(Paragraph(escape(f"Change view for {c}: green = gained, red = lost, grey = detected in both"), st["small"]))
    except Exception:  # noqa: BLE001  (a missing image must not produce a misleading report)
        raise ReportGateFailed("Evidence images could not be loaded; cannot produce a report.") from None

    # Measurements: values rendered here from stored metrics only
    story.append(Paragraph("D. Measurements (detected pixels)", st["h"]))
    rows = [["Category", "Earlier px", "Newer px", "Difference px", "Change %", "Coverage earlier / newer"]]
    for c in run.categories:
        m = run.metrics[c]
        rows.append([c, f"{m['old_pixels']:,}", f"{m['new_pixels']:,}", f"{m['difference_pixels']:+,}",
                     _fmt_pct(m["change_percent"]), f"{m['coverage_old']:.2%} / {m['coverage_new']:.2%}"])
    mt = Table(rows, repeatRows=1)
    mt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#94a3b8")),
                            ("FONTSIZE", (0, 0), (-1, -1), 8.5)]))
    story.append(mt)
    if any(run.metrics[c].get("area_m2") for c in run.categories):
        arows = [["Category", "Earlier m²", "Newer m²", "Difference m²"]]
        for c in run.categories:
            a = run.metrics[c].get("area_m2")
            if a:
                arows.append([c, f"{a['earlier']:,.1f}", f"{a['newer']:,.1f}", f"{a['difference']:+,.1f}"])
        at = Table(arows)
        at.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#94a3b8")), ("FONTSIZE", (0, 0), (-1, -1), 8.5)]))
        story += [Spacer(1, 4), Paragraph("Estimated area (georeferenced, projected CRS)", st["small"]), at]
    else:
        story.append(Paragraph("Real-world area was not established: values are detected-pixel counts, not square metres.", st["small"]))

    # Findings with evidence
    story.append(Paragraph("E. Findings and evidence", st["h"]))
    for f in run.findings or []:
        story.append(Paragraph(
            f"<b>{escape(f['category'].title())}</b>: direction <b>{escape(f['change_direction'])}</b>, "
            f"{f['old_value']:,} to {f['new_value']:,} detected pixels. Review status: {escape(f['review_status'])}.", st["p"]))
        story.append(Paragraph("Evidence: " + escape(", ".join(f["evidence_refs"])), st["small"]))
        for lim in f["limitations"]:
            story.append(Paragraph("&bull; " + escape(lim), st["small"]))

    story.append(Paragraph("F. Recommended action", st["h"]))
    for it in items:
        story.append(Paragraph("&bull; " + escape(it.get("recommended_action", "")), st["p"]))

    story.append(Paragraph("G. Method and limitations", st["h"]))
    story.append(Paragraph(escape(
        f"Alignment: {v.get('method')} (aligned={v.get('aligned')}, overlap={v.get('overlap_ratio')}). "
        "Segmentation by SAM 3.1; quality screened with AI-assisted review (not a guarantee of accuracy); "
        "measurements computed by backend code; narrative text by Muse Spark validated against stored results."), st["p"]))
    for w in (v.get("warnings") or []) + (v.get("reasons") or []):
        story.append(Paragraph("&bull; " + escape(str(w)), st["small"]))

    story.append(Paragraph("H. Review status", st["h"]))
    story.append(Paragraph("Human review: pending. Inspection tasks are created only after user approval.", st["p"]))
    return _finish(story, st, kind, run)


def _finish(story: list, st: dict, kind: str, run: AnalysisRun) -> bytes:
    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#475569"))
        canvas.drawString(15 * mm, 9 * mm, "Potential change detected; requires human review. Generated by GreenWatch AI.")
        canvas.drawRightString(195 * mm, 9 * mm, f"Run {str(run.id)[:8]} | page {doc.page}")
        canvas.restoreState()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"GreenWatch report {run.id}")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def generate(db: Session, storage: Storage, run: AnalysisRun) -> Report:
    if run.status in ("queued", "processing"):
        raise ReportGateFailed("The run has not finished; no report can be produced.")
    if run.status == "failed":
        raise ReportGateFailed("The run failed; no report is generated for failed runs.")
    inv = run.investigation
    failures = gate_checks(db, run)
    if failures or not run.metrics:
        kind = "diagnostic"
    elif run.status == "review_required":
        kind = "needs_review"
    else:
        kind = "full"
    pdf = build_pdf(kind, inv, run, storage, failures)
    path = f"reports/{inv.id}/{run.id}_{kind}_{uuid.uuid4().hex[:8]}.pdf"
    storage.upload_bytes(path, pdf, "application/pdf")
    rep = Report(investigation_id=inv.id, run_id=run.id, kind=kind, storage_path=path)
    db.add(rep)
    db.flush()
    return rep
