"""Checkpointed file jobs; scientific processing never invokes an agent or API."""

import json
import math
import re
import shutil
from pathlib import Path
from uuid import uuid4

from PIL import Image

from livingmeta.agents.prompts import COORDINATOR, FIGURES, PROMPT_VERSION, RECONCILE, TEXT, VERIFY
from livingmeta.domain import Attribute, Evidence, Experiment, ExtractionBatch, Measurement, Protocol
from livingmeta.extraction.figures import FigureReview, digitize_plot, segment_microscopy
from livingmeta.extraction.layout import inspect_pdf, ocr_image
from livingmeta.extraction.normalization import normalize_measurement
from livingmeta.extraction.verification import (VerificationReview, apply_review, canonical_text,
                                               source_text, supported_number, verify_attribute, verify_batch)

from .contracts import EvidenceDataset, FileResponse, JobRequest, LocalWorkflowError, MAX_RESPONSE_BYTES, RunManifest
from .workspace import (BLOCKED_SOURCES, assert_private_workspace, atomic_json, digest_json, event, file_digest,
                        load_dataset, load_jobs, load_manifest, load_snapshot, next_job as next_job, now, read_json,
                        safe_path, workspace_lock)

TERMINAL = {"completed", "abstained", "failed"}
SCHEMAS = {"screening": ExtractionBatch, "text_table": ExtractionBatch, "figure": FigureReview,
           "verification": VerificationReview, "resolution": VerificationReview}
INSTRUCTIONS = {"screening": COORDINATOR, "text_table": TEXT, "figure": FIGURES,
                "verification": VERIFY, "resolution": RECONCILE}


def _engine_hash():
    files = [Path(__file__), Path(__file__).with_name("contracts.py"), Path(__file__).with_name("workspace.py"),
             Path(__file__).with_name("synthesis.py")]
    package = Path(__file__).parents[1]
    files += [package / "domain.py", package / "agents/prompts.py", package / "discovery/fulltext.py",
              *[package / "extraction" / name for name in
                                    ("layout.py", "normalization.py", "verification.py", "figures.py")],
              *[package / "statistics" / name for name in ("engine.py", "effects.py", "meta_analysis.R")]]
    return digest_json({path.relative_to(package).as_posix(): file_digest(path) for path in files})


def _document(manifest, document_hash):
    return next(document for document in manifest["documents"] if document["sha256"] == document_hash)


def _inventory(workspace, document):
    path = safe_path(workspace, document["inventory_path"])
    if document.get("inventory_hash") and file_digest(path) != document["inventory_hash"]:
        raise LocalWorkflowError("Cached source inventory was changed")
    return read_json(path)


def _unit_id(unit):
    return str(unit.get("unit_id") or unit.get("xml_element") or unit.get("page"))


def _unit(workspace, document, unit_id):
    return next(unit for unit in _inventory(workspace, document)["pages"] if _unit_id(unit) == unit_id)


def source_signature(workspace, document):
    """Freeze the primary bytes, media bytes, and acquisition version metadata."""
    if file_digest(safe_path(workspace, document["source_path"])) != document["sha256"]:
        raise LocalWorkflowError("Primary source hash differs from the frozen manifest")
    assets = {}
    for asset in document.get("assets", []):
        relative = asset["path"]
        digest = file_digest(safe_path(workspace, relative))
        if asset.get("sha256") and digest != asset["sha256"]:
            raise LocalWorkflowError("Acquired source asset differs from its recorded hash")
        assets[relative] = digest
    return digest_json({"primary": document["sha256"], "assets": assets, "access": document.get("access"),
                        "bundle_hash": document.get("bundle_hash")})


def _document_jobs(workspace, document):
    return [job for job in load_jobs(workspace) if job["document_hash"] == document["sha256"] and
            job.get("inventory_hash") == document.get("inventory_hash")]


def _job(workspace, manifest, document, phase, payload, inputs=None, images=None):
    inputs, images = list(inputs or []), list(images or [])
    paths = list(dict.fromkeys(["protocol.json", document["source_path"], *inputs, *images]))
    hashes = {path: file_digest(safe_path(workspace, path)) for path in paths}
    identity = {"document_hash": document["sha256"], "phase": phase, "payload": payload,
                "protocol_hash": manifest["protocol_hash"], "prompt_version": PROMPT_VERSION,
                "engine_hash": manifest["engine_hash"], "input_hashes": hashes}
    job_id = digest_json(identity)[:32]
    path = safe_path(workspace, f"jobs/requests/{job_id}.json")
    instructions = INSTRUCTIONS[phase].replace(
        "You have no\nweb, file, shell, retrieval, or benchmark tools. Use only supplied source evidence.",
        "Read only the declared local input files and images. Do not use network, retrieval, benchmark, or unrelated filesystem access.")
    instructions += "\nThis is a local file job. Return only the required JSON result. " \
        "Read only the declared source inputs. Do not access manual reference answers, discovery, or external tools."
    if document.get("source_format") == "xml":
        instructions += "\nThis source is JATS XML. Evidence must use source_format=xml, xml_element, and page=null; never invent PDF page numbers."
    request = {"schema_version": 2, "id": job_id, "document_hash": document["sha256"], "phase": phase,
               "inputs": paths, "images": images, "input_hashes": hashes, "instructions": instructions,
               "response_schema": SCHEMAS[phase].model_json_schema(), "payload": payload}
    request["request_hash"] = digest_json(request)
    JobRequest.model_validate(request)
    existing_request = path.exists()
    if existing_request:
        try:
            stored = read_json(path)
            JobRequest.model_validate(stored)
        except ValueError as error:
            raise LocalWorkflowError("Stored job request has an invalid schema") from error
        stored_hash = digest_json({key: value for key, value in stored.items() if key != "request_hash"})
        if stored_hash != stored["request_hash"]:
            raise LocalWorkflowError("Stored job request hash is inconsistent")
        if stored != request:
            raise LocalWorkflowError("Stored job request differs from the current frozen identity")
    else:
        atomic_json(path, request)
    state_path = safe_path(workspace, f"jobs/state/{job_id}.json")
    if state_path.exists():
        return job_id
    if safe_path(workspace, f"jobs/responses/{job_id}.json").exists():
        raise LocalWorkflowError("A committed response has lost its job state; restore the checkpoint before continuing")
    # Callers hold the workspace lock. Recover the request/state creation window
    # without replacing an existing claim, completed checkpoint, or request.
    atomic_json(state_path, {
        "id": job_id, "document_hash": document["sha256"], "phase": phase, "status": "pending",
        "inventory_hash": document["inventory_hash"],
        "attempts": 0, "created_at": now(), "unit_id": payload.get("unit_id"),
        "region_id": payload.get("region_id")})
    event(workspace, "job_state_recovered" if existing_request else "job_prepared", request_id=job_id,
          phase=phase, document_hash=document["sha256"], model_calls=0)
    return job_id


