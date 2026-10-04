"""Serial, checkpointed specialist extraction from source PDFs only.

Manual benchmark answers and manuscripts are never input to this pipeline. The
caller receives only one primary-source page at a time plus the research protocol.
"""

import hashlib
import inspect
import json
import re
from pathlib import Path
from typing import Callable

from livingmeta.agents.prompts import (COORDINATOR, FIGURES, PROMPT_VERSION, RECONCILE, TEXT, VERIFY)
from livingmeta.agents.provider import COMPLEX_MODEL, DEFAULT_MODEL
from livingmeta.domain import Evidence, Experiment, ExtractionBatch, Measurement, PageCoverage, Protocol
from .figures import FigureReview, digitize_plot, segment_microscopy
from .layout import inspect_pdf
from .normalization import normalize_measurement
from .verification import VerificationReview, apply_review, canonical_text, source_text, verify_batch


async def _callback(callback: Callable | None, state: dict):
    if callback:
        result = callback(state)
        if inspect.isawaitable(result):
            await result


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _stable_id(*parts) -> str:
    return hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:24]


def _page_input(page: dict, protocol: Protocol, document_hash: str) -> str:
    text = source_text(page)
    if len(text) > 80000:
        raise ValueError("Page text exceeds bounded context; split unusually large source pages explicitly")
    return json.dumps({"protocol": protocol.model_dump(), "document_hash": document_hash,
                       "page": page["page"], "width_pdf_points": page["width"],
                       "height_pdf_points": page["height"], "pixel_width": page.get("pixel_width"),
                       "pixel_height": page.get("pixel_height"), "PAGE TEXT": page["text"],
                       "TABLE TEXT": [{"locator": t["id"], "rows": t["rows"]} for t in page["tables"]],
                       "figure_candidates": page["figures"]}, ensure_ascii=False)


def _word_boxes(page: dict) -> list[dict]:
    scale_x = page["pixel_width"] / page["width"]
    scale_y = page["pixel_height"] / page["height"]
    result = [{"text": word["text"], "box": [word["box"][0] * scale_x, word["box"][1] * scale_y,
                                               word["box"][2] * scale_x, word["box"][3] * scale_y]}
              for word in page["words"]]
    result.extend(page.get("ocr", {}).get("words", []))
    return result


def _tick_labels(page: dict, plan) -> set[str]:
    """Require proposed tick text near that axis, not merely somewhere on a page."""
    labels = set()
    if len(plan.plot_bounds) != 4:
        return labels
    x0, y0, x1, y1 = plan.plot_bounds
    words = _word_boxes(page)
    for is_x, axis in ((True, plan.x_axis), (False, plan.y_axis)):
        for tick in axis.ticks:
            supported = False
            for word in words:
                if canonical_text(word["text"]) != canonical_text(tick.label):
                    continue
                a, b, c, d = word["box"]
                if is_x:
                    supported = a - 8 <= tick.pixel <= c + 8 and (abs(b - y1) < 70 or abs(d - y0) < 70)
                else:
                    supported = b - 8 <= tick.pixel <= d + 8 and (abs(c - x0) < 90 or abs(a - x1) < 90)
                if supported:
                    break
            if supported:
                labels.add(tick.label.strip())
    return labels


def _caption_supported(excerpt: str, page: dict) -> bool:
    return bool(excerpt.strip()) and canonical_text(excerpt) in canonical_text(source_text(page))


