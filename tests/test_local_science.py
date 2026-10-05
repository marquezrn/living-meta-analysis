"""Local scientific safeguards exercised with synthetic PDF, XML, and figure data."""

import copy
import subprocess
import sys

import pytest
from PIL import Image

from livingmeta.domain import Protocol
from livingmeta.local.contracts import LocalWorkflowError
from livingmeta.local.synthesis import derive_contrasts, synthesize_workspace
from livingmeta.local.workflow import (finalize_workspace, next_job, prepare_workspace,
                                       refresh_workspace, submit_response)
from livingmeta.local.workspace import atomic_json, digest_json, file_digest, load_manifest, load_snapshot, read_json
from test_figures import plot_image, plot_plan
from test_local_workspace import TEXT, drain, extraction, make_pdf, prepared as prepared, response


def make_jats(path, figure=False):
    media = '<fig id="F1"><caption><p>Synthetic plot</p></caption><graphic xlink:href="plot.png"/></fig>' if figure else ""
    path.write_text(f'''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
    <article-id pub-id-type="doi">10.1234/synthetic</article-id><title-group><article-title>Synthetic Pickering experiment</article-title></title-group>
    </article-meta></front><body><sec id="results"><p id="p1">{TEXT}</p>
    <table-wrap id="T1"><caption><p>Droplet size</p></caption><table><tr><th>Size um</th></tr><tr><td>7</td></tr></table></table-wrap>
    {media}</sec></body></article>''', encoding="utf-8")


@pytest.fixture
def xml_workspace(tmp_path):
    papers = tmp_path / "jats"
    papers.mkdir()
    make_jats(papers / "synthetic.xml")
    workspace = tmp_path / "xml-private"
    prepare_workspace(papers, workspace, Protocol())
    return workspace


def test_jats_uses_exact_element_provenance_and_no_pdf_pages(xml_workspace):
    assert drain(xml_workspace) == ["screening", "text_table", "verification"]
    snapshot = finalize_workspace(xml_workspace)
    observation = snapshot["experiments"][0]["measurements"][0]
    assert observation["status"] == "accepted"
    assert observation["evidence"][0]["page"] is None
    assert observation["evidence"][0]["source_format"] == "xml"
    assert observation["evidence"][0]["xml_element"] == "id:p1"
    document = snapshot["documents"][0]
    assert document["page_count"] == 0
    assert document["coverage"]["tables"][0]["supported_measurements"] == 0
    assert document["coverage"]["source_units"][0]["xml_element"] == "/article/body[1]"


def test_jats_nonexistent_element_cannot_support_measurement(xml_workspace):
    screening = next_job(xml_workspace)
    submit_response(xml_workspace, response(screening, {"eligible": True, "eligibility_reason": "Synthetic"}))
    text = next_job(xml_workspace)
    data = extraction(text)
    data["experiments"][0]["measurements"][0]["evidence"][0]["xml_element"] = "id:missing"
    submit_response(xml_workspace, response(text, data))
    verification = next_job(xml_workspace)
    submit_response(xml_workspace, response(verification, {}))
    assert finalize_workspace(xml_workspace)["experiments"][0]["measurements"][0]["status"] == "rejected"


def test_reused_labels_do_not_merge_unidentified_conditions(prepared):
    _, workspace = prepared
    screening = next_job(workspace)
    submit_response(workspace, response(screening, {"eligible": True, "eligibility_reason": "Synthetic"}))
    text = next_job(workspace)
    first = extraction(text)["experiments"][0]
    second = copy.deepcopy(first)
    second["id"] = "different model identifier"
    submit_response(workspace, response(text, extraction(text, experiments=[first, second])))
    verification = next_job(workspace)
    submit_response(workspace, response(verification, {}))
    records = finalize_workspace(workspace)["experiments"]
    assert len(records) == 2 and len({record["id"] for record in records}) == 2


def test_supported_figure_geometry_retains_overlay_and_uncertainty(tmp_path, monkeypatch):
    papers = tmp_path / "jats"
    papers.mkdir()
    make_jats(papers / "synthetic.xml", figure=True)
    plot_image(papers / "plot.png")
    words = [{"text": str(value), "box": [pixel - 5, 184, pixel + 5, 194]}
             for pixel, value in [(20, 0), (100, 5), (180, 10)]]
    words += [{"text": str(value), "box": [0, pixel - 5, 15, pixel + 5]}
              for pixel, value in [(20, 10), (100, 5), (180, 0)]]
    monkeypatch.setattr("livingmeta.local.workflow.ocr_image", lambda *args: {"text": "0 5 10", "words": words})
    workspace = tmp_path / "figure-private"
    prepare_workspace(papers, workspace, Protocol(), use_ocr=True)
    stages, figure_data, verifier_images = [], None, []
    while (job := next_job(workspace)) is not None:
        stages.append(job["phase"])
        if job["phase"] == "screening":
            result = {"eligible": True, "eligibility_reason": "Synthetic", "doi": "10.1234/synthetic"}
        elif job["phase"] == "text_table":
            result = extraction(job)
        elif job["phase"] == "figure":
            plan = plot_plan(figure_id="F1")
            result = {"plots": [plan.model_dump()], "microscopy": [], "abstentions": []}
            figure_data = job
        else:
            verifier_images = job["images"]
            result = {}
        submit_response(workspace, response(job, result))
    snapshot = finalize_workspace(workspace)
    measurements = [m for e in snapshot["experiments"] for m in e["measurements"]]
    digitized = next(m for m in measurements if m["origin"] == "digitized")
    assert digitized["value"] == pytest.approx(5)
    assert digitized["status"] == "uncertain"  # Geometry is verified; series interpretation is not adjudicated.
    assert digitized["n_independent"] is None
    evidence = digitized["evidence"][0]
    assert evidence["source_format"] == "media" and evidence["page"] is None
    assert (workspace / evidence["artifact_key"]).exists()
    assert evidence["asset_path"] in verifier_images
    assert figure_data["images"] == verifier_images
    assert snapshot["documents"][0]["coverage"]["figures"][0]["supported_measurements"] == 1
    assert stages.count("figure") == 1


