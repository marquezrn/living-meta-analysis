"""Shared scientific contracts. Missing evidence is never silently imputed."""

from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScientificModel(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class RunStatus(StrEnum):
    queued = "queued"
    running = "running"
    completed = "completed"
    completed_with_abstentions = "completed_with_abstentions"
    budget_exhausted = "budget_exhausted"
    failed = "failed"
    cancelled = "cancelled"


class Evidence(ScientificModel):
    document_hash: str
    page: int = Field(ge=1)
    source_type: Literal["text", "table", "figure", "microscopy"]
    locator: str
    excerpt: str
    bounding_box: list[float] | None = None
    figure_id: str | None = None
    panel: str | None = None
    series: str | None = None
    artifact_key: str | None = None
    calibration: str | None = None
    digitization_uncertainty: float | None = None


class Attribute(ScientificModel):
    name: str
    text: str
    value: float | None = None
    unit: str | None = None
    basis: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    status: Literal["candidate", "accepted", "uncertain", "rejected"] = "candidate"
    validation_notes: list[str] = Field(default_factory=list)


class Measurement(ScientificModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    experiment_id: str
    outcome: str
    raw_value: str
    value: float | None = None
    unit: str | None = None
    normalized_value: float | None = None
    normalized_unit: str | None = None
    qualifier: Literal["exact", "lt", "le", "gt", "ge", "range", "missing"] = "exact"
    lower_bound: float | None = None
    upper_bound: float | None = None
    concentration_basis: str | None = None
    statistic: str | None = None
    measurement_method: str | None = None
    time_value: float | None = None
    time_unit: str | None = None
    uncertainty_type: Literal["SD", "SE", "CI", "unknown", "none"] = "none"
    uncertainty: float | None = None
    ci_lower: float | None = None
    ci_upper: float | None = None
    n_independent: int | None = Field(default=None, ge=1)
    n_technical: int | None = Field(default=None, ge=1)
    origin: Literal["reported", "digitized", "derived", "curve_sample"] = "reported"
    status: Literal["candidate", "accepted", "uncertain", "rejected", "stale"] = "candidate"
    missing_reason: str | None = None
    validation_notes: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def preserve_missingness(self):
        if self.qualifier == "missing" and self.value is not None:
            raise ValueError("A missing measurement cannot contain an observed value")
        return self


class Experiment(BaseModel):
    id: str
    sample_label: str
    doi: str | None = None
    study_family: str
    attributes: list[Attribute] = Field(default_factory=list)
    measurements: list[Measurement] = Field(default_factory=list)


class Condition(BaseModel):
    id: str
    experiment_id: str
    sample_label: str
    attributes: list[Attribute]
    measurement_ids: list[str]


class StudyFamily(BaseModel):
    id: str
    publication_dois: list[str]
    document_ids: list[str]
    experiment_ids: list[str]
    relationship_basis: str


class PageCoverage(BaseModel):
    page: int = Field(ge=1)
    text_status: str
    table_count: int = Field(ge=0)
    figure_count: int = Field(ge=0)
    notes: list[str] = Field(default_factory=list)


class ExtractionBatch(BaseModel):
    doi: str | None = None
    title: str | None = None
    publication_type: Literal["experimental", "review", "simulation", "unknown"] = "unknown"
    eligible: bool
    eligibility_reason: str
    experiments: list[Experiment] = Field(default_factory=list)
    coverage: list[PageCoverage] = Field(default_factory=list)
    abstentions: list[str] = Field(default_factory=list)


class Protocol(BaseModel):
    name: str = "Nanocellulose-stabilized Pickering emulsions"
    version: str = "1.0"
    topic: str = "Pickering emulsions stabilized by nanocellulose"
    inclusion: list[str] = Field(default_factory=lambda: [
        "Original experiments on oil/water Pickering emulsions containing nanocellulose",
        "Film and coating studies only when identifiable emulsion experiments are reported",
    ])
    exclusion: list[str] = Field(default_factory=lambda: [
        "Reviews are discovery sources only", "Pure simulations are not primary experimental evidence",
    ])
    variables: list[str] = Field(default_factory=lambda: [
        "Emulsion_Type", "Oil_Type", "Oil_Content_vol_percent", "Emulsification_Method",
        "Droplet_Size_um", "Stability_Days", "Electrolyte_Concentration_mM", "Type_of_Nanocellulose",
        "Modification", "Concentration_wt_percent", "Particle_L_nm", "Particle_w_nm", "Aspect_Ratio",
        "Crystallinity_Index", "Zeta_Potential_mV", "Surface_Charge_Density_e_nm2", "Surface_Energy_mJ_m2",
        "Contact_Angle_deg", "Oil_Content_wt_percent", "Surface_Charge_Density_mmol_g", "pH",
        "Temperature", "Storage_Time", "Rheology", "Preparation_Settings", "Replicates", "Uncertainty",
    ])
    queries: dict[str, str] = Field(default_factory=lambda: {
        "openalex": "Pickering nanocellulose",
        "crossref": "Pickering nanocellulose",
        "pubmed": '(Pickering[Title/Abstract]) AND (nanocellulose[Title/Abstract] OR "cellulose nanocrystals"[Title/Abstract] OR "cellulose nanofibrils"[Title/Abstract])',
        "europepmc": 'Pickering AND (nanocellulose OR "cellulose nanocrystals" OR "cellulose nanofibrils")',
        "scopus": 'TITLE-ABS-KEY(Pickering AND (nanocellulose OR "cellulose nanocrystals" OR "cellulose nanofibrils"))',
        "arxiv": 'all:Pickering AND (all:nanocellulose OR all:"cellulose nanocrystals")',
    })
    enabled_sources: list[str] = Field(default_factory=lambda: ["openalex", "crossref", "pubmed", "europepmc"])
    analysis_mode: Literal["descriptive", "inferential"] = "descriptive"
    outcome: str = "Droplet_Size_um"
    unit: str | None = None
    effect_measure: Literal["MD", "ROM"] = "MD"
    outcome_definition: str | None = None
    measurement_method: str | None = None
    comparator: str | None = None
    time_point: str | None = None
    concentration_basis: str | None = None


class Citation(BaseModel):
    source: str
    source_id: str
    doi: str | None = None
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    url: str | None = None
    abstract: str | None = None
    full_text_url: str | None = None
    license: str | None = None
    version: str | None = None
    is_preprint: bool = False
    status: Literal["active", "corrected", "retracted", "concern", "withdrawn"] = "active"
    related_dois: list[str] = Field(default_factory=list)
    updated_at: str | None = None