def _inspect_document(workspace, document, use_ocr):
    source = safe_path(workspace, document["source_path"])
    if file_digest(source) != document["sha256"]:
        raise LocalWorkflowError("Primary source hash differs from the frozen manifest")
    relative = f"artifacts/{document['sha256']}"
    artifacts = safe_path(workspace, relative)
    artifacts.mkdir(parents=True, exist_ok=True)
    if document.get("source_format", "pdf") == "xml":
        from livingmeta.discovery.fulltext import inspect_jats
        inventory = inspect_jats(source, artifacts)
    else:
        inventory = inspect_pdf(source, artifacts, use_ocr=use_ocr)
    for identifier in ("doi", "pmid", "pmcid", "title"):
        if inventory.get(identifier) and not document.get(identifier):
            document[identifier] = inventory[identifier]
    for unit in inventory["pages"]:
        unit["unit_id"] = _unit_id(unit)
        if unit.get("image_path"):
            unit["image_path"] = Path(unit["image_path"]).resolve().relative_to(Path(workspace).resolve()).as_posix()
        for figure in unit["figures"]:
            if document.get("source_format") != "xml":
                continue
            figure["media_paths"] = []
            figure["asset_paths"] = [Path(path).resolve().relative_to(Path(workspace).resolve()).as_posix()
                                     for path in figure.get("asset_paths", [])]
            for reference in figure.get("asset_refs", []):
                reference = reference.get("href") if isinstance(reference, dict) else reference
                if not reference or ":" in reference or ".." in Path(reference).parts:
                    continue
                path = source.parent / reference
                candidates = [path] if path.suffix else [path.with_suffix(extension) for extension in
                                                        (".png", ".jpg", ".jpeg", ".tif", ".webp")]
                for candidate in candidates:
                    if not candidate.is_file() or candidate.is_symlink():
                        continue
                    try:
                        with Image.open(candidate) as image:
                            if image.width * image.height > 25_000_000:
                                continue
                            target = artifacts / f"media-{file_digest(candidate)[:24]}.png"
                            if not target.exists():
                                image.convert("RGB").save(target)
                        media = target.relative_to(Path(workspace)).as_posix()
                        figure["media_paths"].append(media)
                        if use_ocr:
                            figure.setdefault("ocr", {})[media] = ocr_image(target)
                    except (OSError, ValueError):
                        continue
    inventory["source_signature"] = source_signature(workspace, document)
    path = artifacts / "inventory.json"
    atomic_json(path, inventory)
    document.update(inventory_path=f"{relative}/inventory.json", inventory_hash=file_digest(path),
                    page_count=inventory["page_count"], use_ocr=use_ocr, status="pending",
                    source_signature=inventory["source_signature"])
    return inventory


def _inspect_safely(workspace, document, use_ocr):
    try:
        inventory = _inspect_document(workspace, document, use_ocr)
        if not inventory["pages"]:
            document.update(status="abstained", inspection_error="No inspectable source units were found")
    except LocalWorkflowError:
        raise
    except Exception as error:
        document.update(status="abstained", inspection_error=f"Source inspection failed: {type(error).__name__}")
        document.pop("inventory_path", None)
        document.pop("inventory_hash", None)
        event(workspace, "source_inspection_abstained", document_hash=document["sha256"],
              reason=document["inspection_error"])


