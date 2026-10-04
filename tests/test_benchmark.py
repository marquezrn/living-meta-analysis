import pytest

from livingmeta.benchmark import compare, family_split, import_reference_html
from livingmeta.domain import Attribute, Experiment, Measurement


def test_original_literal_import_preserves_nulls_and_inequalities(tmp_path):
    path = tmp_path / "reference.html"
    path.write_text('''<script>const database = [{ 'DOI':'10.1234/ABC',
    'Droplet_Size_um':null, 'Particle_L_nm':'>1000', 'Oil_Type':'null token'}];
    database[0].Droplet_Size_um = 999; evil();</script>''')
    rows = import_reference_html(path)
    assert rows[0]["DOI"] == "10.1234/abc"
    assert rows[0]["Droplet_Size_um"] is None
    assert rows[0]["Particle_L_nm"] == ">1000"
    assert rows[0]["Oil_Type"] == "null token"
    assert rows[0]["_reference_origin"] == "manual_original"


def test_reference_javascript_is_not_executed(tmp_path):
    path = tmp_path / "unsafe.html"
    path.write_text("const database = [{'DOI':'10.1234/abc', 'x': process.exit()}];")
    with pytest.raises(ValueError, match="Nonliteral"):
        import_reference_html(path)


def test_family_partition_stable_and_shared():
    assert family_split("same-study") == family_split("same-study")
    assert {family_split(f"study-{i}") for i in range(100)} == {"development", "holdout"}


def experiment(identifier="a", size=2, qualifier="exact", unit="um"):
    return Experiment(id=identifier, sample_label="sample", doi="10.1234/test", study_family="family",
                      attributes=[Attribute(name="Oil_Type", text="Hexadecane"),
                                  Attribute(name="Emulsion_Type", text="o/w")],
                      measurements=[Measurement(experiment_id=identifier, outcome="Droplet_Size_um",
                                                raw_value=str(size), value=size, qualifier=qualifier,
                                                unit=unit, status="accepted")])


def test_unit_conversion_and_manual_agreement_label():
    reference = [{"DOI": "10.1234/test", "Oil_Type": "Hexadecane", "Emulsion_Type": "o/w",
                  "Droplet_Size_um": 2}, {"DOI": "10.1234/missing", "Droplet_Size_um": 3}]
    result = compare([experiment(size=2000, unit="nm")], reference, ["10.1234/test"])
    assert result["label"] == "agreement_with_manual_reference"
    assert result["counts"]["matched"] == 1
    assert result["counts"]["missing_source_rows"] == 1
    assert result["agreement"]["proportion"] == 1
    assert result["agreement"]["verified_precision"] is None
    assert result["missing_source_dois"] == ["10.1234/missing"]


def test_inequalities_not_converted_to_exact_observations():
    result = compare([experiment(size=2)], [{"DOI": "10.1234/test", "Droplet_Size_um": ">2"}],
                     ["10.1234/test"])
    assert result["agreement"]["proportion"] == 0
    assert result["field_comparisons"][0]["reason"] == "qualifier_mismatch"


def test_ambiguous_alignment_is_explicit():
    reference = [{"DOI": "10.1234/test", "Oil_Type": "Hexadecane", "Emulsion_Type": "o/w",
                  "Droplet_Size_um": 2}, {"DOI": "10.1234/test", "Oil_Type": "Hexadecane",
                                          "Emulsion_Type": "o/w", "Droplet_Size_um": 3}]
    result = compare([experiment()], reference, ["10.1234/test"])
    assert result["counts"]["matched"] == 0
    assert result["counts"]["ambiguous"] == 1
    assert result["agreement"]["proportion"] is None


def test_multiple_candidates_cannot_claim_same_manual_row():
    result = compare([experiment("a"), experiment("b")], [{"DOI": "10.1234/test", "Droplet_Size_um": 2}],
                     ["10.1234/test"])
    assert result["counts"]["matched"] == 0
    assert result["counts"]["ambiguous"] == 2


def test_imputed_reference_rejected():
    with pytest.raises(ValueError, match="imputed"):
        compare([], [{"DOI": "10.1234/test", "_reference_origin": "imputed"}], [])


def test_independent_adjudication_precision_recall_and_pending():
    from livingmeta.benchmark import adjudicated_metrics
    decisions = [{"study_family": "family", "experiment_id": "experiment", "field": "diameter",
                  "reviewer": "Reviewer A", "source_locator": "Article hash/page 2/table 1",
                  "correct": True, "recoverable": True, "accepted": True, "source_type": "table"},
                 {"study_family": "family", "experiment_id": "experiment", "field": "stability",
                  "reviewer": "Reviewer A", "source_locator": "Article hash/page 3/figure 2",
                  "correct": False, "recoverable": True, "accepted": False, "abstained": True,
                  "source_type": "figure"},
                 {"study_family": "family", "experiment_id": "experiment", "field": "zeta"}]
    result = adjudicated_metrics(decisions)
    assert result["accepted_field_precision"]["estimate"] == 1
    assert result["recoverable_field_recall"]["estimate"] == 0.5
    assert result["pending_fields"] == 1
    assert result["source_recall"]["figure"]["estimate"] == 0
    assert result["accepted_field_precision"]["wilson_95_ci"][0] < 1


def test_family_bootstrap_is_reproducible_and_preserves_clusters():
    from livingmeta.benchmark import adjudicated_metrics
    decisions = [{"study_family": f"family-{i}", "experiment_id": "experiment", "field": "diameter",
                  "reviewer": "Reviewer A", "source_locator": "Primary article/page 2/table 1",
                  "correct": i == 0, "recoverable": True, "accepted": True, "source_type": "table"}
                 for i in range(3)]
    first = adjudicated_metrics(decisions)["family_clustered_intervals"]
    second = adjudicated_metrics(decisions)["family_clustered_intervals"]
    assert first == second
    assert first["study_families"] == 3
    assert first["accepted_precision_95_ci"][0] <= 1 / 3 <= first["accepted_precision_95_ci"][1]
