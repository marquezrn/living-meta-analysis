"""Derive inferential inputs from accepted evidence instead of trusting client numbers."""

import math

from fastapi import HTTPException

from .db import Document, ExperimentRecord, MeasurementRecord
from .domain import Measurement
from .extraction.normalization import UNITS, parse_unit


def evidence_contrasts(db, project_id, proposals, spec):
    output = []
    for proposal in proposals:
        records = []
        arms = {}
        for name in ("treatment", "control"):
            mid = proposal.get(f"{name}_measurement_id")
            record = db.get(MeasurementRecord, mid) if mid else None
            if not record or record.project_id != project_id or mid not in proposal.get("source_measurement_ids", []):
                raise HTTPException(422, f"{name}: select a linked source measurement")
            document, experiment = db.get(Document, record.document_id), db.get(ExperimentRecord, record.experiment_id)
            m = Measurement.model_validate(record.payload)
            if document.status != "extracted" or experiment.source_status != "active" or m.status != "accepted":
                raise HTTPException(422, f"{name}: source evidence is not active and accepted")
            if (m.qualifier != "exact" or m.origin == "curve_sample" or m.statistic not in ("mean", "arithmetic_mean")
                    or m.uncertainty_type != "SD" or m.uncertainty is None or m.uncertainty < 0
                    or m.n_independent is None or m.n_independent < 2 or m.value is None or not m.evidence):
                raise HTTPException(422, f"{name}: a reported exact mean, SD, and independent n >= 2 are required")
            if any(e.document_hash != document.sha256 for e in m.evidence):
                raise HTTPException(422, f"{name}: measurement evidence must match the frozen primary source")
            attrs = {a["name"]: a["text"] for a in experiment.payload.get("attributes", [])}
            attr_records = {a["name"]: a for a in experiment.payload.get("attributes", [])}
            for key in ("outcome_definition", "comparator", "arms_independent"):
                definitions = [a for a in experiment.payload.get("attributes", []) if a["name"] == key]
                if len(definitions) != 1:
                    raise HTTPException(422, f"{name}: {key} must have one unambiguous condition definition")
                attr = attr_records.get(key, {})
                if attr.get("status") != "accepted" or not attr.get("evidence"):
                    raise HTTPException(422, f"{name}: {key} requires accepted primary-source condition evidence")
                if any(e.get("document_hash") != document.sha256 for e in attr["evidence"]):
                    raise HTTPException(422, f"{name}: condition evidence must match the frozen primary source")
            time = f"{m.time_value:g} {m.time_unit}" if m.time_value is not None and m.time_unit else None
            if (m.outcome != spec.get("outcome") or m.measurement_method != spec.get("measurement_method")
                    or m.concentration_basis != spec.get("concentration_basis")
                    or (attrs.get("time_point") or time) != spec.get("time_point")
                    or attrs.get("outcome_definition") != spec.get("outcome_definition")
                    or attrs.get("comparator") != spec.get("comparator")):
                raise HTTPException(422, f"{name}: source definitions, method, time, basis, or comparator differ from the protocol")
            try:
                source_unit, target_unit = parse_unit(m.unit), parse_unit(spec["unit"])
                mean = float(UNITS.Quantity(m.value, source_unit).to(target_unit).magnitude)
                # Difference of two converted quantities preserves offset-unit SD semantics.
                sd = abs(float(UNITS.Quantity(m.value + m.uncertainty, source_unit).to(target_unit).magnitude) - mean)
            except Exception:
                raise HTTPException(422, f"{name}: source units cannot be converted to the analysis unit") from None
            arm = {"mean": mean, "sd": sd, "n_independent": m.n_independent, "qualifier": "exact"}
            supplied = proposal.get(name, {})
            if any(k in supplied and (not isinstance(supplied[k], (float, int)) or not math.isclose(supplied[k], arm[k], rel_tol=1e-8, abs_tol=1e-10))
                   for k in ("mean", "sd", "n_independent")):
                raise HTTPException(422, f"{name}: supplied numbers disagree with accepted primary evidence")
            arms[name], records = arm, records + [experiment]
        if records[0].id == records[1].id or proposal["treatment_measurement_id"] == proposal["control_measurement_id"]:
            raise HTTPException(422, "A contrast needs distinct experimental arms")
        families = {e.payload["study_family"] for e in records}
        if len(families) != 1 or proposal.get("study_family") not in (None, next(iter(families))):
            raise HTTPException(422, "Treatment and control must belong to the same verified study family")
        if any({a["name"]: a["text"] for a in e.payload.get("attributes", [])}.get("arms_independent") != "true" for e in records):
            raise HTTPException(422, "Source experiment metadata must explicitly establish independent arms")
        output.append({**proposal, **spec, **arms, "id": proposal.get("id"),
                       "study_family": next(iter(families)), "arms_independent": True})
    return output
