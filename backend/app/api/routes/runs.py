import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from app import schemas as S
from app.api.deps import load_investigation, load_run, report_out, run_out, storage_dep, task_out
from app.core.config import get_settings
from app.core.errors import Conflict, DomainError, MetaApiError, ValidationFailed
from app.core.security import has_demo_key, require_demo_key, require_meta_diagnostic_key
from app.db.models import AnalysisRun, InspectionTask, Investigation, Report
from app.db.session import SessionLocal, get_db
from app.services import report as report_service
from app.services.agent import create_task, execute_run
from app.services.meta_client import get_meta_client
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/api", tags=["runs"])
limiter = Limiter(key_func=get_remote_address)


def run_in_background(run_id: uuid.UUID) -> None:
    db = SessionLocal()
    try:
        execute_run(db, run_id, get_meta_client(), get_storage())
    finally:
        db.close()


@router.post(
    "/meta/diagnostics",
    dependencies=[Depends(require_meta_diagnostic_key)],
)
@limiter.limit("2/hour")
def diagnose_meta_configuration(request: Request):
    settings = get_settings()
    base = urlsplit(settings.meta_base_url)
    path = settings.meta_chat_path.split("?", 1)[0].strip("/")
    request_path = "/".join(part for part in (base.path.strip("/"), path) if part)
    details = {
        "api_host": base.hostname,
        "endpoint_path": f"/{request_path}" if request_path else "",
        "model": settings.agent_model,
    }
    if not settings.meta_api_key or not base.hostname or not settings.agent_model or not path:
        return {
            "ok": False,
            "result": "missing_configuration",
            "api_key_configured": bool(settings.meta_api_key),
            **details,
        }

    try:
        response = get_meta_client().chat(
            [{"role": "user", "content": "Reply with exactly the word OK."}],
            model=settings.agent_model,
        )
    except MetaApiError as exc:
        return {
            "ok": False,
            "result": "provider_error" if exc.status else "client_error",
            "provider_status": exc.status,
            "provider_error_code": exc.provider_code,
            **details,
        }

    return {
        "ok": bool(response.content and response.content.strip()),
        "result": "reply_received" if response.content and response.content.strip() else "empty_reply",
        "provider_status": 200,
        **details,
    }


@router.post("/investigations/{inv_id}/agent-runs", response_model=S.AgentRunOut, status_code=202,
             dependencies=[Depends(require_demo_key)])
@limiter.limit("10/minute")
def start_run(request: Request, inv_id: uuid.UUID, body: S.AgentRunCreate, background: BackgroundTasks,
              db: Session = Depends(get_db), storage: Storage = Depends(storage_dep)):
    s = get_settings()
    inv = load_investigation(db, inv_id, True)
    if inv.is_sample:
        raise DomainError("The sample investigation is read-only.")
    roles = {i.role for i in inv.images}
    if roles != {"earlier", "newer"}:
        raise ValidationFailed("Upload both an earlier and a newer image before analysing.")
    if db.query(AnalysisRun).filter(AnalysisRun.investigation_id == inv.id,
                                    AnalysisRun.status.in_(["queued", "processing"])).count():
        raise Conflict("An analysis is already running for this investigation.")
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = db.query(AnalysisRun).filter(AnalysisRun.created_at >= hour_ago).count()
    if recent >= s.max_agent_runs_per_hour:
        raise DomainError("Hourly analysis limit reached; try again later.")
    cats = list(dict.fromkeys(body.categories)) or ["buildings"]
    run = AnalysisRun(investigation_id=inv.id, categories=cats, status="queued")
    db.add(run)
    inv.status = "processing"
    db.commit()
    background.add_task(run_in_background, run.id)
    return run_out(db, run, storage)


@router.get("/agent-runs/{run_id}", response_model=S.AgentRunOut)
def get_run(run_id: uuid.UUID, db: Session = Depends(get_db), keyed: bool = Depends(has_demo_key),
            storage: Storage = Depends(storage_dep)):
    return run_out(db, load_run(db, run_id, keyed), storage)


@router.post("/agent-runs/{run_id}/approve-task", response_model=S.TaskOut, status_code=201,
             dependencies=[Depends(require_demo_key)])
def approve_task(run_id: uuid.UUID, body: S.ApproveTaskIn, db: Session = Depends(get_db)):
    run = load_run(db, run_id, True)
    if run.investigation.is_sample:
        raise DomainError("The sample investigation is read-only.")
    if run.status not in ("completed", "review_required"):
        raise Conflict("Tasks can only be created from a finished analysis.")
    run.approval = True
    task = create_task(db, run, body.title, body.reason, body.priority)
    db.commit()
    return task_out(task, db)


@router.post("/agent-runs/{run_id}/report", response_model=S.ReportOut, status_code=201,
             dependencies=[Depends(require_demo_key)])
def regenerate_report(run_id: uuid.UUID, db: Session = Depends(get_db), storage: Storage = Depends(storage_dep)):
    run = load_run(db, run_id, True)
    rep = report_service.generate(db, storage, run)
    db.commit()
    return report_out(rep, storage)


@router.get("/reports/{report_id}", response_model=S.ReportOut)
def get_report(report_id: uuid.UUID, db: Session = Depends(get_db), keyed: bool = Depends(has_demo_key),
               storage: Storage = Depends(storage_dep)):
    rep = db.get(Report, report_id)
    if rep is None:
        from app.core.errors import NotFound
        raise NotFound("Report not found.")
    load_investigation(db, rep.investigation_id, keyed)
    return report_out(rep, storage)


@router.get("/tasks", response_model=list[S.TaskOut])
def list_tasks(db: Session = Depends(get_db), keyed: bool = Depends(has_demo_key)):
    q = db.query(InspectionTask)
    if not keyed:
        q = q.join(Investigation, Investigation.id == InspectionTask.investigation_id).filter(Investigation.is_sample.is_(True))
    return [task_out(t, db) for t in q.order_by(InspectionTask.created_at.desc()).limit(200)]


@router.patch("/tasks/{task_id}", response_model=S.TaskOut, dependencies=[Depends(require_demo_key)])
def patch_task(task_id: uuid.UUID, body: S.TaskPatch, db: Session = Depends(get_db)):
    t = db.get(InspectionTask, task_id)
    if t is None:
        from app.core.errors import NotFound
        raise NotFound("Task not found.")
    t.status = body.status
    db.commit()
    return task_out(t, db)


@router.get("/stats", response_model=S.StatsOut)
def stats(db: Session = Depends(get_db), keyed: bool = Depends(has_demo_key)):
    inv_q = db.query(Investigation)
    task_q = db.query(InspectionTask).filter(InspectionTask.status != "done")
    if not keyed:
        inv_q = inv_q.filter(Investigation.is_sample.is_(True))
        task_q = task_q.join(Investigation, Investigation.id == InspectionTask.investigation_id).filter(Investigation.is_sample.is_(True))
    return S.StatsOut(
        investigations=inv_q.count(),
        awaiting_review=inv_q.filter(Investigation.status == "review_required").count(),
        completed=inv_q.filter(Investigation.status == "completed").count(),
        open_tasks=task_q.count(),
    )
