"""Offline structural fixtures and fake agents; no model-quality claims."""

import json
from pathlib import Path

import pytest

from livingmeta.domain import Attribute, Evidence, Experiment, ExtractionBatch, Measurement, Protocol
from livingmeta.extraction.layout import inspect_pdf, render_crop
from livingmeta.extraction.pipeline import _merge_batches, extract_pdf
from livingmeta.extraction.normalization import normalize_measurement
from livingmeta.extraction.verification import (VerificationReview, supported_number, verify_attribute, verify_measurement)


def make_pdf(path: Path, texts: list[str]):
    """Tiny synthetic source PDF using standard PDF objects, no publisher content."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    page_ids = []
    for text in texts:
        page_id, stream_id = len(objects) + 1, len(objects) + 2
        page_ids.append(page_id)
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 40 740 Td ({escaped}) Tj ET".encode("ascii")
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>".encode())
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {len(page_ids)} >>".encode()
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(document)


def page_record(text="Droplet size 7 um; SD 1 um; 3 independent preparations."):
    return {"text": text, "tables": [], "width": 612, "height": 792}


def measurement(**updates):
    data = dict(experiment_id="experiment", outcome="Droplet_Size_um", raw_value="7", value=7,
                unit="um", evidence=[Evidence(document_hash="source", page=1, source_type="text", locator="paragraph",
                                            excerpt="Droplet size 7 um; SD 1 um; 3 independent preparations.")])
    data.update(updates)
    return Measurement(**data)


def test_numeric_tokens_do_not_accept_substrings():
    assert not supported_number(7, "17 um")
    assert supported_number(-1.2e-3, "-1.2e-3 m")
    assert not supported_number(float("nan"), "nan")


def test_exact_excerpts_and_units_are_verified():
    result = verify_measurement(measurement(uncertainty_type="SD", uncertainty=1, n_independent=3), {1: page_record()}, "source")
    assert result.status == "accepted"
    assert result.normalized_value == 7
    assert result.uncertainty == 1


def test_invented_number_excerpt_and_hash_are_rejected():
    assert verify_measurement(measurement(value=17), {1: page_record()}, "source").status == "rejected"
    candidate = measurement()
    candidate.evidence[0].excerpt = "Droplet size 7 um was measured after 100 days."
    assert verify_measurement(candidate, {1: page_record()}, "source").status == "rejected"
    assert verify_measurement(measurement(), {1: page_record()}, "different-source").status == "rejected"


def test_unknown_error_bar_and_technical_n_are_not_independent():
    text = "Droplet size 7 um with error 1 um across 3 technical repeats."
    candidate = measurement(uncertainty_type="SD", uncertainty=1, n_independent=3, n_technical=3)
    candidate.evidence[0].excerpt = text
    result = verify_measurement(candidate, {1: page_record(text)}, "source")
    assert result.uncertainty_type == "unknown"
    assert result.n_independent is None
    assert result.n_technical == 3
    assert result.status == "uncertain"


def test_curve_samples_cannot_claim_replication():
    candidate = measurement(origin="curve_sample", n_independent=40, n_technical=40)
    result = verify_measurement(candidate, {1: page_record()}, "source")
    assert result.n_independent is None and result.n_technical is None
    assert result.status == "uncertain"


def test_ocr_numbers_and_condition_definitions_require_visual_review():
    page = {**page_record(), "text_status": "ocr_requires_review"}
    assert verify_measurement(measurement(), {1: page}, "source").status == "uncertain"
    evidence = measurement().evidence
    condition = Attribute(name="outcome_definition", text="Droplet size", evidence=evidence)
    assert verify_attribute(condition, {1: page}, "source").status == "uncertain"
    assert verify_attribute(condition, {1: page_record()}, "source").status == "accepted"
    independent = Attribute(name="arms_independent", text="true", evidence=evidence)
    assert verify_attribute(independent, {1: page_record()}, "source").status == "uncertain"
    assert verify_attribute(condition, {1: page_record()}, "other-source").status == "rejected"


def test_conditions_from_later_pages_are_preserved_without_overwriting_conflicts():
    first = Experiment(id="a", sample_label="Sample A", study_family="untrusted", attributes=[
        Attribute(name="Oil_Type", text="sunflower", evidence=measurement().evidence)])
    later = Experiment(id="b", sample_label="Sample A", study_family="untrusted", attributes=[
        Attribute(name="Temperature", text="25", value=25), Attribute(name="Oil_Type", text="olive")])
    batches = [ExtractionBatch(eligible=True, eligibility_reason="Synthetic", experiments=[exp]) for exp in (first, later)]
    result = _merge_batches(batches, ExtractionBatch(eligible=True, eligibility_reason="Synthetic"), "source")
    assert len(result.experiments) == 1
    assert [(a.name, a.text) for a in result.experiments[0].attributes] == [
        ("Oil_Type", "sunflower"), ("Temperature", "25"), ("Oil_Type", "olive")]


def test_inequalities_and_raw_uncertainty_are_preserved():
    result = verify_measurement(measurement(raw_value="7 +/- 2"), {1: page_record()}, "source")
    assert result.status == "rejected"
    assert verify_measurement(measurement(qualifier="lt"), {1: page_record()}, "source").status == "rejected"


def test_unit_normalization_never_converts_missing_basis():
    candidate = measurement(outcome="Concentration_wt_percent", value=2, unit="wt%", concentration_basis=None)
    result = normalize_measurement(candidate)
    assert result.normalized_value is None
    candidate.concentration_basis = "volume"
    assert normalize_measurement(candidate).status == "uncertain"
    candidate.concentration_basis = "mass"
    assert normalize_measurement(candidate).normalized_value == 2
    assert normalize_measurement(measurement(unit="nm", value=7000)).normalized_value == pytest.approx(7)


def test_structural_inventory_and_crop(tmp_path):
    path = tmp_path / "synthetic-source.pdf"
    make_pdf(path, ["Droplet size 7 um.", "Synthetic second page."])
    inventory = inspect_pdf(path, tmp_path / "artifacts", use_ocr=False)
    assert inventory["page_count"] == 2
    assert inventory["pages"][0]["text"] == "Droplet size 7 um."
    assert inventory["pages"][0]["words"][0]["box"][0] > 0
    assert Path(inventory["pages"][0]["image_path"]).exists()
    render_crop(Path(inventory["pages"][0]["image_path"]), [0, 0, 100, 100], tmp_path / "crop.png")
    with pytest.raises(ValueError, match="outside"):
        render_crop(Path(inventory["pages"][0]["image_path"]), [-1, 0, 100, 100], tmp_path / "bad.png")


@pytest.mark.asyncio
async def test_pipeline_fake_caller_checkpoints_and_resume(tmp_path, monkeypatch):
    """Fake caller checks plumbing; this does not validate OpenAI extraction quality."""
    monkeypatch.setattr("livingmeta.extraction.layout.ocr_image", lambda path: {"status": "unavailable", "text": "", "words": []})
    path = tmp_path / "synthetic-source.pdf"
    make_pdf(path, ["Droplet size 7 um; SD 1 um; 3 independent preparations."])
    calls = []
    async def caller(agent_name, instructions, input_text, output_type, images=None, model=None):
        calls.append(agent_name)
        if agent_name == "coordinator":
            return ExtractionBatch(eligible=True, eligibility_reason="Synthetic experimental source", publication_type="experimental")
        if agent_name == "text_and_tables":
            context = json.loads(input_text)
            candidate = measurement()
            candidate.evidence[0].document_hash = context["document_hash"]
            return ExtractionBatch(eligible=True, eligibility_reason="Synthetic source", experiments=[
                Experiment(id="model-id", sample_label="Sample A", study_family="model-family", measurements=[candidate])])
        return VerificationReview()
    states = []
    result = await extract_pdf(path, Protocol(), caller, tmp_path / "artifacts", on_checkpoint=lambda state: states.append(state))
    assert calls == ["coordinator", "text_and_tables", "verification"]
    assert result.experiments[0].measurements[0].status == "accepted"
    assert result.experiments[0].id != "model-id"
    assert len(result.coverage) == 1
    assert states[-1]["complete"]
    calls.clear()
    repeated = await extract_pdf(path, Protocol(), caller, tmp_path / "artifacts", checkpoint=states[-1])
    assert not calls
    assert repeated == result
    changed = Protocol(version="2.0")
    with pytest.raises(ValueError, match="mismatch"):
        await extract_pdf(path, changed, caller, tmp_path / "artifacts", checkpoint=states[-1])


@pytest.mark.asyncio
async def test_interrupted_page_resumes_without_repeating_completed_calls(tmp_path, monkeypatch):
    monkeypatch.setattr("livingmeta.extraction.layout.ocr_image", lambda path: {"status": "unavailable", "text": "", "words": []})
    path = tmp_path / "source.pdf"
    make_pdf(path, ["Droplet size 7 um; SD 1 um; 3 independent preparations."])
    states, calls = [], []
    interrupted = True
    async def caller(agent_name, instructions, input_text, output_type, **kwargs):
        nonlocal interrupted
        calls.append(agent_name)
        if agent_name == "coordinator":
            return ExtractionBatch(eligible=True, eligibility_reason="Synthetic source")
        if agent_name == "text_and_tables":
            context = json.loads(input_text)
            candidate = measurement()
            candidate.evidence[0].document_hash = context["document_hash"]
            return ExtractionBatch(eligible=True, eligibility_reason="Synthetic source", experiments=[Experiment(
                id="x", sample_label="Sample A", study_family="x", measurements=[candidate])])
        if interrupted:
            interrupted = False
            raise RuntimeError("Synthetic budget interruption")
        return VerificationReview()
    with pytest.raises(RuntimeError):
        await extract_pdf(path, Protocol(), caller, tmp_path / "artifacts", on_checkpoint=states.append)
    assert "text" in states[-1]["pages"]["1"]
    calls.clear()
    await extract_pdf(path, Protocol(), caller, tmp_path / "artifacts", checkpoint=states[-1])
    assert calls == ["verification"]


@pytest.mark.asyncio
async def test_reconciliation_resume_preserves_completed_verification(tmp_path, monkeypatch):
    monkeypatch.setattr("livingmeta.extraction.layout.ocr_image", lambda path: {"status": "unavailable", "text": "", "words": []})
    path = tmp_path / "source.pdf"
    make_pdf(path, ["Droplet size 7 um; SD 1 um; 3 independent preparations."])
    states, calls = [], []
    interrupted = True
    async def caller(agent_name, instructions, input_text, output_type, **kwargs):
        nonlocal interrupted
        calls.append(agent_name)
        if agent_name == "coordinator":
            return ExtractionBatch(eligible=True, eligibility_reason="Synthetic source")
        if agent_name == "text_and_tables":
            candidate = measurement()
            candidate.evidence[0].document_hash = json.loads(input_text)["document_hash"]
            return ExtractionBatch(eligible=True, eligibility_reason="Synthetic source", experiments=[Experiment(
                id="x", sample_label="Sample A", study_family="x", measurements=[candidate])])
        if agent_name == "verification":
            batch = json.loads(input_text.split("\nCANDIDATES:\n")[1])
            return VerificationReview(uncertain_measurement_ids=[batch["experiments"][0]["measurements"][0]["id"]],
                                      reasons=["Synthetic ambiguity requiring reconciliation"])
        if interrupted:
            interrupted = False
            raise RuntimeError("Synthetic interruption during reconciliation")
        return VerificationReview()
    with pytest.raises(RuntimeError):
        await extract_pdf(path, Protocol(), caller, tmp_path / "artifacts", on_checkpoint=states.append)
    assert "review" in states[-1]["pages"]["1"]
    calls.clear()
    batch = await extract_pdf(path, Protocol(), caller, tmp_path / "artifacts", checkpoint=states[-1])
    assert calls == ["reconciliation"]
    assert batch.experiments[0].measurements[0].status == "uncertain"
    inventory = json.loads((tmp_path / "artifacts" / "inventory.json").read_text())
    assert "adjudication" in inventory["pages"][0]["processing_status"]
