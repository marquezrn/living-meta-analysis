"""Versioned file jobs. Agent-written data never controls paths or execution."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from livingmeta.domain import Condition, Experiment, Protocol, StudyFamily

SCHEMA_VERSION = 2
PHASES = ("screening", "text_table", "figure", "verification", "resolution")
MAX_ATTEMPTS = 2
MAX_RESPONSE_BYTES = 5_000_000


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal[2]
    id: str
    name: str
    protocol: Protocol
    protocol_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    engine_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_version: str
    documents: list[dict[str, Any]]
    status: Literal["ready", "running", "paused", "failed", "cancelled", "completed", "completed_with_abstentions"]
    created_at: str
    updated_at: str
    last_metadata_check: str | None = None
    last_extraction: str | None = None
    last_synthesis_update: str | None = None
    synthesis_stale: bool = True
    agent_usage: dict[str, Any]


class EvidenceDataset(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal[2]
    experiments: list[Experiment]
    publications: list[dict[str, Any]]
    limitations: list[str]
    conditions: list[Condition] = Field(default_factory=list)
    study_families: list[StudyFamily] = Field(default_factory=list)


class ReportSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal[2]
    project: dict[str, Any]
    protocol: Protocol
    documents: list[dict[str, Any]]
    experiments: list[Experiment]
    conditions: list[Condition] = Field(default_factory=list)
    study_families: list[StudyFamily] = Field(default_factory=list)
    publications: list[dict[str, Any]]
    freshness: dict[str, Any]
    coverage: dict[str, Any]
    synthesis: dict[str, Any]
    benchmark: dict[str, Any]
    run: dict[str, Any]
    provenance: dict[str, Any]
    artifacts: list[dict[str, Any]]
    embedded_artifacts: list[dict[str, Any]]


class FileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[2] = 2
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    result: dict[str, Any]
    agent: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    claim_token: str = Field(pattern=r"^[a-f0-9]{32}$")


class JobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: Literal[2] = 2
    id: str
    request_hash: str
    document_hash: str
    phase: Literal["screening", "text_table", "figure", "verification", "resolution"]
    inputs: list[str]
    images: list[str]
    input_hashes: dict[str, str]
    instructions: str
    response_schema: dict[str, Any]
    payload: dict[str, Any]


class LocalWorkflowError(ValueError):
    """A rejected response or unsafe workspace leaves existing evidence intact."""