def _figure_batch(review: FigureReview, page: dict, digest: str, artifact_dir: Path) -> tuple[ExtractionBatch, list[dict]]:
    experiments: dict[str, Experiment] = {}
    abstentions = list(review.abstentions)
    results = []
    if len(review.plots) + len(review.microscopy) > 30:
        return ExtractionBatch(eligible=True, eligibility_reason="Primary-source figure candidates", abstentions=["Figure proposal count exceeds safety bound"]), []
    for index, plan in enumerate(review.plots):
        if len(plan.points) > 500:
            abstentions.append(f"{plan.figure_id}: point count exceeds safety bound")
            continue
        if not _caption_supported(plan.caption_excerpt, page):
            abstentions.append(f"{plan.figure_id}: caption excerpt lacks source support")
            continue
        overlay = artifact_dir / f"page-{page['page']:04d}-plot-{index + 1:02d}-overlay.png"
        result = digitize_plot(Path(page["image_path"]), plan, verified_tick_labels=_tick_labels(page, plan), overlay_path=overlay)
        record = result.model_dump()
        record["plan"] = plan.model_dump()
        results.append(record)
        if result.status == "abstained":
            abstentions.append(f"{plan.figure_id}: {'; '.join(result.notes)}")
            continue
        for point in result.points:
            key = point.sample_label
            experiment = experiments.setdefault(key, Experiment(id=_stable_id(digest, key), sample_label=key, study_family=digest))
            evidence = Evidence(document_hash=digest, page=page["page"], source_type="figure", locator=plan.figure_id,
                                excerpt=plan.caption_excerpt, figure_id=plan.figure_id, panel=plan.panel, series=plan.series,
                                artifact_key=overlay.name, calibration=json.dumps(result.calibration),
                                digitization_uncertainty=point.y_uncertainty)
            measurement = Measurement(experiment_id=experiment.id, outcome=plan.outcome, raw_value=f"Digitized marker at {point.pixel}",
                                      value=point.y, unit=plan.unit or plan.y_axis.unit, origin=point.origin, status="uncertain",
                                      uncertainty_type=point.uncertainty_type, evidence=[evidence],
                                      validation_notes=[*result.notes, f"X coordinate: {point.x} {plan.x_axis.unit or ''}; digitization uncertainty: {point.x_uncertainty}",
                                                        "Semantic sample and series assignment requires source adjudication"])
            if point.lower is not None:
                if point.uncertainty_type == "CI":
                    measurement.ci_lower, measurement.ci_upper = point.lower, point.upper
                elif point.uncertainty_type in {"SD", "SE"}:
                    # Require explicit caption definition and symmetric endpoints.
                    label = "standard deviation" if point.uncertainty_type == "SD" else "standard error"
                    explicit = re.search(rf"\b{point.uncertainty_type}\b|{label}", plan.caption_excerpt, re.I)
                    half = (point.upper - point.lower) / 2
                    if explicit and abs((point.upper + point.lower) / 2 - point.y) <= max(point.y_uncertainty * 2, half * 0.05):
                        measurement.uncertainty = half
                    else:
                        measurement.uncertainty_type = "unknown"
                        measurement.lower_bound, measurement.upper_bound = point.lower, point.upper
                        measurement.validation_notes.append("Error bar meaning or symmetry is unsupported; no SD/SE inferred")
                else:
                    measurement.lower_bound, measurement.upper_bound = point.lower, point.upper
            elif measurement.uncertainty_type != "none":
                measurement.uncertainty_type = "unknown"
            experiment.measurements.append(normalize_measurement(measurement))
    for index, plan in enumerate(review.microscopy):
        if not _caption_supported(plan.caption_excerpt, page):
            abstentions.append(f"{plan.figure_id}: microscopy caption lacks source support")
            continue
        # Multiword scale labels must be literal source text/OCR; no inferred scales.
        scale_text = source_text(page) + " " + page.get("ocr", {}).get("text", "")
        labels = {plan.scale_label} if canonical_text(plan.scale_label) in canonical_text(scale_text) else set()
        overlay = artifact_dir / f"page-{page['page']:04d}-microscopy-{index + 1:02d}-overlay.png"
        result = segment_microscopy(Path(page["image_path"]), plan, verified_scale_labels=labels, overlay_path=overlay)
        results.append({**result, "plan": plan.model_dump()})
        if result["status"] == "abstained":
            abstentions.append(f"{plan.figure_id}: {'; '.join(result['notes'])}")
            continue
        experiment = experiments.setdefault(plan.sample_label, Experiment(id=_stable_id(digest, plan.sample_label),
                                                                          sample_label=plan.sample_label, study_family=digest))
        for number, obj in enumerate(result["objects"]):
            calibration = {key: result[key] for key in ("scale_value", "scale_unit", "scale_bar_pixels", "units_per_pixel")}
            evidence = Evidence(document_hash=digest, page=page["page"], source_type="microscopy", locator=f"{plan.figure_id}:object-{number + 1}",
                                excerpt=plan.caption_excerpt, figure_id=plan.figure_id, panel=plan.panel, artifact_key=overlay.name,
                                calibration=json.dumps(calibration), digitization_uncertainty=obj["digitization_uncertainty"])
            experiment.measurements.append(normalize_measurement(Measurement(experiment_id=experiment.id,
                outcome="Projected_Droplet_Diameter_2D", raw_value=f"Segmented object area {obj['area_pixels']} pixels",
                value=obj["diameter"], unit=obj["unit"], origin="derived", status="uncertain",
                measurement_method=result["method"], n_technical=result["n_technical"], n_independent=None,
                evidence=[evidence], validation_notes=result["notes"])))
    return ExtractionBatch(eligible=True, eligibility_reason="Primary-source figure candidates", experiments=list(experiments.values()), abstentions=abstentions), results