def test_changed_media_requires_explicit_refresh_and_new_jobs(tmp_path):
    papers = tmp_path / "jats"
    papers.mkdir()
    make_jats(papers / "synthetic.xml", figure=True)
    Image.new("RGB", (20, 20), "white").save(papers / "plot.png")
    workspace = tmp_path / "private"
    prepare_workspace(papers, workspace, Protocol())
    old = next_job(workspace)
    manifest = load_manifest(workspace)
    document = manifest["documents"][0]
    media = workspace / document["assets"][0]["path"]
    Image.new("RGB", (20, 20), "black").save(media)
    with pytest.raises(LocalWorkflowError, match="asset"):
        next_job(workspace)
    document["assets"][0]["sha256"] = file_digest(media)
    atomic_json(workspace / "manifest.json", manifest)
    refresh_workspace(workspace)
    new = next_job(workspace)
    assert new["document_hash"] == old["document_hash"] and new["id"] != old["id"]
    assert new["attempt"] == 1
    with pytest.raises(LocalWorkflowError):
        submit_response(workspace, response(old, {"eligible": True, "eligibility_reason": "Late response"}))


def contrast_fixture():
    spec = {"outcome": "Droplet_Size_um", "outcome_definition": "Mean droplet diameter",
            "measurement_method": "microscopy", "comparator": "untreated", "time_point": "0 day",
            "concentration_basis": None, "unit": "um", "effect_measure": "MD"}
    evidence = {"document_hash": "source", "page": 1, "source_type": "text", "locator": "paragraph",
                "excerpt": "Mean droplet diameter 7 um; SD 1 um; 3 independent groups; microscopy at 0 day untreated."}
    experiments = []
    for name, value in [("treatment", 7000), ("control", 5000)]:
        attributes = [{"name": key, "text": text, "status": "accepted", "evidence": [evidence]}
                      for key, text in [("outcome_definition", spec["outcome_definition"]),
                                        ("comparator", "untreated"), ("arms_independent", "true")]]
        experiments.append({"id": name, "sample_label": name, "study_family": "family", "attributes": attributes,
            "measurements": [{"id": f"{name}-mean", "experiment_id": name, "outcome": "Droplet_Size_um",
                "raw_value": str(value), "value": value, "unit": "nm", "statistic": "mean",
                "measurement_method": "microscopy", "time_value": 0, "time_unit": "day", "uncertainty_type": "SD",
                "uncertainty": 1000, "n_independent": 3, "status": "accepted", "evidence": [evidence]}]})
    documents = [{"sha256": "source", "status": "completed"}, {"sha256": "unrelated", "status": "completed"}]
    proposal = {"id": "contrast", "treatment_measurement_id": "treatment-mean", "control_measurement_id": "control-mean"}
    return experiments, documents, proposal, spec


def test_contrasts_use_retained_source_numbers_and_convert_sd():
    experiments, documents, proposal, spec = contrast_fixture()
    result = derive_contrasts(experiments, documents, [proposal], spec)[0]
    assert result["treatment"] == {"mean": 7, "sd": 1, "n_independent": 3, "qualifier": "exact"}
    assert result["control"]["mean"] == 5
    assert result["study_family"] == "family"
    proposal["treatment"] = {"mean": 999}
    with pytest.raises(LocalWorkflowError, match="disagree"):
        derive_contrasts(experiments, documents, [proposal], spec)


@pytest.mark.parametrize("change", ["missing_n", "wrong_method", "technical_curve", "different_family", "borrowed_definition"])
def test_invalid_scientific_contrasts_fail_closed(change):
    experiments, documents, proposal, spec = contrast_fixture()
    measurement = experiments[0]["measurements"][0]
    if change == "missing_n":
        measurement["n_independent"] = None
    elif change == "wrong_method":
        measurement["measurement_method"] = "laser diffraction"
    elif change == "technical_curve":
        measurement["origin"] = "curve_sample"
    elif change == "different_family":
        experiments[0]["study_family"] = "unrelated family"
    else:
        experiments[0]["attributes"][0]["evidence"] = [{**measurement["evidence"][0], "document_hash": "unrelated"}]
    with pytest.raises(LocalWorkflowError):
        derive_contrasts(experiments, documents, [proposal], spec)


