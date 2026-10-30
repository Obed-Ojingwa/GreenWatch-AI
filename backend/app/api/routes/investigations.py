import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app import schemas as S
from app.api.deps import investigation_out, load_investigation, storage_dep
from app.core.config import get_settings
from app.core.errors import Conflict, DomainError, ValidationFailed
from app.core.security import has_demo_key, require_demo_key
from app.db.models import AnalysisRun, Image, Investigation
from app.db.session import get_db
from app.services.storage import Storage

import hashlib
import io
from datetime import date

from PIL import Image as PILImage

router = APIRouter(prefix="/api", tags=["investigations"])

ALLOWED_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/tiff": "tif"}
MAX_PIXELS = 80_000_000


@router.post("/investigations", response_model=S.InvestigationOut, status_code=201,
             dependencies=[Depends(require_demo_key)])
def create_investigation(body: S.InvestigationCreate, db: Session = Depends(get_db), storage: Storage = Depends(storage_dep)):
    inv = Investigation(**body.model_dump())
    db.add(inv)
    db.commit()
    return investigation_out(inv, storage)


@router.get("/investigations", response_model=list[S.InvestigationOut])
def list_investigations(db: Session = Depends(get_db), keyed: bool = Depends(has_demo_key), storage: Storage = Depends(storage_dep)):
    q = db.query(Investigation)
    if not keyed:
        q = q.filter(Investigation.is_sample.is_(True))
    return [investigation_out(i, storage) for i in q.order_by(Investigation.created_at.desc()).limit(200)]


@router.get("/investigations/{inv_id}", response_model=S.InvestigationOut)
def get_investigation(inv_id: uuid.UUID, db: Session = Depends(get_db), keyed: bool = Depends(has_demo_key), storage: Storage = Depends(storage_dep)):
    return investigation_out(load_investigation(db, inv_id, keyed), storage)


@router.post("/investigations/{inv_id}/images", response_model=S.InvestigationOut, status_code=201,
             dependencies=[Depends(require_demo_key)])
async def upload_image(
    inv_id: uuid.UUID,
    role: str = Form(...),
    capture_date: date | None = Form(default=None),
    source: str | None = Form(default=None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    storage: Storage = Depends(storage_dep),
):
    s = get_settings()
    inv = load_investigation(db, inv_id, True)
    if inv.is_sample:
        raise DomainError("The sample investigation is read-only.")
    if role not in ("earlier", "newer"):
        raise ValidationFailed("role must be 'earlier' or 'newer'.")
    if file.content_type not in ALLOWED_TYPES:
        raise ValidationFailed("Only JPEG, PNG or TIFF images are accepted.")
    data = await file.read(s.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > s.max_upload_mb * 1024 * 1024:
        raise DomainError(f"File exceeds {s.max_upload_mb} MB.")
    try:
        img = PILImage.open(io.BytesIO(data))
        w, h = img.size
        if w * h > MAX_PIXELS:
            raise ValidationFailed(f"Image has too many pixels ({w}x{h}).")
        img.load()
    except ValidationFailed:
        raise
    except Exception as e:  # noqa: BLE001
        raise ValidationFailed(f"Could not decode image: {e}") from e
    from app.services.georef import extract_georef
    georef = extract_georef(img)
    sha = hashlib.sha256(data).hexdigest()
    existing = next((i for i in inv.images if i.role == role), None)
    if existing:
        # Deleting an image cascades to the segmentation rows of every run that used it, which
        # would silently destroy the evidence behind earlier findings and reports.
        if db.query(AnalysisRun).filter(AnalysisRun.investigation_id == inv.id).count():
            raise Conflict(
                "Images cannot be replaced after an analysis has run. Create a new investigation "
                "to keep the earlier evidence intact."
            )
        db.delete(existing)
        db.flush()
    path = f"investigations/{inv.id}/{role}_{sha[:10]}.{ALLOWED_TYPES[file.content_type]}"
    storage.upload_bytes(path, data, file.content_type)
    db.add(Image(investigation_id=inv.id, role=role, storage_path=path, capture_date=capture_date,
                 source=source, width=w, height=h, sha256=sha, georef_meta=georef))
    inv.status = "draft"
    db.commit()
    db.refresh(inv)
    return investigation_out(inv, storage)