def _merge_batches(batches: list[ExtractionBatch], screening: ExtractionBatch, digest: str) -> ExtractionBatch:
    output = screening.model_copy(deep=True)
    output.experiments, output.coverage = [], []
    experiments: dict[str, Experiment] = {}
    for batch in batches:
        output.abstentions.extend(batch.abstentions)
        output.coverage.extend(batch.coverage)
        for original in batch.experiments:
            # Do not blindly accept model-supplied identifiers or study families.
            key = original.sample_label.strip()
            if not key:
                output.abstentions.append("Experiment without an identifiable sample label")
                continue
            experiment = experiments.setdefault(key, Experiment(id=_stable_id(digest, key), sample_label=key,
                doi=output.doi, study_family=output.doi or digest, attributes=[]))
            for attribute in original.attributes:
                match = next((a for a in experiment.attributes if (a.name, a.text, a.value, a.unit, a.basis) ==
                              (attribute.name, attribute.text, attribute.value, attribute.unit, attribute.basis)), None)
                if match is None:
                    experiment.attributes.append(attribute.model_copy(deep=True))
                else:
                    existing_evidence = {e.model_dump_json() for e in match.evidence}
                    match.evidence.extend(e for e in attribute.evidence if e.model_dump_json() not in existing_evidence)
                    if attribute.status == "accepted" and match.status != "rejected":
                        match.status = "accepted"
            existing = {m.id: m for m in experiment.measurements}
            for measurement in original.measurements:
                measurement = measurement.model_copy(deep=True)
                measurement.experiment_id = experiment.id
                measurement.id = _stable_id(experiment.id, measurement.outcome, measurement.raw_value,
                                            measurement.unit, measurement.measurement_method, measurement.time_value,
                                            measurement.origin, [(e.page, e.locator) for e in measurement.evidence])
                if measurement.id not in existing:
                    experiment.measurements.append(measurement)
                    existing[measurement.id] = measurement
    output.experiments = list(experiments.values())
    output.abstentions = list(dict.fromkeys(output.abstentions))
    return output


