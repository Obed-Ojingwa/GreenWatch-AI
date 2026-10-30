"""Shared API helpers: access control and serializers."""
from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy.orm import Session

from app import schemas as S
from app.core.errors import NotFound, Unauthorized
from app.core.security import has_demo_key
from app.db.models import AgentStep, AnalysisRun, Investigation, InspectionTask, Report
from app.db.session import get_db
from app.services.storage import Storage, get_storage


def storage_dep() -> Storage:
    return get_storage()


def load_investigation(db: Session, inv_id: uuid.UUID, keyed: bool) -> Investigation:
    inv = db.get(Investigation, inv_id)
    if inv is None:
        raise NotFound("Investigation not found.")
    if not inv.is_sample and not keyed:
        raise Unauthorized("A demo access key is required to view this investigation.")
    return inv


def load_run(db: Session, run_id: uuid.UUID, keyed: bool) -> AnalysisRun:
    run = db.get(AnalysisRun, run_id)
    if run is None:
        raise NotFound("Run not found.")
    load_investigation(db, run.investigation_id, keyed)
    return run


def investigation_out(inv: Investigation, storage: Storage) -> S.InvestigationOut:
    latest = max(inv.runs, key=lambda r: r.created_at, default=None)
    return S.InvestigationOut(
        id=inv.id, title=inv.title, location_name=inv.location_name, latitude=inv.latitude,
        longitude=inv.longitude, objective=inv.objective, notes=inv.notes, is_sample=inv.is_sample,
        status=inv.status, created_at=inv.created_at, latest_run_id=latest.id if latest else None,
        images=[
            S.ImageOut(id=i.id, role=i.role, capture_date=i.capture_date, source=i.source, width=i.width,
                       height=i.height, georeferenced=bool(i.georef_meta), url=storage.signed_url(i.storage_path))
            for i in sorted(inv.images, key=lambda x: x.role)
        ],
    )


def task_out(t: InspectionTask, db: Session) -> S.TaskOut:
    inv = db.get(Investigation, t.investigation_id)
    return S.TaskOut(id=t.id, investigation_id=t.investigation_id, run_id=t.run_id,
                     investigation_title=inv.title if inv else None, title=t.title, reason=t.reason,
                     priority=t.priority, status=t.status, created_at=t.created_at)


def report_out(r: Report, storage: Storage) -> S.ReportOut:
    return S.ReportOut(id=r.id, kind=r.kind, url=storage.signed_url(r.storage_path), created_at=r.created_at)


def run_out(db: Session, run: AnalysisRun, storage: Storage) -> S.AgentRunOut:
    steps = db.query(AgentStep).filter_by(run_id=run.id).order_by(AgentStep.idx).all()
    prepared: dict[str, str] = {}
    if run.validation is not None:
        for role in ("earlier", "newer"):
            prepared[role] = storage.signed_url(f"runs/{run.id}/prepared_{role}.png")
    diffs = {c: storage.signed_url(f"runs/{run.id}/diff_{c}.png") for c in (run.metrics or {})}
    rep = db.query(Report).filter_by(run_id=run.id).order_by(Report.created_at.desc()).first()
    tasks = db.query(InspectionTask).filter_by(run_id=run.id).all()
    return S.AgentRunOut(
        id=run.id, investigation_id=run.investigation_id, status=run.status, categories=list(run.categories or []),
        error=run.error, started_at=run.started_at, completed_at=run.completed_at, created_at=run.created_at,
        validation=run.validation, metrics=run.metrics, findings=run.findings, interpretation=run.interpretation,
        steps=[S.StepOut(idx=s.idx, tool=s.tool, status=s.status, input=s.input, output=s.output, duration_ms=s.duration_ms) for s in steps],
        segmentations=[
            S.SegOut(id=s.id, role=s.role, category=s.category, prompt_used=s.prompt_used, attempt=s.attempt,
                     pixel_count=s.pixel_count, coverage=s.coverage, qc_status=s.qc_status, qc_detail=s.qc_detail,
                     from_cache=s.from_cache,
                     overlay_url=storage.signed_url(s.overlay_path) if s.overlay_path else None,
                     mask_url=storage.signed_url(s.mask_path) if s.mask_path else None)
            for s in sorted(run.segmentations, key=lambda x: (x.category, x.role, x.attempt))
        ],
        prepared_urls=prepared, diff_urls=diffs,
        report=report_out(rep, storage) if rep else None,
        tasks=[task_out(t, db) for t in tasks],
        model_info=run.model_config_json,
    )


__all__ = ["Depends", "get_db", "has_demo_key", "storage_dep"]
