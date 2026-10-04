"""Compare original manual records; agreement is not source-verified accuracy."""

import math
import re
from collections import defaultdict

import pint

from livingmeta.benchmark.reference import family_split
from livingmeta.discovery.identity import canonical_doi
from livingmeta.domain import Experiment

CONDITIONS = ("Emulsion_Type", "Oil_Type", "Oil_Content_vol_percent", "Oil_Content_wt_percent",
              "Emulsification_Method", "Type_of_Nanocellulose", "Type_of_Nanoparticle", "Modification",
              "Concentration_wt_percent", "Electrolyte_Concentration_mM", "pH", "Temperature")
FIELD_UNITS = {"Droplet_Size_um": "um", "Particle_L_nm": "nm", "Particle_w_nm": "nm",
               "Electrolyte_Concentration_mM": "mmol/liter", "Zeta_Potential_mV": "mV",
               "Surface_Energy_mJ_m2": "mJ/meter**2", "Contact_Angle_deg": "degree",
               "Stability_Days": "day", "Surface_Charge_Density_mmol_g": "mmol/g",
               "Oil_Content_vol_percent": "%", "Oil_Content_wt_percent": "%",
               "Concentration_wt_percent": "%", "Aspect_Ratio": "dimensionless",
               "Crystallinity_Index": "dimensionless"}
REGISTRY = pint.UnitRegistry()


def _cell(value, unit=None) -> dict:
    qualifier = "exact"
    numeric = None
    if value is not None:
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            numeric = float(value)
        elif isinstance(value, str):
            match = re.fullmatch(r"\s*(<=|>=|<|>|≤|≥)?\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*", value)
            if match:
                qualifier = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge", "≤": "le", "≥": "ge"}.get(match[1], "exact")
                numeric = float(match[2])
    return {"raw": value, "value": numeric, "unit": unit, "qualifier": qualifier}


def _candidate(experiment: Experiment) -> dict:
    fields = {}
    for attribute in experiment.attributes:
        if attribute.status == "rejected":
            continue
        if attribute.name in fields:
            fields[attribute.name] = {"ambiguous": True, "raw": None, "value": None, "unit": None, "qualifier": "missing"}
            continue
        fields[attribute.name] = _cell(attribute.text, attribute.unit)
        if attribute.value is not None and fields[attribute.name]["qualifier"] == "exact":
            fields[attribute.name]["value"] = attribute.value
        fields[attribute.name]["basis"] = attribute.basis
        fields[attribute.name]["status"] = attribute.status
    for measurement in experiment.measurements:
        if measurement.status != "accepted" or measurement.origin == "curve_sample":
            continue
        name = measurement.outcome
        # Duplicate measurements with the same outcome are never selected arbitrarily.
        if name in fields:
            fields[name] = {"ambiguous": True, "raw": None, "value": None, "unit": None, "qualifier": "missing"}
            continue
        fields[name] = _cell(measurement.raw_value, measurement.unit)
        fields[name].update(qualifier=measurement.qualifier, basis=measurement.concentration_basis)
        if measurement.normalized_value is not None:
            fields[name].update(value=measurement.normalized_value, unit=measurement.normalized_unit)
        elif measurement.value is not None:
            fields[name]["value"] = measurement.value
    return fields


def _agreement(candidate: dict, reference, field: str) -> tuple[bool, float | None, str]:
    if candidate.get("ambiguous"):
        return False, None, "ambiguous_candidate_field"
    target = _cell(reference, FIELD_UNITS.get(field))
    if candidate["qualifier"] != target["qualifier"]:
        return False, None, "qualifier_mismatch"
    if candidate["value"] is not None and target["value"] is not None:
        actual = candidate["value"]
        source_unit = candidate.get("unit")
        target_unit = target.get("unit")
        expected_basis = "mass" if field in ("Concentration_wt_percent", "Oil_Content_wt_percent") else \
            "volume" if field == "Oil_Content_vol_percent" else None
        if expected_basis and candidate.get("basis") not in (expected_basis, "w/w" if expected_basis == "mass" else "v/v"):
            return False, None, "concentration_basis_unverified"
        if target_unit and not source_unit:
            return False, None, "candidate_unit_missing"
        if source_unit and target_unit:
            try:
                actual = REGISTRY.Quantity(actual, source_unit).to(target_unit).magnitude
            except (pint.UndefinedUnitError, pint.DimensionalityError, ValueError):
                return False, None, "unit_mismatch"
        error = abs(actual - target["value"])
        equal = math.isclose(actual, target["value"], rel_tol=0.02, abs_tol=1e-8)
        return equal, error, "within_registered_2_percent_tolerance" if equal else "numerical_disagreement"
    actual_text = str(candidate.get("raw", "")).strip().casefold()
    expected_text = str(reference).strip().casefold()
    return actual_text == expected_text, None, "literal_text_comparison"


