import json
import math
import shutil
from types import SimpleNamespace

import pytest

from livingmeta.domain import Experiment, Measurement, Protocol
from livingmeta.statistics import descriptive, effect_size, inferential, validate_covariance


def contrast(identifier="a", family="family-a"):
    return {"id": identifier, "study_family": family, "arms_independent": True,
            "outcome_definition": "volume-weighted diameter", "measurement_method": "laser diffraction",
            "comparator": "unmodified CNC", "time_point": "day 1", "unit": "um",
            "treatment": {"mean": 20, "sd": 2, "n_independent": 4},
            "control": {"mean": 10, "sd": 3, "n_independent": 9}}


def specification():
    return {"outcome_definition": "volume-weighted diameter", "measurement_method": "laser diffraction",
            "comparator": "unmodified CNC", "time_point": "day 1", "unit": "um", "effect_measure": "MD"}


def test_mean_difference_and_log_ratio_analytical():
    assert effect_size(contrast(), "MD")["yi"] == 10
    assert effect_size(contrast(), "MD")["vi"] == 2
    result = effect_size(contrast(), "ROM")
    assert result["yi"] == pytest.approx(math.log(2))
    assert result["vi"] == pytest.approx(0.0125)


@pytest.mark.parametrize("change", [{"sd": None}, {"n_independent": 1}, {"n_independent": 2.5},
                                   {"n_independent": True}, {"sd": -1}, {"mean": float("nan")}])
def test_variance_is_never_fabricated(change):
    value = contrast()
    value["treatment"].update(change)
    with pytest.raises(ValueError):
        effect_size(value)


def test_nonpositive_log_ratio_means():
    value = contrast()
    value["control"]["mean"] = 0
    with pytest.raises(ValueError, match="positive"):
        effect_size(value, "ROM")


def test_covariance_requires_order_diagonal_symmetry_and_positive_definiteness():
    effects = [effect_size(contrast("a", "family")), effect_size(contrast("b", "family"))]
    assert validate_covariance([[2, 0.5], [0.5, 2]], effects, ["a", "b"]).shape == (2, 2)
    for matrix, order in [([[2, 3], [3, 2]], ["a", "b"]), ([[2, 1], [0, 2]], ["a", "b"]),
                          ([[1, 0], [0, 1]], ["a", "b"]), ([[2, 0], [0, 2]], ["b", "a"])]:
        with pytest.raises(ValueError):
            validate_covariance(matrix, effects, order)


def test_missing_protocol_and_incompatible_arms_are_excluded():
    assert inferential([contrast()], {})["status"] == "ineligible"
    value = contrast()
    value["measurement_method"] = "microscopy"
    result = inferential([value], specification())
    assert result["effects"] == []
    assert "measurement_method" in str(result["excluded"])


def test_missing_r_is_reported_honestly(monkeypatch):
    monkeypatch.setattr("livingmeta.statistics.engine.shutil.which", lambda name: None)
    result = inferential([contrast(), contrast("b", "family-b")], specification())
    assert result["status"] == "engine_unavailable"
    assert len(result["effects"]) == 2 and "pooled" not in result


def test_dependent_effects_require_justified_covariance():
    values = [contrast(), contrast("b", "family-a"), contrast("c", "family-c")]
    result = inferential(values, specification())
    assert "covariance" in result["limitations"][0]


def test_fixed_r_bridge_only_receives_json(monkeypatch):
    captured = {}
    monkeypatch.setattr("livingmeta.statistics.engine.shutil.which", lambda name: "/usr/bin/Rscript")

    def run(args, **kwargs):
        captured["args"] = args
        captured["payload"] = json.loads(kwargs["input"])
        return SimpleNamespace(returncode=0, stdout='{"estimate":10,"ci_lower":1,"ci_upper":19}', stderr="")
    monkeypatch.setattr("livingmeta.statistics.engine.subprocess.run", run)
    result = inferential([contrast(), contrast("b", "family-b")], specification())
    assert result["status"] == "completed"
    assert captured["args"][1] == "--vanilla"
    assert captured["args"][2].endswith("meta_analysis.R")
    assert captured["payload"]["effects"][0]["vi"] == 2


def test_descriptive_preserves_methods_and_inequalities():
    exact = Measurement(experiment_id="x", outcome="diameter", raw_value="2", value=2, unit="um",
                        measurement_method="microscopy", status="accepted")
    other = exact.model_copy(update={"id": "other", "value": 4, "measurement_method": "DLS"})
    censored = exact.model_copy(update={"id": "censored", "qualifier": "gt", "value": 10})
    result = descriptive([Experiment(id="x", sample_label="sample", study_family="study",
                                     measurements=[exact, other, censored])], Protocol())
    assert len(result["groups"]) == 2
    assert all(group["n"] == 1 for group in result["groups"])
    assert len(result["excluded"]) == 1


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="Pinned R engine is available in Docker, not this host")
def test_r_integration_analytical_symmetric_effects():
    first = contrast()
    second = contrast("b", "family-b")
    result = inferential([first, second], specification())
    assert result["status"] == "completed", result["limitations"]
    assert result["pooled"]["estimate"] == pytest.approx(10)
    assert result["pooled"]["inference"] == "REML + safeguarded Knapp-Hartung"


def test_low_cr2_degrees_of_freedom_suppress_inference(monkeypatch):
    monkeypatch.setattr("livingmeta.statistics.engine.shutil.which", lambda name: "/usr/bin/Rscript")
    monkeypatch.setattr("livingmeta.statistics.engine.subprocess.run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout='{"estimate":10,"robust_df":2,"inference_available":false}',
                                        stderr=""))
    values = [contrast(), contrast("b", "family-a"), contrast("c", "family-c"), contrast("d", "family-d")]
    spec = specification()
    spec.update(covariance=[[2, 0.5, 0, 0], [0.5, 2, 0, 0], [0, 0, 2, 0], [0, 0, 0, 2]],
                covariance_order=["a", "b", "c", "d"], covariance_justification="Reported covariance")
    result = inferential(values, spec)
    assert result["status"] == "inference_unavailable"
    assert "pooled" not in result
    assert result["diagnostic_estimates"]["estimate"] == 10


def test_boolean_means_and_unrepresentable_variances_are_rejected():
    value = contrast()
    value["treatment"]["mean"] = True
    with pytest.raises(ValueError):
        effect_size(value)
    value = contrast()
    value["treatment"]["mean"] = 1e-320
    with pytest.raises(ValueError, match="variance"):
        effect_size(value, "ROM")
