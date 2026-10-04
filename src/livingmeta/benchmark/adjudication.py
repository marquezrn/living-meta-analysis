"""Metrics from independent primary-source adjudication, separate from manual agreement."""

import math
from collections import defaultdict

import numpy as np


def _binomial(successes: int, trials: int) -> dict:
    if not trials:
        return {"estimate": None, "denominator": 0, "wilson_95_ci": None}
    z = 1.959963984540054
    proportion = successes / trials
    denominator = 1 + z * z / trials
    centre = (proportion + z * z / (2 * trials)) / denominator
    half_width = z * math.sqrt(proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)) / denominator
    return {"estimate": proportion, "denominator": trials,
            "wilson_95_ci": [max(0, centre - half_width), min(1, centre + half_width)]}


def _family_intervals(decisions: list[dict], samples: int = 1000) -> dict:
    groups = defaultdict(list)
    for decision in decisions:
        groups[decision["study_family"]].append(decision)
    families = sorted(groups)
    if len(families) < 2:
        return {"status": "unavailable", "reason": "At least two independently adjudicated study families are required"}
    rng = np.random.default_rng(2026)
    precision, recall = [], []
    for _ in range(samples):
        resampled = [decision for family in rng.choice(families, size=len(families), replace=True)
                     for decision in groups[family]]
        accepted = [d for d in resampled if d["accepted"]]
        recoverable = [d for d in resampled if d["recoverable"]]
        if accepted:
            precision.append(sum(d["correct"] for d in accepted) / len(accepted))
        if recoverable:
            recall.append(sum(d["accepted"] and d["correct"] for d in recoverable) / len(recoverable))
    return {"status": "estimated", "method": "study-family percentile bootstrap", "replicates": samples,
            "seed": 2026, "study_families": len(families),
            "accepted_precision_95_ci": np.quantile(precision, [0.025, 0.975]).tolist() if precision else None,
            "recoverable_recall_95_ci": np.quantile(recall, [0.025, 0.975]).tolist() if recall else None,
            "limitation": "Few families or uniformly successful fields can produce unstable or degenerate intervals"}


def adjudicated_metrics(decisions: list[dict], total_cost_usd: float | None = None) -> dict:
    """One decision per recoverability-labelled field, including absent/abstained candidates.

    Each completed decision needs a reviewer, primary-source locator, explicit correctness,
    source type, accepted flag, and recoverability flag. No agent confidence serves as truth.
    """
    seen = set()
    complete = []
    pending = []
    for decision in decisions:
        key = (decision.get("study_family"), decision.get("experiment_id"), decision.get("field"))
        if any(part is None for part in key) or key in seen:
            raise ValueError("Every adjudication needs a unique study-family, experiment, and field key")
        seen.add(key)
        if (not decision.get("reviewer") or not decision.get("source_locator") or
                decision.get("correct") not in (True, False) or
                decision.get("recoverable") not in (True, False) or
                decision.get("accepted") not in (True, False)):
            pending.append(key)
            continue
        if decision.get("source_type") not in ("text", "table", "figure", "microscopy"):
            raise ValueError("Adjudicated source type must be text, table, figure, or microscopy")
        complete.append(decision)
    accepted = [d for d in complete if d["accepted"]]
    recoverable = [d for d in complete if d["recoverable"]]
    recovered = [d for d in recoverable if d["accepted"] and d["correct"]]
    sources = defaultdict(list)
    for decision in recoverable:
        category = "text_table" if decision["source_type"] in ("text", "table") else decision["source_type"]
        sources[category].append(decision)
    strata = {category: _binomial(sum(d["accepted"] and d["correct"] for d in entries), len(entries))
              for category, entries in sources.items()}
    return {"label": "independently_source_adjudicated_metrics",
            "status": "not_run" if not decisions else "completed" if not pending else "partial_adjudication",
            "accepted_field_precision": _binomial(sum(d["correct"] for d in accepted), len(accepted)),
            "recoverable_field_recall": _binomial(len(recovered), len(recoverable)),
            "source_recall": strata,
            "family_clustered_intervals": _family_intervals(complete),
            "abstention_rate": _binomial(sum(d.get("abstained", False) for d in complete), len(complete)),
            "verified_additions": sum(d["accepted"] and d["correct"] and d.get("additional_to_manual", False)
                                      for d in complete),
            "adjudicated_fields": len(complete), "pending_fields": len(pending),
            "total_cost_usd": total_cost_usd,
            "targets": {"accepted_field_precision": 0.98, "text_table_recall": 0.95, "figure_recall": 0.90},
            "target_attainment": {"precision": (sum(d["correct"] for d in accepted) / len(accepted) >= 0.98)
                                  if accepted else None,
                                  "text_table": strata.get("text_table", {}).get("estimate", 0) >= 0.95
                                  if "text_table" in strata else None,
                                  "figure": strata.get("figure", {}).get("estimate", 0) >= 0.90
                                  if "figure" in strata else None},
            "limitations": ["Wilson intervals treat fields as independent; use the accompanying family bootstrap when enough adjudicated families exist.",
                            "Pending adjudications are excluded from estimates and reported explicitly."]}
