"""Sampling effects from independently replicated experimental arms."""

import math

import numpy as np

COMPATIBILITY_FIELDS = ("outcome_definition", "measurement_method", "comparator", "time_point", "unit")


def effect_size(contrast: dict, measure: str = "MD") -> dict:
    arms = []
    for name in ("treatment", "control"):
        arm = contrast.get(name, {})
        mean, sd, n = arm.get("mean"), arm.get("sd"), arm.get("n_independent")
        if any(v is None for v in (mean, sd, n)):
            raise ValueError(f"{name}: mean, SD, and independent sample size must be reported")
        if isinstance(n, bool) or not isinstance(n, int) or n < 2:
            raise ValueError(f"{name}: independent sample size must be an integer of at least 2")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (mean, sd)) or sd < 0:
            raise ValueError(f"{name}: finite mean and nonnegative SD required")
        arms.append((float(mean), float(sd), n))
    (mt, st, nt), (mc, sc, nc) = arms
    if measure == "MD":
        yi, vi = mt - mc, st * st / nt + sc * sc / nc
    elif measure == "ROM":
        if mt <= 0 or mc <= 0:
            raise ValueError("Log response ratios require positive means")
        yi = math.log(mt) - math.log(mc)
        try:
            vi = (st / mt) ** 2 / nt + (sc / mc) ** 2 / nc
        except (OverflowError, ZeroDivisionError) as exc:
            raise ValueError("Sampling variance is not numerically representable") from exc
    else:
        raise ValueError("Supported effect measures are MD and ROM")
    if vi <= 0 or not math.isfinite(vi) or not math.isfinite(yi):
        raise ValueError("A positive sampling variance is required; zero variance is not fabricated")
    return {"id": contrast["id"], "study_family": contrast["study_family"], "yi": yi, "vi": vi,
            "effect_measure": measure}


def validate_covariance(matrix: list[list[float]], effects: list[dict], order: list[str]) -> np.ndarray:
    if order != [e["id"] for e in effects]:
        raise ValueError("Covariance order must exactly match retained effect IDs")
    value = np.asarray(matrix, dtype=float)
    count = len(effects)
    if value.shape != (count, count) or not np.all(np.isfinite(value)):
        raise ValueError("Covariance must be a finite square matrix matching retained effects")
    if not np.allclose(value, value.T, rtol=1e-8, atol=1e-12):
        raise ValueError("Covariance must be symmetric")
    if not np.allclose(np.diag(value), [e["vi"] for e in effects], rtol=1e-6, atol=1e-12):
        raise ValueError("Covariance diagonal must equal calculated sampling variances")
    if np.min(np.linalg.eigvalsh(value)) <= 0:
        raise ValueError("Covariance must be positive definite")
    for i in range(count):
        for j in range(i):
            if effects[i]["study_family"] != effects[j]["study_family"] and abs(value[i, j]) > 1e-12:
                raise ValueError("Between-family covariance is incompatible with family-clustered inference")
    return value
