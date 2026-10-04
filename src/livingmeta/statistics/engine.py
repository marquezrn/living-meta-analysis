"""Descriptive summaries and a validated fixed-script R bridge; no AI statistics."""

import json
import math
import shutil
import statistics as stdlib_statistics
import subprocess
from collections import Counter
from pathlib import Path

from livingmeta.domain import Experiment, Protocol
from livingmeta.statistics.effects import COMPATIBILITY_FIELDS, effect_size, validate_covariance

R_PACKAGES = {"metafor": "4.8-0", "clubSandwich": "0.6.1", "jsonlite": "2.0.0"}


def descriptive(experiments: list[Experiment], protocol: Protocol) -> dict:
    groups = {}
    excluded = []
    for experiment in experiments:
        for measurement in experiment.measurements:
            if measurement.status != "accepted" or measurement.qualifier != "exact" or \
                    measurement.origin == "curve_sample":
                excluded.append({"experiment_id": experiment.id, "measurement_id": measurement.id,
                                 "reason": "Only accepted exact measurements enter numerical summaries"})
                continue
            value = measurement.normalized_value if measurement.normalized_value is not None else measurement.value
            unit = measurement.normalized_unit if measurement.normalized_value is not None else measurement.unit
            if value is None or not math.isfinite(value):
                excluded.append({"experiment_id": experiment.id, "measurement_id": measurement.id,
                                 "reason": "No finite observed value"})
                continue
            key = (measurement.outcome, unit, measurement.measurement_method, measurement.statistic,
                   measurement.time_value, measurement.time_unit, measurement.concentration_basis)
            groups.setdefault(key, []).append((value, experiment.study_family))
    summaries = []
    names = ("outcome", "unit", "measurement_method", "statistic", "time_value", "time_unit", "concentration_basis")
    for key, observations in groups.items():
        values = [v for v, _ in observations]
        summaries.append(dict(zip(names, key), n=len(values), study_families=len({s for _, s in observations}),
                              value_min=min(values), value_max=max(values),
                              mean=stdlib_statistics.mean(values), median=stdlib_statistics.median(values)))
    return {"mode": "descriptive", "protocol_version": protocol.version, "groups": summaries,
            "counts": {"experiments": len(experiments), "observations": sum(g["n"] for g in summaries),
                       "excluded": len(excluded)}, "excluded": excluded,
            "warnings": ["Means describe observed experimental conditions; they are not pooled treatment effects.",
                         "Groups with an unknown method, time, or concentration basis require interpretation."],
            "constraints": ["No variance, sample size, or missing value is imputed.",
                            "Multiple experiments from one family do not become independent studies."]}


def inferential(contrasts: list[dict], spec: dict) -> dict:
    result = {"mode": "inferential", "status": "ineligible", "effects": [], "excluded": [],
              "limitations": [], "engine": {"R_packages": R_PACKAGES}}
    missing = [field for field in COMPATIBILITY_FIELDS if not spec.get(field)]
    if missing:
        result["limitations"].append("Protocol requires " + ", ".join(missing))
        return result
    measure = spec.get("effect_measure", "MD")
    if measure not in ("MD", "ROM"):
        raise ValueError("Supported effect measures are MD and ROM")
    ids = set()
    for contrast in contrasts:
        reasons = []
        if not contrast.get("id") or contrast["id"] in ids or not contrast.get("study_family"):
            reasons.append("Unique contrast ID and study family are required")
        ids.add(contrast.get("id"))
        for field in COMPATIBILITY_FIELDS:
            if contrast.get(field) != spec[field]:
                reasons.append(f"Incompatible or unspecified {field}")
        if contrast.get("concentration_basis") != spec.get("concentration_basis"):
            reasons.append("Incompatible concentration basis")
        if contrast.get("paired") or contrast.get("arms_independent") is not True:
            reasons.append("Effect formulas require explicitly independent experimental arms")
        if any(contrast.get(name, {}).get("qualifier", "exact") != "exact" for name in ("treatment", "control")):
            reasons.append("Censored or range-valued arms cannot supply exact effects")
        try:
            effect = effect_size(contrast, measure)
        except (ValueError, KeyError, TypeError) as exc:
            reasons.append(str(exc))
        if reasons:
            result["excluded"].append({"id": contrast.get("id"), "reasons": reasons})
        else:
            result["effects"].append(effect)
    effects = result["effects"]
    if len(effects) < 2:
        result["limitations"].append("At least two eligible independent study families are required")
        return result
    counts = Counter(e["study_family"] for e in effects)
    if len(counts) < 2:
        result["limitations"].append("At least two eligible independent study families are required")
        return result
    dependent = any(count > 1 for count in counts.values())
    covariance = None
    if dependent or spec.get("covariance") is not None:
        if spec.get("covariance") is None:
            result["limitations"].append("Dependent effects require a justified sampling covariance matrix")
            return result
        try:
            covariance = validate_covariance(spec["covariance"], effects, spec.get("covariance_order", [])).tolist()
        except ValueError as exc:
            result["limitations"].append(str(exc))
            return result
        dependent = True
        if len(counts) < 3:
            result["limitations"].append("CR2 inference requires at least three study-family clusters")
            return result
        if not spec.get("covariance_justification"):
            result["limitations"].append("Covariance provenance or sensitivity assumption must be documented")
            return result
    executable = shutil.which("Rscript")
    if not executable:
        result.update(status="engine_unavailable")
        result["limitations"].append("Rscript is unavailable; run the pinned statistics Docker image")
        return result
    script = Path(__file__).parents[3] / "statistics" / "meta_analysis.R"
    # Installed wheels can use the same package-local resource without accepting user paths/code.
    if not script.exists():
        script = Path(__file__).with_name("meta_analysis.R")
    payload = {"effects": effects, "measure": measure, "dependent": dependent, "covariance": covariance,
               "packages": R_PACKAGES}
    try:
        completed = subprocess.run([executable, "--vanilla", str(script)],
                                   input=json.dumps(payload, allow_nan=False), text=True,
                                   capture_output=True, timeout=120, check=False)
        if completed.returncode != 0:
            result.update(status="engine_failed")
            result["limitations"].append(completed.stderr.strip()[-2000:] or "R engine returned an error")
            return result
        report = json.loads(completed.stdout)
        if completed.stderr.strip():
            result["limitations"].append("R engine diagnostics: " + completed.stderr.strip()[-2000:])
        if report.get("robust_df") is not None and report["robust_df"] < 4:
            result.update(status="inference_unavailable", diagnostic_estimates=report)
            result["limitations"].append("CR2 degrees of freedom are below 4; inferential intervals and p-values are suppressed")
        else:
            result.update(status="completed", pooled=report)
        if len(counts) < 5:
            result["limitations"].append("Few independent study families: interpret interval estimates cautiously")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        result.update(status="engine_failed")
        result["limitations"].append(f"R bridge failed: {type(exc).__name__}")
    return result
