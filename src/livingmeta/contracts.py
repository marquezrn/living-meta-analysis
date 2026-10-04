"""Public API response schemas; internal object keys and authentication tokens are excluded."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .domain import Citation, Measurement, Protocol


class UTCResponse(BaseModel):
    @field_validator("*", mode="before")
    @classmethod
    def preserve_utc(cls, value):
        # SQLite loses tzinfo; database timestamps are stored in UTC.
        return value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value


class ProjectView(UTCResponse):
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


class RunView(UTCResponse):
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


class DocumentView(UTCResponse):
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


class PublicationView(Citation, UTCResponse):
    id: str
    state: str
    sources: list[str] = Field(default_factory=list)
    discovered_at: datetime
