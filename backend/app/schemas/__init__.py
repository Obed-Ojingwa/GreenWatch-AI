from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ALLOWED_CATEGORIES = ("buildings", "trees", "vegetation")


class InvestigationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    location_name: str = Field(min_length=1, max_length=200)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    objective: str = Field(min_length=1, max_length=1000)
    notes: str | None = Field(default=None, max_length=2000)


class ImageOut(BaseModel):
    id: uuid.UUID
    role: str
    capture_date: date | None
    source: str | None
    width: int
    height: int
    georeferenced: bool
    url: str


class InvestigationOut(BaseModel):
    id: uuid.UUID
    title: str
    location_name: str
    latitude: float | None
    longitude: float | None
    objective: str
    notes: str | None
    is_sample: bool
    status: str
    created_at: datetime
    images: list[ImageOut] = []
    latest_run_id: uuid.UUID | None = None


class AgentRunCreate(BaseModel):
    categories: list[Literal["buildings", "trees", "vegetation"]] = ["buildings"]


class StepOut(BaseModel):
    idx: int
    tool: str
    status: str
    input: Any = None
    output: Any = None
    duration_ms: int | None


class SegOut(BaseModel):
    id: uuid.UUID
    role: str
    category: str
    prompt_used: str
    attempt: int
    pixel_count: int | None
    coverage: float | None
    qc_status: str | None
    qc_detail: Any = None
    from_cache: bool
    overlay_url: str | None
    mask_url: str | None


class ReportOut(BaseModel):
    id: uuid.UUID
    kind: str
    url: str
    created_at: datetime


class TaskOut(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    run_id: uuid.UUID | None
    investigation_title: str | None = None
    title: str
    reason: str
    priority: str
    status: str
    created_at: datetime


class AgentRunOut(BaseModel):
    id: uuid.UUID
    investigation_id: uuid.UUID
    status: str
    categories: list[str]
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    validation: Any = None
    metrics: Any = None
    findings: Any = None
    interpretation: Any = None
    steps: list[StepOut] = []
    segmentations: list[SegOut] = []
    prepared_urls: dict[str, str] = {}
    diff_urls: dict[str, str] = {}
    report: ReportOut | None = None
    tasks: list[TaskOut] = []
    model_info: Any = None


class ApproveTaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=800)
    priority: Literal["low", "medium", "high"] = "medium"


class TaskPatch(BaseModel):
    status: Literal["open", "in_progress", "done"]


class StatsOut(BaseModel):
    investigations: int
    awaiting_review: int
    completed: int
    open_tasks: int