async def extract_pdf(path: Path, protocol: Protocol, caller: Callable, artifact_dir: Path,
                      checkpoint: dict | None = None, on_checkpoint: Callable | None = None) -> ExtractionBatch:
    """Extract primary-source evidence with durable phase/page resume points.

    External/budget/cancellation exceptions propagate after the last completed
    checkpoint. Re-running with that checkpoint does not repeat finished phases.
    Figure-derived values remain uncertain until scientific adjudication.
    """
    artifact_dir = Path(artifact_dir)
    inventory = inspect_pdf(Path(path), artifact_dir, use_ocr=True)
    digest = inventory["document_hash"]
    protocol_hash = hashlib.sha256(protocol.model_dump_json().encode()).hexdigest()
    state = checkpoint.copy() if checkpoint else {"document_hash": digest, "protocol_hash": protocol_hash,
                                                "prompt_version": PROMPT_VERSION, "pages": {}}
    if (state.get("document_hash") != digest or state.get("protocol_hash") != protocol_hash
            or state.get("prompt_version") != PROMPT_VERSION):
        raise ValueError("Checkpoint source, protocol, or prompt version mismatch")
    state.setdefault("pages", {})
    async def save():
        _write_json(artifact_dir / "checkpoint.json", state)
        await _callback(on_checkpoint, json.loads(json.dumps(state)))

    _write_json(artifact_dir / "inventory.json", inventory)
    if "screening" not in state:
        screening_input = json.dumps({"protocol": protocol.model_dump(), "document_hash": digest,
                                      "SOURCE FIRST PAGES": [page["text"][:30000] for page in inventory["pages"][:2]]})
        screening_images = [Path(p["image_path"]) for p in inventory["pages"][:2] if not p["text"].strip()]
        screened = await caller("coordinator", COORDINATOR, screening_input, ExtractionBatch,
                                images=screening_images or None, model=DEFAULT_MODEL)
        screening = screened if isinstance(screened, ExtractionBatch) else ExtractionBatch.model_validate(screened)
        screening.experiments = []
        if screening.doi and canonical_text(screening.doi) not in canonical_text(" ".join(p["text"] for p in inventory["pages"])):
            screening.abstentions.append("Coordinator DOI was not found in primary-source text; DOI withheld")
            screening.doi = None
        state["screening"] = screening.model_dump()
        await save()
    screening = ExtractionBatch.model_validate(state["screening"])
    if not screening.eligible:
        screening.coverage = [PageCoverage(page=p["page"], text_status="screened_ineligible",
                                           table_count=len(p["tables"]), figure_count=len(p["figures"]),
                                           notes=[screening.eligibility_reason]) for p in inventory["pages"]]
        state["complete"] = True
        await save()
        for page in inventory["pages"]:
            for candidate in page["tables"] + page["figures"]:
                candidate["status"] = "not_extracted_ineligible_document"
        _write_json(artifact_dir / "inventory.json", inventory)
        _write_json(artifact_dir / "extraction.json", screening.model_dump())
        return screening
    batches = []
    pages = {page["page"]: page for page in inventory["pages"]}
    for page in inventory["pages"]:
        key = str(page["page"])
        progress = state["pages"].setdefault(key, {})
        context = _page_input(page, protocol, digest)
        if "text" not in progress:
            if page["text"].strip() or page["tables"]:
                extracted = await caller("text_and_tables", TEXT, context, ExtractionBatch, model=DEFAULT_MODEL)
                extracted = extracted if isinstance(extracted, ExtractionBatch) else ExtractionBatch.model_validate(extracted)
                verified = verify_batch(extracted, pages, digest)
            else:
                verified = ExtractionBatch(eligible=True, eligibility_reason="Eligible source", abstentions=[f"Page {key}: no recoverable text"])
            progress["text"] = verified.model_dump()
            await save()
        if "figures" not in progress:
            if page["figures"] or page["captions"]:
                reviewed = await caller("figures", FIGURES, context, FigureReview,
                                        images=[Path(page["image_path"])], model=COMPLEX_MODEL)
                reviewed = reviewed if isinstance(reviewed, FigureReview) else FigureReview.model_validate(reviewed)
                figures, digitization = _figure_batch(reviewed, page, digest, artifact_dir)
                _write_json(artifact_dir / f"page-{page['page']:04d}-digitization.json", {"results": digitization, "abstentions": figures.abstentions})
                if not reviewed.plots and not reviewed.microscopy:
                    figures.abstentions.append(f"Page {key}: figure candidates have no supported quantitative geometry")
            else:
                figures = ExtractionBatch(eligible=True, eligibility_reason="No detected figure candidates")
            progress["figures"] = figures.model_dump()
            await save()
        if "verified" not in progress:
            combined = _merge_batches([ExtractionBatch.model_validate(progress["text"]), ExtractionBatch.model_validate(progress["figures"])], screening, digest)
            if any(e.measurements for e in combined.experiments):
                review_input = context + "\nCANDIDATES:\n" + combined.model_dump_json()
                if "review" not in progress:
                    review = await caller("verification", VERIFY, review_input, VerificationReview, model=DEFAULT_MODEL)
                    review = review if isinstance(review, VerificationReview) else VerificationReview.model_validate(review)
                    progress["review"] = review.model_dump()
                    await save()
                review = VerificationReview.model_validate(progress["review"])
                combined = apply_review(combined, review)
                if review.rejected_measurement_ids or review.uncertain_measurement_ids:
                    if "reconciliation" not in progress:
                        reconciliation = await caller("reconciliation", RECONCILE,
                            review_input + "\nDISCREPANCIES:\n" + review.model_dump_json(), VerificationReview, model=COMPLEX_MODEL)
                        reconciliation = reconciliation if isinstance(reconciliation, VerificationReview) else VerificationReview.model_validate(reconciliation)
                        progress["reconciliation"] = reconciliation.model_dump()
                        await save()
                    reconciliation = VerificationReview.model_validate(progress["reconciliation"])
                    combined = apply_review(combined, reconciliation)
            text_notes = page["notes"] + [f"{t['id']}: examined by text/table specialist" for t in page["tables"]]
            figure_notes = [f"{f['id']}: reviewed; quantitative support recorded in digitization sidecar" for f in page["figures"]]
            status = "processed_with_abstentions" if combined.abstentions else "processed"
            if any(m.status in {"uncertain", "rejected"} for e in combined.experiments for m in e.measurements):
                status = "processed_requires_adjudication"
            combined.coverage = [PageCoverage(page=page["page"], text_status=page["text_status"] + ":" + status,
                                              table_count=len(page["tables"]), figure_count=len(page["figures"]),
                                              notes=text_notes + figure_notes)]
            progress["verified"] = combined.model_dump()
            await save()
        batches.append(ExtractionBatch.model_validate(progress["verified"]))
        completed = batches[-1]
        for table in page["tables"]:
            linked = [m for e in completed.experiments for m in e.measurements
                      if any(table["id"] in evidence.locator for evidence in m.evidence)]
            table["status"] = "extracted_candidates" if linked else "examined_no_supported_measurements"
            table["measurement_count"] = len(linked)
        figure_batch = ExtractionBatch.model_validate(progress["figures"])
        for figure in page["figures"]:
            figure["status"] = ("reviewed_geometry_requires_adjudication" if figure_batch.experiments
                                else "reviewed_no_supported_quantitative_geometry")
        page["processing_status"] = completed.coverage[0].text_status
    final = _merge_batches(batches, screening, digest)
    state["complete"] = True
    await save()
    _write_json(artifact_dir / "inventory.json", inventory)
    _write_json(artifact_dir / "extraction.json", final.model_dump())
    return final
