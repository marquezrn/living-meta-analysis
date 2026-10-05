"""Deterministic local synthesis derived from retained primary-source measurements."""

import math
from pathlib import Path

from livingmeta.domain import Experiment, Measurement, Protocol
from livingmeta.extraction.normalization import UNITS, parse_unit
from livingmeta.statistics.engine import descriptive, inferential

from .contracts import LocalWorkflowError
from .workspace import (atomic_json, dataset_signature, event, load_dataset, load_manifest,
                        now, read_json, safe_path, workspace_lock)


def derive_contrasts(experiments, documents, proposals, spec):
    """Resolve only source IDs; supplied effect means, SDs and n are never trusted."""
    records = {m["id"]: (experiment, m) for experiment in experiments for m in experiment["measurements"]}
    hashes = {document["sha256"]: document for document in documents}
    output = []
    if not isinstance(proposals, list) or any(not isinstance(proposal, dict) for proposal in proposals):
        raise LocalWorkflowError("Contrasts must be an array of source-linked objects")
    for proposal in proposals:
        arms, families, experiment_ids = {}, set(), []
        for name in ("treatment", "control"):
            measurement_id = proposal.get(f"{name}_measurement_id")
            if measurement_id not in records:
                raise LocalWorkflowError(f"{name}: a retained source measurement ID is required")
            experiment, raw = records[measurement_id]
            measurement = Measurement.model_validate(raw)
            if (measurement.status != "accepted" or measurement.qualifier != "exact"
                    or measurement.origin == "curve_sample" or measurement.statistic not in {"mean", "arithmetic_mean"}
                    or measurement.uncertainty_type != "SD" or measurement.uncertainty is None
                    or measurement.uncertainty < 0 or measurement.n_independent is None
                    or measurement.n_independent < 2 or measurement.value is None or not measurement.evidence):
                raise LocalWorkflowError(f"{name}: accepted exact mean, reported SD and independent n >= 2 are required")
            if any(e.document_hash not in hashes or hashes[e.document_hash].get("status") != "completed"
                   for e in measurement.evidence):
                raise LocalWorkflowError(f"{name}: source evidence is not complete and active")
            attributes = {}
            for key in ("outcome_definition", "comparator", "arms_independent"):
                values = [a for a in experiment.get("attributes", []) if a["name"] == key]
                if len(values) != 1 or values[0].get("status") != "accepted" or not values[0].get("evidence"):
                    raise LocalWorkflowError(f"{name}: {key} needs one accepted source-supported definition")
                if any(e["document_hash"] not in {source.document_hash for source in measurement.evidence}
                       for e in values[0]["evidence"]):
                    raise LocalWorkflowError(f"{name}: condition evidence must belong to the measurement's primary source")
                attributes[key] = values[0]["text"]
            time = f"{measurement.time_value:g} {measurement.time_unit}" if measurement.time_value is not None and measurement.time_unit else None
            if (measurement.outcome != spec.get("outcome") or measurement.measurement_method != spec.get("measurement_method")
                    or measurement.concentration_basis != spec.get("concentration_basis") or time != spec.get("time_point")
                    or attributes["outcome_definition"] != spec.get("outcome_definition")
                    or attributes["comparator"] != spec.get("comparator") or attributes["arms_independent"] != "true"):
                raise LocalWorkflowError(f"{name}: source definitions, comparator, independent arms, method, time or basis differ")
            try:
                source_unit, target_unit = parse_unit(measurement.unit), parse_unit(spec["unit"])
                mean = float(UNITS.Quantity(measurement.value, source_unit).to(target_unit).magnitude)
                sd = abs(float(UNITS.Quantity(measurement.value + measurement.uncertainty, source_unit).to(target_unit).magnitude) - mean)
            except Exception:
                raise LocalWorkflowError(f"{name}: source units cannot be converted to the registered unit") from None
            arms[name] = {"mean": mean, "sd": sd, "n_independent": measurement.n_independent, "qualifier": "exact"}
            for key, value in proposal.get(name, {}).items():
                if key in arms[name] and isinstance(value, (int, float)) and not math.isclose(value, arms[name][key]):
                    raise LocalWorkflowError("Proposed arm numbers disagree with retained source measurements")
            families.add(experiment["study_family"])
            experiment_ids.append(experiment["id"])
        if len(families) != 1 or experiment_ids[0] == experiment_ids[1]:
            raise LocalWorkflowError("A contrast needs distinct experimental arms in the same study family")
        output.append({**proposal, **spec, **arms, "study_family": next(iter(families)), "arms_independent": True})
    return output


def synthesize_workspace(workspace: Path):
    with workspace_lock(workspace):
        manifest, dataset = load_manifest(workspace), load_dataset(workspace)
        from .workflow import _engine_hash, source_signature
        if manifest["engine_hash"] != _engine_hash():
            raise LocalWorkflowError("Scientific engine changed before synthesis")
        for document in manifest["documents"]:
            if source_signature(workspace, document) != document.get("source_signature"):
                raise LocalWorkflowError("Source bundle changed before synthesis")
        protocol = Protocol.model_validate(manifest["protocol"])
        experiments = [Experiment.model_validate(experiment) for experiment in dataset["experiments"]]
        result = {"schema_version": 2, "descriptive": descriptive(experiments, protocol),
                  "protocol_hash": manifest["protocol_hash"], "engine_hash": manifest["engine_hash"],
                  "dataset_hash": dataset_signature(dataset),
                  "synthesis_stale": manifest.get("synthesis_stale", True), "created_at": now()}
        if protocol.analysis_mode == "inferential":
            path = safe_path(workspace, "analysis.json")
            if not path.exists():
                result["inferential"] = {"status": "ineligible", "limitations": [
                    "Register explicit source-linked contrast IDs in analysis.json; arms are not guessed"]}
            else:
                analysis = read_json(path)
                spec = {key: getattr(protocol, key) for key in ("outcome", "outcome_definition", "measurement_method",
                    "comparator", "time_point", "concentration_basis", "unit", "effect_measure")}
                # Covariance remains a documented, protocol-specific scientific input.
                spec.update({key: analysis[key] for key in ("covariance", "covariance_order", "covariance_justification") if key in analysis})
                try:
                    contrasts = derive_contrasts(dataset["experiments"], manifest["documents"], analysis.get("contrasts", []), spec)
                    result["inferential"] = inferential(contrasts, spec)
                except LocalWorkflowError as error:
                    result["inferential"] = {"status": "ineligible", "limitations": [str(error)]}
        atomic_json(safe_path(workspace, "synthesis.json"), result)
        manifest["last_synthesis_update"] = result["created_at"]
        atomic_json(safe_path(workspace, "manifest.json"), manifest)
        event(workspace, "synthesis_computed", mode=protocol.analysis_mode,
              inferential_status=result.get("inferential", {}).get("status"), model_calls=0)
    return result