def prepare_workspace(papers: Path, workspace: Path, protocol: Protocol, use_ocr=False):
    workspace = assert_private_workspace(workspace)
    papers = Path(papers).expanduser().resolve()
    if not papers.is_dir() or workspace.is_relative_to(papers):
        raise LocalWorkflowError("Choose a primary-source folder and a separate private workspace")
    protocol = Protocol.model_validate(protocol)
    protocol_hash = digest_json(protocol.model_dump(mode="json"))
    with workspace_lock(workspace):
        path = safe_path(workspace, "manifest.json")
        if path.exists():
            manifest = load_manifest(workspace)
            if manifest["protocol_hash"] != protocol_hash or manifest["engine_hash"] != _engine_hash():
                raise LocalWorkflowError("Protocol or scientific engine changed; prepare a new frozen workspace")
        else:
            manifest = {"schema_version": 2, "id": uuid4().hex, "name": protocol.name,
                        "protocol": protocol.model_dump(mode="json"), "protocol_hash": protocol_hash,
                        "prompt_version": PROMPT_VERSION, "engine_hash": _engine_hash(), "documents": [],
                        "created_at": now(), "updated_at": now(), "status": "ready",
                        "agent_usage": {"billing_mode": "existing_agent_client", "separate_api_calls": 0,
                                        "monetary_cost": None}, "blinding": {"status": "not_verified"}}
            atomic_json(safe_path(workspace, "protocol.json"), manifest["protocol"])
            atomic_json(safe_path(workspace, "dataset.json"), {"schema_version": 2, "experiments": [],
                        "publications": [], "limitations": []})
        RunManifest.model_validate(manifest)
        known = {document["sha256"] for document in manifest["documents"]}
        for source in sorted(papers.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in {".pdf", ".xml", ".nxml"}:
                continue
            if source.is_symlink() or source.stat().st_size > 100 * 1024 * 1024:
                raise LocalWorkflowError("Source symlinks and documents above 100 MB are unsupported")
            digest = file_digest(source)
            if digest in known:
                continue
            source_format = "pdf" if source.suffix.lower() == ".pdf" else "xml"
            relative = f"sources/{digest}/{source.name}"
            destination = safe_path(workspace, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            document = {"id": digest, "document_hash": digest, "sha256": digest, "filename": source.name,
                        "source_format": source_format, "source_path": relative, "status": "pending"}
            if source_format == "xml":
                # Copy only adjacent image assets; extraction never follows external URLs.
                for asset in source.parent.iterdir():
                    if asset.is_file() and not asset.is_symlink() and asset.suffix.lower() in {
                            ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp"}:
                        if asset.stat().st_size <= 20 * 1024 * 1024:
                            shutil.copyfile(asset, destination.parent / asset.name)
                            document.setdefault("assets", []).append({
                                "path": (destination.parent / asset.name).relative_to(workspace).as_posix(),
                                "sha256": file_digest(asset), "kind": "figure_media"})
            manifest["documents"].append(document)
            known.add(digest)
        for document in manifest["documents"]:
            if not document.get("inventory_path"):
                _inspect_safely(workspace, document, use_ocr)
            elif source_signature(workspace, document) != document.get("source_signature"):
                raise LocalWorkflowError("Source bundle changed; refresh the workspace before continuing")
        manifest["updated_at"] = now()
        atomic_json(path, manifest)
        synchronize(workspace, manifest)
        event(workspace, "workspace_prepared", documents=len(manifest["documents"]), separate_api_calls=0)
    return manifest


def refresh_workspace(workspace: Path):
    """Prepare newly acquired sources; retire jobs from superseded media bundles."""
    with workspace_lock(workspace):
        manifest = load_manifest(workspace)
        if manifest["engine_hash"] != _engine_hash():
            raise LocalWorkflowError("Scientific engine changed; prepare a new frozen workspace")
        for document in manifest["documents"]:
            if document.get("status") in BLOCKED_SOURCES:
                continue
            signature = source_signature(workspace, document)
            if document.get("inventory_path") and signature == document.get("source_signature"):
                continue
            for job in load_jobs(workspace):
                if job["document_hash"] != document["sha256"]:
                    continue
                if job["status"] not in TERMINAL:
                    job.update(status="abstained", reason="Source bundle superseded by explicit refresh")
                    atomic_json(safe_path(workspace, f"jobs/state/{job['id']}.json"), job)
            _inspect_safely(workspace, document, document.get("use_ocr", False))
        manifest["synthesis_stale"] = True
        manifest["status"] = "ready"
        synchronize(workspace, manifest)
        event(workspace, "sources_refreshed", documents=len(manifest["documents"]), model_calls=0)
    return manifest


def _phase_result(workspace, job):
    path = safe_path(workspace, f"jobs/responses/{job['id']}.json")
    if path.exists() and job.get("response_hash") and file_digest(path) != job["response_hash"]:
        raise LocalWorkflowError("Committed agent response was modified")
    return read_json(path)["result"] if path.exists() else None


def recover_committed_responses(workspace, manifest):
    """Recover the durable response/state crash window without repeating agent work."""
    if manifest["engine_hash"] != _engine_hash():
        raise LocalWorkflowError("Scientific engine changed; prepare a new frozen workspace")
    for state in load_jobs(workspace):
        path = safe_path(workspace, f"jobs/responses/{state['id']}.json")
        if state["status"] != "claimed" or not path.exists():
            continue
        saved = read_json(path)
        request = read_json(safe_path(workspace, f"jobs/requests/{state['id']}.json"))
        if (saved.get("claim_token") != state.get("claim_token") or
                saved.get("request_hash") != request["request_hash"] or
                digest_json({k: v for k, v in request.items() if k != "request_hash"}) != request["request_hash"] or
                digest_json(saved.get("result")) != saved.get("result_hash")):
            raise LocalWorkflowError("Interrupted response commit is inconsistent")
        for relative, digest in request["input_hashes"].items():
            if file_digest(safe_path(workspace, relative)) != digest:
                raise LocalWorkflowError("Frozen job input was modified")
        SCHEMAS[request["phase"]].model_validate(saved["result"])
        document = _document(manifest, request["document_hash"])
        if document.get("status") in BLOCKED_SOURCES or state.get("inventory_hash") != document.get("inventory_hash"):
            continue
        if request["phase"] == "screening":
            document.update(doi=saved["result"].get("doi"), title=saved["result"].get("title"))
        if request["phase"] in {"text_table", "figure"}:
            batch = safe_path(workspace, f"artifacts/{document['sha256']}/batches/{request['id']}.json")
            if not saved.get("batch_hash") or file_digest(batch) != saved["batch_hash"]:
                raise LocalWorkflowError("Interrupted candidate batch is inconsistent")
            state["batch_hash"] = saved["batch_hash"]
        state.update(status="completed", completed_at=saved["received_at"], response_hash=file_digest(path))
        atomic_json(safe_path(workspace, f"jobs/state/{state['id']}.json"), state)
        event(workspace, "response_commit_recovered", request_id=state["id"], model_calls=0)


def _source_context(document, unit):
    return {"document_hash": document["sha256"], "source_format": document.get("source_format", "pdf"),
            "unit_id": _unit_id(unit), "page": unit.get("page"), "xml_element": unit.get("xml_element"),
            "width_pdf_points": unit.get("width"), "height_pdf_points": unit.get("height"),
            "pixel_width": unit.get("pixel_width"), "pixel_height": unit.get("pixel_height"),
            "PAGE TEXT": unit["text"], "TABLE TEXT": unit["tables"], "paragraphs": unit.get("paragraphs", []),
            "figure_candidates": unit["figures"]}


def synchronize(workspace, manifest):
    """Create successors only after predecessor evidence is durably committed."""
    recover_committed_responses(workspace, manifest)
    if manifest["engine_hash"] != _engine_hash():
        raise LocalWorkflowError("Scientific engine changed; prepare a new frozen workspace before claiming jobs")
    for document in manifest["documents"]:
        if not document.get("inventory_path") or document.get("status") in BLOCKED_SOURCES or document.get("inspection_error"):
            continue
        inventory = _inventory(workspace, document)
        if source_signature(workspace, document) != document.get("source_signature"):
            raise LocalWorkflowError("Source bundle changed; explicit refresh is required")
        jobs = _document_jobs(workspace, document)
        screening = next((job for job in jobs if job["phase"] == "screening"), None)
        if screening is None:
            units = inventory["pages"][:2]
            images = [unit["image_path"] for unit in units if unit.get("image_path")]
            _job(workspace, manifest, document, "screening", {
                "protocol": manifest["protocol"], "source_format": document.get("source_format", "pdf"),
                "document_hash": document["sha256"], "SOURCE FIRST PAGES": [unit["text"][:30000] for unit in units]},
                inputs=[document["inventory_path"]], images=images)
            continue
        if screening["status"] not in TERMINAL:
            continue
        result = _phase_result(workspace, screening)
        if not result:
            document.update(status="abstained", eligibility_reason=screening.get("reason", "Screening unavailable"))
            continue
        if not result["eligible"]:
            document.update(status="screened_excluded", eligibility_reason=result["eligibility_reason"],
                            coverage={"source_units": [], "tables": [], "figures": [], "not_attempted": True})
            continue
        document.update(doi=result.get("doi"), title=result.get("title"), status="extracting")
        for unit in inventory["pages"]:
            uid = _unit_id(unit)
            unit_jobs = [job for job in jobs if job.get("unit_id") == uid]
            text_job = next((job for job in unit_jobs if job["phase"] == "text_table"), None)
            context = _source_context(document, unit)
            review_images = list(dict.fromkeys(([unit["image_path"]] if unit.get("image_path") else []) +
                                [path for region in unit["figures"] for path in region.get("media_paths", [])]))
            if text_job is None:
                _job(workspace, manifest, document, "text_table", {**context, "protocol": manifest["protocol"]},
                     inputs=[document["inventory_path"]], images=review_images)
            for region in unit["figures"]:
                if not any(job["phase"] == "figure" and job.get("region_id") == region["id"] for job in unit_jobs):
                    images = [unit["image_path"]] if unit.get("image_path") else region.get("media_paths", [])
                    _job(workspace, manifest, document, "figure", {**context, "region_id": region["id"],
                         "region": region, "protocol": manifest["protocol"]},
                         inputs=[document["inventory_path"]], images=images)
            expected = 1 + len(unit["figures"])
            extraction = [job for job in unit_jobs if job["phase"] in {"text_table", "figure"}]
            if len(extraction) != expected or any(job["status"] not in TERMINAL for job in extraction):
                continue
            verification = next((job for job in unit_jobs if job["phase"] == "verification"), None)
            candidates = _collect(workspace, document, extraction)
            if verification is None:
                _job(workspace, manifest, document, "verification", {**context, "candidates": candidates,
                     "extraction_request_ids": [job["id"] for job in extraction]},
                     inputs=[document["inventory_path"]], images=review_images)
                continue
            if verification["status"] not in TERMINAL:
                continue
            review = _phase_result(workspace, verification)
            if review and (review.get("rejected_measurement_ids") or review.get("uncertain_measurement_ids")):
                if not any(job["phase"] == "resolution" for job in unit_jobs):
                    _job(workspace, manifest, document, "resolution", {**context, "candidates": candidates,
                         "discrepancies": review}, inputs=[document["inventory_path"]],
                         images=review_images)
    manifest["updated_at"] = now()
    atomic_json(safe_path(workspace, "manifest.json"), manifest)


def _verify_xml(batch, inventory, unit, document_hash):
    """Validate exact JATS element provenance without assigning fictitious pages."""
    result = batch.model_copy(deep=True)
    elements = inventory.get("elements", {})
    for experiment in result.experiments:
        for observation in [*experiment.attributes, *experiment.measurements]:
            failures = []
            for evidence in observation.evidence:
                locator = getattr(evidence, "xml_element", None)
                element = elements.get(locator)
                text = element.get("text", "") if isinstance(element, dict) else str(element or "")
                if (evidence.document_hash != document_hash or getattr(evidence, "source_format", "pdf") != "xml"
                        or evidence.page is not None or not locator or not text
                        or canonical_text(evidence.excerpt) not in canonical_text(text)):
                    failures.append("JATS element evidence lacks exact source support")
                if evidence.bounding_box is not None:
                    failures.append("JATS text evidence has no PDF page bounds; use exact structural locators")
                if locator and unit.get("xml_element") and locator != unit["xml_element"] and not (
                        locator.startswith(unit["xml_element"] + "/") or canonical_text(text) in canonical_text(unit["text"])):
                    failures.append("JATS element lies outside the requested source unit")
            if not observation.evidence:
                failures.append("Primary JATS element evidence is missing")
            text = "\n".join(e.excerpt for e in observation.evidence)
            if isinstance(observation, Measurement):
                for field in ("value", "lower_bound", "upper_bound", "uncertainty", "time_value", "n_independent", "n_technical"):
                    value = getattr(observation, field)
                    if value is not None and not supported_number(value, text):
                        failures.append(f"JATS reported {field} lacks numeric support")
                if observation.origin != "reported":
                    observation.status = "uncertain"
                    observation.validation_notes.append("Figure geometry requires separate source calibration")
                else:
                    # Reuse scientific semantic checks with the actual null source page.
                    verified = verify_batch(ExtractionBatch(eligible=True, eligibility_reason="JATS source",
                        experiments=[Experiment(id=experiment.id, sample_label=experiment.sample_label,
                            study_family=experiment.study_family, measurements=[observation])]),
                        {None: {**unit, "width": math.inf, "height": math.inf}}, document_hash)
                    revised = verified.experiments[0].measurements[0]
                    observation.status = revised.status
                    observation.n_independent, observation.n_technical = revised.n_independent, revised.n_technical
                    observation.uncertainty_type = revised.uncertainty_type
                    observation.validation_notes.extend(revised.validation_notes)
            else:
                revised = verify_attribute(observation, {None: {**unit, "width": math.inf, "height": math.inf}}, document_hash)
                observation.status = revised.status
                observation.validation_notes.extend(revised.validation_notes)
            if failures:
                observation.status = "rejected"
                observation.validation_notes.extend(failures)
        experiment.measurements = [normalize_measurement(measurement) for measurement in experiment.measurements]
    return result


def _canonical_batch(batch, document, unit_id):
    for ordinal, experiment in enumerate(batch.experiments):
        conditions = sorted(((a.name, a.text, a.value, a.unit, a.basis) for a in experiment.attributes),
                            key=lambda value: json.dumps(value, ensure_ascii=False))
        identity = {"document": document["sha256"], "sample": experiment.sample_label,
                    "conditions": conditions, "unidentified_condition_unit": unit_id if not conditions else None,
                    "unidentified_condition_record": ordinal if not conditions else None}
        experiment.id = digest_json(identity)[:32]
        condition_id = digest_json({"experiment_id": experiment.id, "conditions": conditions})[:32]
        experiment.doi, experiment.study_family = document.get("doi"), document.get("study_family") or document.get("doi") or document["sha256"]
        for measurement in experiment.measurements:
            measurement.experiment_id = experiment.id
            measurement.condition_id = condition_id
            measurement.id = digest_json({"experiment": experiment.id, "outcome": measurement.outcome,
                "raw_value": measurement.raw_value, "unit": measurement.unit, "method": measurement.measurement_method,
                "time": [measurement.time_value, measurement.time_unit], "evidence": [e.model_dump(mode="json") for e in measurement.evidence]})[:32]
    return batch


def _tick_labels(unit, plan, image_path, region=None):
    words = []
    if unit.get("pixel_width") and unit.get("width"):
        sx, sy = unit["pixel_width"] / unit["width"], unit["pixel_height"] / unit["height"]
        words = [{"text": word["text"], "box": [word["box"][0] * sx, word["box"][1] * sy,
                  word["box"][2] * sx, word["box"][3] * sy]} for word in unit.get("words", [])]
    words += unit.get("ocr", {}).get("words", [])
    if region:
        words += region.get("ocr", {}).get(image_path, {}).get("words", [])
    labels = set()
    x0, y0, x1, y1 = plan.plot_bounds
    for is_x, axis in ((True, plan.x_axis), (False, plan.y_axis)):
        for tick in axis.ticks:
            for word in words:
                if canonical_text(word["text"]) != canonical_text(tick.label):
                    continue
                a, b, c, d = word["box"]
                matched = (a - 8 <= tick.pixel <= c + 8 and (abs(b - y1) < 70 or abs(d - y0) < 70)) if is_x else \
                    (b - 8 <= tick.pixel <= d + 8 and (abs(c - x0) < 90 or abs(a - x1) < 90))
                if matched:
                    labels.add(tick.label.strip())
                    break
    return labels


def _figures(workspace, document, unit, review, request):
    batch = ExtractionBatch(eligible=True, eligibility_reason="Source figure review", abstentions=list(review.abstentions))
    images = request["images"]
    if len(images) != 1 or len(review.plots) + len(review.microscopy) > 30:
        batch.abstentions.append("A figure job requires one identifiable image and at most 30 proposals")
        return batch
    image_path = images[0]
    for index, plan in enumerate([*review.plots, *review.microscopy]):
        text = source_text(unit) + "\n" + "\n".join(
            caption.get("text", "") if isinstance(caption, dict) else caption for caption in unit.get("captions", []))
        if not plan.caption_excerpt.strip() or canonical_text(plan.caption_excerpt) not in canonical_text(text):
            batch.abstentions.append(f"{plan.figure_id}: source caption is not supported")
            continue
        overlay = f"artifacts/{document['sha256']}/{request['id']}-overlay-{index}.png"
        sidecar = f"artifacts/{document['sha256']}/{request['id']}-geometry-{index}.json"
        kwargs = {"document_hash": document["sha256"], "page": unit.get("page"), "source_type": "figure",
                  "locator": plan.figure_id, "excerpt": plan.caption_excerpt, "figure_id": request["payload"]["region_id"],
                  "panel": plan.panel, "artifact_key": overlay}
        if document.get("source_format") == "xml":
            if plan.figure_id != request["payload"]["region_id"]:
                batch.abstentions.append("Proposed figure ID differs from the requested JATS figure")
                continue
            kwargs.update(source_format="media", asset_path=image_path, xml_element=unit.get("xml_element"))
        if index < len(review.plots):
            if len(plan.points) > 500:
                batch.abstentions.append("Figure point bound exceeded")
                continue
            result = digitize_plot(safe_path(workspace, image_path), plan,
                verified_tick_labels=_tick_labels(unit, plan, image_path, request["payload"].get("region")),
                overlay_path=safe_path(workspace, overlay))
            atomic_json(safe_path(workspace, sidecar), {"plan": plan.model_dump(), "result": result.model_dump()})
            if result.status == "abstained":
                batch.abstentions.append(f"{plan.figure_id}: {'; '.join(result.notes)}")
                continue
            for point in result.points:
                evidence = Evidence(**kwargs, series=plan.series, calibration=json.dumps(result.calibration),
                                    digitization_uncertainty=point.y_uncertainty)
                experiment = Experiment(id="candidate", sample_label=point.sample_label, study_family="candidate",
                    attributes=[Attribute(name="figure_series", text=plan.series, evidence=[evidence], status="uncertain"),
                                Attribute(name="figure_x", text=str(point.x), value=point.x, unit=plan.x_axis.unit,
                                          evidence=[evidence], status="uncertain")])
                observation = Measurement(experiment_id="candidate", outcome=plan.outcome,
                    raw_value=f"Digitized marker at {point.pixel}", value=point.y, unit=plan.unit or plan.y_axis.unit,
                    origin=point.origin, status="uncertain", evidence=[evidence],
                    validation_notes=[*result.notes, "Series and sample interpretation requires independent source adjudication"])
                if point.lower is not None:
                    observation.lower_bound, observation.upper_bound = point.lower, point.upper
                    observation.uncertainty_type = "unknown"
                    label = "standard deviation" if point.uncertainty_type == "SD" else "standard error"
                    half = (point.upper - point.lower) / 2
                    if point.uncertainty_type in {"SD", "SE"} and re.search(
                            rf"\b{point.uncertainty_type}\b|{label}", plan.caption_excerpt, re.I) and \
                            abs((point.upper + point.lower) / 2 - point.y) <= max(point.y_uncertainty * 2, half * .05):
                        observation.uncertainty_type, observation.uncertainty = point.uncertainty_type, half
                    elif point.uncertainty_type == "CI" and re.search(r"\bCI\b|confidence interval", plan.caption_excerpt, re.I):
                        observation.uncertainty_type = "CI"
                        observation.ci_lower, observation.ci_upper = point.lower, point.upper
                experiment.measurements = [normalize_measurement(observation)]
                batch.experiments.append(experiment)
        else:
            scale_text = text + " " + unit.get("ocr", {}).get("text", "")
            region_ocr = request["payload"].get("region", {}).get("ocr", {}).get(image_path, {})
            scale_text += " " + region_ocr.get("text", "")
            result = segment_microscopy(safe_path(workspace, image_path), plan,
                verified_scale_labels={plan.scale_label} if canonical_text(plan.scale_label) in canonical_text(scale_text) else set(),
                overlay_path=safe_path(workspace, overlay))
            atomic_json(safe_path(workspace, sidecar), {"plan": plan.model_dump(), "result": result})
            if result["status"] == "abstained":
                batch.abstentions.append(f"{plan.figure_id}: {'; '.join(result['notes'])}")
                continue
            kwargs["source_type"] = "microscopy"
            evidence = Evidence(**kwargs, calibration=json.dumps({key: result[key] for key in
                ("scale_value", "scale_unit", "scale_bar_pixels", "units_per_pixel")}))
            experiment = Experiment(id="candidate", sample_label=plan.sample_label, study_family="candidate")
            experiment.measurements = [normalize_measurement(Measurement(experiment_id="candidate",
                outcome="Projected_Droplet_Diameter_2D", raw_value=f"Segmented area {obj['area_pixels']} pixels",
                value=obj["diameter"], unit=obj["unit"], origin="derived", status="uncertain", evidence=[evidence],
                measurement_method=result["method"], n_technical=result["n_technical"], n_independent=None,
                validation_notes=result["notes"])) for obj in result["objects"]]
            batch.experiments.append(experiment)
    if not review.plots and not review.microscopy:
        batch.abstentions.append("Figure reviewed without supported quantitative geometry")
    return _canonical_batch(batch, document, request["payload"]["unit_id"])


def _collect(workspace, document, jobs):
    experiments, abstentions = [], []
    for job in jobs:
        path = safe_path(workspace, f"artifacts/{document['sha256']}/batches/{job['id']}.json")
        if path.exists():
            if job.get("batch_hash") and file_digest(path) != job["batch_hash"]:
                raise LocalWorkflowError("Committed scientific batch was modified")
            batch = read_json(path)
            experiments.extend(batch["experiments"])
            abstentions.extend(batch["abstentions"])
        elif job["status"] in TERMINAL:
            abstentions.append(f"{job['phase']}: {job.get('reason', 'no response')}")
    return {"eligible": True, "eligibility_reason": "Primary-source candidates", "doi": document.get("doi"),
            "experiments": experiments, "abstentions": list(dict.fromkeys(abstentions)), "coverage": []}


def submit_response(workspace: Path, response: dict):
    encoded = json.dumps(response, allow_nan=False).encode()
    if len(encoded) > MAX_RESPONSE_BYTES:
        raise LocalWorkflowError("Agent response exceeds the 5 MB bound")
    parsed = FileResponse.model_validate(response)
    with workspace_lock(workspace):
        manifest = load_manifest(workspace)
        if manifest["status"] in {"paused", "cancelled", "failed"}:
            raise LocalWorkflowError("Resume the stopped run before submitting evidence")
        if manifest["engine_hash"] != _engine_hash():
            raise LocalWorkflowError("Scientific engine changed since preparation")
        request = read_json(safe_path(workspace, f"jobs/requests/{parsed.request_id}.json"))
        expected = digest_json({key: value for key, value in request.items() if key != "request_hash"})
        if parsed.request_hash != request["request_hash"] or expected != request["request_hash"]:
            raise LocalWorkflowError("Response request hash does not match the frozen job")
        for relative, digest in request["input_hashes"].items():
            if file_digest(safe_path(workspace, relative)) != digest:
                raise LocalWorkflowError("Frozen job input was modified")
        state_path = safe_path(workspace, f"jobs/state/{parsed.request_id}.json")
        state = read_json(state_path)
        response_path = safe_path(workspace, f"jobs/responses/{parsed.request_id}.json")
        if response_path.exists():
            previous = read_json(response_path)
            if previous.get("submitted_result_hash") != digest_json(parsed.result):
                raise LocalWorkflowError("A completed response is immutable")
            recover_committed_responses(workspace, manifest)
            synchronize(workspace, manifest)
            return {"status": "already_received", "request_id": parsed.request_id}
        if state["status"] != "claimed" or parsed.claim_token != state["claim_token"]:
            raise LocalWorkflowError("Response needs the currently claimed job")
        result = SCHEMAS[request["phase"]].model_validate(parsed.result)
        document = _document(manifest, request["document_hash"])
        if document.get("status") in BLOCKED_SOURCES:
            raise LocalWorkflowError("Publication or source is flagged; evidence remains quarantined")
        if state.get("inventory_hash") != document.get("inventory_hash"):
            raise LocalWorkflowError("Source job was superseded by a refreshed bundle")
        if request["phase"] == "screening":
            result.experiments, result.coverage = [], []
            all_text = " ".join(unit["text"] for unit in _inventory(workspace, document)["pages"])
            printed_doi = _inventory(workspace, document).get("doi")
            if result.doi and result.doi != printed_doi and canonical_text(result.doi) not in canonical_text(all_text):
                result.abstentions.append("Screening DOI not printed in primary source; withheld")
                result.doi = None
            result.doi = result.doi or document.get("doi") or printed_doi
            result.title = result.title or document.get("title")
            document.update(doi=result.doi, title=result.title)
        elif request["phase"] in {"text_table", "figure"}:
            unit = _unit(workspace, document, request["payload"]["unit_id"])
            if request["phase"] == "text_table":
                if len(result.experiments) > 500 or sum(len(e.measurements) for e in result.experiments) > 5000:
                    raise LocalWorkflowError("Candidate count exceeds the bounded source job")
                for experiment in result.experiments:
                    for measurement in experiment.measurements:
                        measurement.normalized_value = measurement.normalized_unit = None
                        measurement.status = "candidate"
                    for observation in [*experiment.attributes, *experiment.measurements]:
                        for evidence in observation.evidence:
                            for relative in (evidence.artifact_key, getattr(evidence, "asset_path", None)):
                                if relative is not None and (relative not in request["inputs"] or
                                                            not safe_path(workspace, relative).is_file()):
                                    raise LocalWorkflowError("Candidate artifact lies outside declared source inputs")
                if document.get("source_format") == "xml":
                    batch = _verify_xml(result, _inventory(workspace, document), unit, document["sha256"])
                else:
                    batch = verify_batch(result, {unit["page"]: unit}, document["sha256"])
                batch = _canonical_batch(batch, document, request["payload"]["unit_id"])
            else:
                batch = _figures(workspace, document, unit, result, request)
            batch_path = safe_path(workspace, f"artifacts/{document['sha256']}/batches/{request['id']}.json")
            atomic_json(batch_path, batch.model_dump(mode="json"))
            state["batch_hash"] = file_digest(batch_path)
        else:
            candidate_ids = {m["id"] for e in request["payload"]["candidates"]["experiments"] for m in e["measurements"]}
            if not set(result.rejected_measurement_ids + result.uncertain_measurement_ids).issubset(candidate_ids):
                raise LocalWorkflowError("Verification refers to measurements outside the source job")
        saved = parsed.model_dump(mode="json")
        saved.update(result=result.model_dump(mode="json"), received_at=now(),
                     result_hash=digest_json(result.model_dump(mode="json")),
                     submitted_result_hash=digest_json(parsed.result), batch_hash=state.get("batch_hash"))
        atomic_json(response_path, saved)
        state.update(status="completed", completed_at=now(), response_hash=file_digest(response_path))
        atomic_json(state_path, state)
        event(workspace, "response_submitted", request_id=request["id"], phase=request["phase"],
              result_hash=saved["result_hash"], agent_metadata_is_reported=True)
        synchronize(workspace, manifest)
    return {"status": "completed", "request_id": parsed.request_id, "phase": request["phase"]}


def finalize_workspace(workspace: Path):
    with workspace_lock(workspace):
        manifest, dataset = load_manifest(workspace), load_dataset(workspace)
        if manifest["engine_hash"] != _engine_hash():
            raise LocalWorkflowError("Scientific engine changed before finalization")
        synchronize(workspace, manifest)
        jobs = load_jobs(workspace)
        experiments, limitations = {}, []
        for document in manifest["documents"]:
            if not document.get("inventory_path"):
                limitations.append(document.get("inspection_error", "Primary source inventory is pending"))
                continue
            inventory = _inventory(workspace, document)
            if file_digest(safe_path(workspace, document["source_path"])) != document["sha256"]:
                raise LocalWorkflowError("Primary source changed before scientific finalization")
            coverage = {"source_units": [], "tables": [], "figures": [],
                        "structural_pages": inventory["page_count"],
                        "structural_tables": sum(len(unit["tables"]) for unit in inventory["pages"]),
                        "structural_figures": sum(len(unit["figures"]) for unit in inventory["pages"])}
            document_jobs = [job for job in jobs if job["document_hash"] == document["sha256"]]
            document_jobs = [job for job in document_jobs if job.get("inventory_hash") == document["inventory_hash"]]
            if document.get("status") == "abstained":
                limitations.append(document.get("eligibility_reason", "Source screening abstained"))
            for unit in inventory["pages"]:
                uid = _unit_id(unit)
                unit_jobs = [job for job in document_jobs if job.get("unit_id") == uid]
                batch = ExtractionBatch.model_validate(_collect(workspace, document, [job for job in unit_jobs
                                                     if job["phase"] in {"text_table", "figure"}]))
                verification = next((job for job in unit_jobs if job["phase"] == "verification"), None)
                resolution = next((job for job in unit_jobs if job["phase"] == "resolution"), None)
                review = _phase_result(workspace, verification) if verification else None
                if review:
                    batch = apply_review(batch, VerificationReview.model_validate(review))
                else:
                    for experiment in batch.experiments:
                        for measurement in experiment.measurements:
                            if measurement.status == "accepted":
                                measurement.status = "uncertain"
                                measurement.validation_notes.append("Independent source verification is incomplete")
                if document.get("status") in BLOCKED_SOURCES:
                    for experiment in batch.experiments:
                        for measurement in experiment.measurements:
                            measurement.status = "stale"
                            measurement.validation_notes.append("Publication or primary source requires reassessment")
                    batch.abstentions.append("Flagged publication/source is excluded from current synthesis")
                if resolution:
                    resolved = _phase_result(workspace, resolution)
                    if resolved:
                        batch = apply_review(batch, VerificationReview.model_validate(resolved))
                    else:
                        batch.abstentions.append("Bounded discrepancy resolution unavailable; uncertainty retained")
                phase_done = verification is not None and verification["status"] in TERMINAL and \
                    (resolution is None or resolution["status"] in TERMINAL)
                coverage["source_units"].append({"unit_id": uid, "page": unit.get("page"),
                    "xml_element": unit.get("xml_element"), "text_attempted": any(j["phase"] == "text_table" and
                        j["attempts"] > 0 for j in unit_jobs), "verification_completed": bool(review),
                    "status": "not_attempted_screened_excluded" if document.get("status") == "screened_excluded" else
                              "processed" if phase_done else "pending",
                    "measurements": sum(len(e.measurements) for e in batch.experiments)})
                for table in unit["tables"]:
                    linked = [m for e in batch.experiments for m in e.measurements if any(
                        table["id"] in ev.locator or (table.get("xml_element") is not None and
                            getattr(ev, "xml_element", None) == table["xml_element"])
                        for ev in m.evidence)]
                    coverage["tables"].append({"id": table["id"], "unit_id": uid,
                        "status": "examined" if review else "pending", "supported_measurements": len(linked)})
                for figure in unit["figures"]:
                    figure_job = next((j for j in unit_jobs if j["phase"] == "figure" and
                                       j.get("region_id") == figure["id"]), None)
                    coverage["figures"].append({"id": figure["id"], "unit_id": uid,
                        "supported_measurements": sum(1 for experiment in batch.experiments for measurement in experiment.measurements
                            if any(e.figure_id == figure["id"] for e in measurement.evidence)),
                        "status": "reviewed" if figure_job and figure_job["status"] == "completed" else
                                  "abstained" if figure_job and figure_job["status"] in TERMINAL else "pending"})
                limitations.extend(batch.abstentions)
                for experiment in batch.experiments:
                    existing = experiments.get(experiment.id)
                    if existing:
                        mids = {m["id"] for m in existing["measurements"]}
                        existing["measurements"].extend(m.model_dump(mode="json") for m in experiment.measurements if m.id not in mids)
                    else:
                        experiments[experiment.id] = experiment.model_dump(mode="json")
            document["coverage"] = coverage
            pending = any(job["status"] not in TERMINAL for job in document_jobs)
            if document.get("status") not in {"screened_excluded", "abstained", *BLOCKED_SOURCES}:
                document["status"] = "pending" if pending else "completed"
        if any(m["status"] in {"uncertain", "rejected", "stale"} for e in experiments.values() for m in e["measurements"]):
            limitations.append("Uncertain and rejected evidence remains visible and excluded from numerical synthesis")
        local_publications = {p["id"]: p for p in dataset.get("publications", [])}
        for document in manifest["documents"]:
            if document.get("publication_id"):
                continue
            publication_id = "local-" + document["sha256"]
            local_publications[publication_id] = {"id": publication_id, "source": "local", "source_id": document["sha256"],
                "doi": document.get("doi"), "pmid": document.get("pmid"), "pmcid": document.get("pmcid"),
                "title": document.get("title"), "document_id": document["id"],
                "state": "extracted" if document.get("status") == "completed" else
                         "screened_excluded" if document.get("status") == "screened_excluded" else "needs_review",
                "status": document.get("status") if document.get("status") in BLOCKED_SOURCES else "active"}
        dataset["publications"] = list(local_publications.values())
        document_publications = {d["sha256"]: d.get("publication_id") or "local-" + d["sha256"] for d in manifest["documents"]}
        conditions, families = [], {}
        for experiment in experiments.values():
            mids = [m["id"] for m in experiment["measurements"]]
            cid = next((m.get("condition_id") for m in experiment["measurements"] if m.get("condition_id")),
                       digest_json({"experiment_id": experiment["id"], "conditions": experiment["attributes"]})[:32])
            conditions.append({"id": cid, "experiment_id": experiment["id"], "sample_label": experiment["sample_label"],
                               "attributes": experiment["attributes"], "measurement_ids": mids})
            family = families.setdefault(experiment["study_family"], {"id": experiment["study_family"],
                "publication_dois": [], "publication_ids": [], "document_ids": [], "experiment_ids": [], "relationship_basis": "exact DOI or source hash"})
            family["experiment_ids"].append(experiment["id"])
            if experiment.get("doi") and experiment["doi"] not in family["publication_dois"]:
                family["publication_dois"].append(experiment["doi"])
            for measurement in experiment["measurements"]:
                for evidence in measurement["evidence"]:
                    if evidence["document_hash"] not in family["document_ids"]:
                        family["document_ids"].append(evidence["document_hash"])
                    publication_id = document_publications.get(evidence["document_hash"])
                    if publication_id and publication_id not in family["publication_ids"]:
                        family["publication_ids"].append(publication_id)
        dataset.update(experiments=list(experiments.values()), conditions=conditions, study_families=list(families.values()), limitations=list(dict.fromkeys(limitations)),
                       protocol_hash=manifest["protocol_hash"], engine_hash=manifest["engine_hash"], updated_at=now())
        EvidenceDataset.model_validate(dataset)
        complete = bool(manifest["documents"]) and all(document.get("status") in {
            "completed", "screened_excluded", "abstained", *BLOCKED_SOURCES} for document in manifest["documents"])
        if manifest["status"] not in {"paused", "cancelled", "failed"}:
            manifest["status"] = "completed_with_abstentions" if complete and limitations else "completed" if complete else "running"
            if not manifest["documents"]:
                manifest.update(status="failed", status_reason="No supported primary sources are prepared")
        publication_documents = {document.get("publication_id"): document for document in manifest["documents"]
                                 if document.get("publication_id")}
        for publication in dataset.get("publications", []):
            document = publication_documents.get(publication.get("id"))
            if document and document.get("status") == "completed" and publication.get("status", "active") == "active":
                publication["state"] = "extracted"
        manifest["synthesis_stale"] = not complete or any(j["status"] in {"abstained", "failed"} for j in jobs) or any(d.get("status") in BLOCKED_SOURCES for d in manifest["documents"]) or bool(
            dataset.get("publications") and any(p.get("state", "pending_extraction") in
                {"pending_extraction", "needs_review"} for p in dataset["publications"]))
        completed_times = [j.get("completed_at") or j.get("released_at") for j in jobs]
        completed_times = [value for value in completed_times if value]
        if complete and completed_times:
            manifest["last_extraction"] = max(completed_times)
        manifest["updated_at"] = now()
        atomic_json(safe_path(workspace, "dataset.json"), dataset)
        snapshot_id = digest_json({"dataset": dataset, "manifest": manifest})
        atomic_json(safe_path(workspace, "manifest.json"), manifest)
        snapshot = {**load_snapshot(workspace), "id": snapshot_id, "created_at": now()}
        atomic_json(safe_path(workspace, f"snapshots/{snapshot_id}.json"), snapshot)
        manifest["latest_snapshot"] = f"snapshots/{snapshot_id}.json"
        atomic_json(safe_path(workspace, "manifest.json"), manifest)
        event(workspace, "snapshot_finalized", snapshot_id=snapshot_id, status=manifest["status"])
    return snapshot