def compare(experiments: list[Experiment], reference: list[dict], available_dois: list[str] | set[str]) -> dict:
    available = {d for value in available_dois if (d := canonical_doi(value))}
    rows_by_doi = defaultdict(list)
    for index, row in enumerate(reference):
        if row.get("_reference_origin", "manual_original") != "manual_original":
            raise ValueError("Only original manual reference values are accepted; imputed rows are excluded")
        rows_by_doi[canonical_doi(row.get("DOI"))].append((row.get("_reference_row_id", index + 1), row))
    possible = {}
    candidates = {}
    for experiment in experiments:
        doi = canonical_doi(experiment.doi)
        if doi not in available:
            continue
        fields = _candidate(experiment)
        candidates[experiment.id] = fields
        matches = []
        initial_rows = rows_by_doi.get(doi, [])
        for row_id, row in initial_rows:
            shared = [field for field in CONDITIONS if field in fields and row.get(field) is not None]
            if shared and not all(_agreement(fields[field], row[field], field)[0] for field in shared):
                continue
            if len(initial_rows) > 1 and len(shared) < 2:
                continue
            matches.append(row_id)
        possible[experiment.id] = matches
    # One-to-one alignment only. No greedy matching that resolves uncertainty by input order.
    reverse = defaultdict(list)
    for experiment_id, row_ids in possible.items():
        for row_id in row_ids:
            reverse[row_id].append(experiment_id)
    matched = {}
    ambiguous = []
    unmatched = []
    for experiment_id, row_ids in possible.items():
        if len(row_ids) == 1 and len(reverse[row_ids[0]]) == 1:
            matched[experiment_id] = row_ids[0]
        elif row_ids:
            ambiguous.append({"experiment_id": experiment_id, "candidate_reference_rows": row_ids})
        else:
            unmatched.append(experiment_id)
    rows = {row_id: row for values in rows_by_doi.values() for row_id, row in values}
    comparisons = []
    additions = []
    numeric_errors = []
    matched_reference = set(matched.values())
    for experiment_id, row_id in matched.items():
        row = rows[row_id]
        fields = candidates[experiment_id]
        for field, reference_value in row.items():
            if field.startswith("_") or field == "DOI" or reference_value is None:
                continue
            candidate = fields.get(field)
            if candidate is None:
                equal, error, reason = False, None, "missing_candidate"
            else:
                equal, error, reason = _agreement(candidate, reference_value, field)
            if error is not None:
                numeric_errors.append(error)
            comparisons.append({"experiment_id": experiment_id, "reference_row_id": row_id, "field": field,
                                "manual_value": reference_value,
                                "candidate": candidate, "agreement": equal, "absolute_error": error,
                                "reason": reason, "adjudication": "pending_primary_source_review"})
        additions.extend({"experiment_id": experiment_id, "field": field, "candidate": candidate,
                          "verification": "not_adjudicated"} for field, candidate in fields.items()
                         if row.get(field) is None and candidate.get("raw") is not None)
    available_rows = [row for row in reference if canonical_doi(row.get("DOI")) in available]
    missing_dois = sorted(doi for doi in rows_by_doi if doi and doi not in available)
    exact = sum(c["agreement"] for c in comparisons)
    evaluated = len(comparisons)
    denominator = sum(value is not None for row in available_rows for field, value in row.items()
                      if not field.startswith("_") and field != "DOI")
    errors_by_field = defaultdict(list)
    for comparison in comparisons:
        if comparison["absolute_error"] is not None:
            errors_by_field[comparison["field"]].append(comparison["absolute_error"])
    field_error = {field: {"unit": FIELD_UNITS.get(field), "count": len(errors),
                          "mean_absolute_error": sum(errors) / len(errors), "max_absolute_error": max(errors)}
                   for field, errors in sorted(errors_by_field.items())}
    return {"label": "agreement_with_manual_reference", "status": "completed_with_limitations" if experiments else "not_run",
            "counts": {"reference_rows": len(reference), "reference_dois": len(rows_by_doi),
                       "available_reference_rows": len(available_rows),
                       "available_dois": len(set(rows_by_doi) & available), "matched": len(matched),
                       "ambiguous": len(ambiguous), "unmatched": len(unmatched),
                       "missing_source_rows": len(reference) - len(available_rows)},
            "agreement": {"exact_matches": exact, "compared_fields": evaluated,
                          "proportion": exact / evaluated if evaluated else None,
                          "recovery_agreement": exact / denominator if denominator else None,
                          "numerical_mae": sum(numeric_errors) / len(numeric_errors) if numeric_errors else None,
                          "numerical_error_by_field": field_error,
                          "relative_tolerance": 0.02, "verified_precision": None, "verified_recall": None},
            "coverage": {"matched_reference_rows": len(matched_reference),
                         "unmatched_available_reference_rows": len(available_rows) - len(matched_reference),
                         "manual_nonmissing_fields": denominator},
            "ambiguous_matches": ambiguous, "unmatched_experiments": unmatched,
            "field_comparisons": comparisons, "potential_additions": additions,
            "missing_source_dois": missing_dois,
            "split": [{"study_family": family, "partition": family_split(family)}
                      for family in sorted({row.get("_study_family", row["DOI"]) for row in reference})],
            "limitations": ["Manual agreement is not independently adjudicated extraction accuracy.",
                            "Source-verified precision, recall, and verified additions remain unevaluated.",
                            "Numerical MAE combines different field units; inspect per-field errors.",
                            "Original nulls and inequalities are preserved; interface imputations are not imported."]}
