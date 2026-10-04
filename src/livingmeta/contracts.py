"""Public API response schemas; internal object keys and authentication tokens are excluded."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .domain import Citation, Measurement, Protocol


class ProjectView(BaseModel):
    id: str
    name: str
    protocol: Protocol
    role: Literal["owner", "editor", "reader"] | None = None
    created_at: datetime
    last_metadata_check: datetime | None
    last_extraction: datetime | None
    last_synthesis_update: datetime | None
    next_metadata_check: datetime | None
    pending_count: int
    synthesis_stale: bool
    discovery_report: dict


class RunView(BaseModel):
    id: str
    project_id: str
    status: Literal["queued", "running", "completed", "completed_with_abstentions", "budget_exhausted", "cancelled", "failed"]
    budget_usd: float
    spent_usd: float
    reserved_usd: float
    progress: dict
    result: dict
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    cancel_requested: bool


class DocumentView(BaseModel):
    id: str
    filename: str
    sha256: str
    doi: str | None
    status: str
    pages: int | None
    error: str | None
    created_at: datetime


class MeasurementView(Measurement):
    document_id: str
    source_status: str = "active"


class PublicationView(Citation):
    id: str
    state: str
    sources: list[str] = Field(default_factory=list)
    discovered_at: datetime