def test_descriptive_synthesis_and_report_snapshot_remain_source_linked(prepared):
    _, workspace = prepared
    drain(workspace)
    finalize_workspace(workspace)
    result = synthesize_workspace(workspace)
    assert result["descriptive"]["counts"]["observations"] == 1
    assert result["descriptive"]["groups"][0]["mean"] == 7
    snapshot = load_snapshot(workspace)
    assert snapshot["synthesis"]["status"] == "descriptive"
    assert snapshot["freshness"]["last_synthesis_update"]
    dataset = read_json(workspace / "dataset.json")
    dataset["experiments"][0]["measurements"][0]["status"] = "uncertain"
    atomic_json(workspace / "dataset.json", dataset)
    assert load_snapshot(workspace)["synthesis"]["status"] == "historical"
    assert synthesize_workspace(workspace)["descriptive"]["counts"]["observations"] == 0


def test_validation_without_changed_data_does_not_make_synthesis_historical(prepared):
    _, workspace = prepared
    drain(workspace)
    finalize_workspace(workspace)
    synthesize_workspace(workspace)
    finalize_workspace(workspace)
    assert load_snapshot(workspace)["synthesis"]["status"] == "descriptive"


def test_missing_r_engine_does_not_fabricate_pooled_estimates(prepared, monkeypatch):
    _, workspace = prepared
    drain(workspace)
    finalize_workspace(workspace)
    experiments, _, proposal, spec = contrast_fixture()
    second = copy.deepcopy(experiments)
    digest = load_manifest(workspace)["documents"][0]["sha256"]
    for index, group in enumerate((experiments, second)):
        for experiment in group:
            experiment["study_family"] = f"family-{index}"
            experiment["id"] += f"-{index}"
            for measurement in experiment["measurements"]:
                measurement["id"] += f"-{index}"
                measurement["experiment_id"] = experiment["id"]
                measurement["evidence"][0]["document_hash"] = digest
            for attribute in experiment["attributes"]:
                attribute["evidence"][0]["document_hash"] = digest
    dataset = read_json(workspace / "dataset.json")
    dataset["experiments"] = experiments + second
    atomic_json(workspace / "dataset.json", dataset)
    manifest = load_manifest(workspace)
    manifest["protocol"].update(spec, analysis_mode="inferential")
    # This isolated fixture registers the synthetic analysis before invoking the engine.
    manifest["protocol_hash"] = digest_json(manifest["protocol"])
    atomic_json(workspace / "protocol.json", manifest["protocol"])
    atomic_json(workspace / "manifest.json", manifest)
    atomic_json(workspace / "analysis.json", {"contrasts": [{**proposal, "id": f"contrast-{index}",
        "treatment_measurement_id": f"treatment-mean-{index}", "control_measurement_id": f"control-mean-{index}"}
        for index in range(2)]})
    monkeypatch.setattr("livingmeta.statistics.engine.shutil.which", lambda *args: None)
    inferential = synthesize_workspace(workspace)["inferential"]
    assert inferential["status"] == "engine_unavailable"
    assert len(inferential["effects"]) == 2 and "pooled" not in inferential


def test_publication_flag_cannot_be_overwritten_by_finalization(prepared):
    _, workspace = prepared
    drain(workspace)
    finalize_workspace(workspace)
    manifest = load_manifest(workspace)
    manifest["documents"][0]["status"] = "quarantined"
    atomic_json(workspace / "manifest.json", manifest)
    assert next_job(workspace) is None
    snapshot = finalize_workspace(workspace)
    assert snapshot["documents"][0]["status"] == "quarantined"
    assert snapshot["experiments"][0]["measurements"][0]["status"] == "stale"
    assert snapshot["freshness"]["synthesis_stale"]
    assert synthesize_workspace(workspace)["descriptive"]["counts"]["observations"] == 0


def test_bad_source_abstains_without_stopping_good_sources(tmp_path):
    papers = tmp_path / "mixed"
    papers.mkdir()
    make_pdf(papers / "good.pdf", [TEXT])
    (papers / "bad.xml").write_text("<article>malformed", encoding="utf-8")
    workspace = tmp_path / "mixed-private"
    prepare_workspace(papers, workspace, Protocol())
    assert drain(workspace) == ["screening", "text_table", "verification"]
    snapshot = finalize_workspace(workspace)
    assert snapshot["run"]["status"] == "completed_with_abstentions"
    assert len(snapshot["experiments"]) == 1
    assert any("Source inspection failed" in reason for reason in snapshot["run"]["limitations"])


def test_local_core_imports_do_not_load_hosted_or_paid_api_modules():
    code = """import sys
from livingmeta.local.workflow import prepare_workspace, next_job, submit_response, finalize_workspace
from livingmeta.local.synthesis import synthesize_workspace
assert not any(name == 'openai' or name == 'agents' or name.startswith('livingmeta.db') or name.startswith('fastapi') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
