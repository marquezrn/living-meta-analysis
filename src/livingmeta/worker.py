"""Durable extraction execution. The worker has no manual-reference input path."""

import asyncio
import hashlib
import json
import logging
import tempfile
from pathlib import Path

from sqlalchemy import select, text

from .agents.prompts import PROMPT_VERSION
from .agents.provider import AgentsCaller
from .budget import BudgetExhausted, BudgetLedger, RunCancelled
from .db import (Document, ExperimentRecord, MeasurementRecord, Project, PublicationRecord,
                 Reservation, Run, utcnow)
from .domain import Experiment, Protocol
from .extraction.pipeline import extract_pdf
from .storage import ArtifactStore

logger = logging.getLogger(__name__)


def _scoped_id(project_id, source_id):
    return hashlib.sha256(f"{project_id}|{source_id}".encode()).hexdigest()[:32]


def persist_batch(db, project_id, document, batch, artifact_keys):
    """Replace the current view; previous run snapshots remain immutable artifacts."""
    for old in db.scalars(select(ExperimentRecord).where(ExperimentRecord.document_id == document.id)):
        old.source_status = "superseded"
    for old in db.scalars(select(MeasurementRecord).where(MeasurementRecord.document_id == document.id)):
        old.payload = {**old.payload, "status": "stale", "validation_notes": [
            *old.payload.get("validation_notes", []), "Replaced by a later source extraction"]}
    for experiment in batch.experiments:
        source_key = experiment.id
        record = db.scalar(select(ExperimentRecord).where(ExperimentRecord.document_id == document.id,
                                                           ExperimentRecord.experiment_key == source_key))
        if record is None:
            record = ExperimentRecord(id=_scoped_id(project_id, source_key), project_id=project_id,
                document_id=document.id, experiment_key=source_key, payload={}, source_status="active")
            db.add(record)
            db.flush()
        value = experiment.model_dump(mode="json")
        value["id"] = record.id
        for attribute in value["attributes"]:
            for evidence in attribute.get("evidence", []):
                if evidence.get("artifact_key"):
                    evidence["artifact_key"] = artifact_keys.get(evidence["artifact_key"], evidence["artifact_key"])
        for measurement in value["measurements"]:
            measurement["id"] = _scoped_id(project_id, measurement["id"])
            measurement["experiment_id"] = record.id
            for evidence in measurement["evidence"]:
                if evidence.get("artifact_key"):
                    evidence["artifact_key"] = artifact_keys.get(evidence["artifact_key"], evidence["artifact_key"])
            existing = db.get(MeasurementRecord, measurement["id"])
            if existing is None:
                db.add(MeasurementRecord(id=measurement["id"], project_id=project_id,
                    document_id=document.id, experiment_id=record.id, payload=measurement))
            else:
                existing.payload = measurement
        record.payload = value
        record.source_status = "active"


def process_run(run_id, session_factory, settings, *, recovery=False, caller_factory=AgentsCaller):
    """Claim once, resume completed checkpoints, conservatively charge unknown calls."""
    ledger = BudgetLedger(session_factory, run_id, settings.max_total_openai_usd)
    with session_factory() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        run = db.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if run is None or run.status not in (("queued", "running") if recovery else ("queued",)):
            return
        if run.cancel_requested:
            run.status, run.finished_at = "cancelled", utcnow()
            db.commit()
            return
        run.status, run.started_at, run.error = "running", run.started_at or utcnow(), None
        protocol = Protocol.model_validate(run.protocol_snapshot or db.get(Project, run.project_id).protocol)
        project_id, document_ids = run.project_id, list(run.document_ids)
        prior_result = dict(run.result or {})
        db.commit()
    if recovery:
        with session_factory() as db:
            unknown = list(db.scalars(select(Reservation.id).where(Reservation.run_id == run_id,
                                                                  Reservation.status == "held")))
        for reservation_id in unknown:
            ledger.settle(reservation_id, interrupted_worker=True)
    result = {"documents": list(prior_result.get("documents", [])), "prompt_version": PROMPT_VERSION,
              "models": [settings.extraction_model, settings.verification_model],
              "protocol": protocol.model_dump(mode="json"), "abstentions": list(prior_result.get("abstentions", []))}
    store = ArtifactStore(settings)
    settings.private_directory.mkdir(parents=True, exist_ok=True)
    try:
        if not settings.openai_api_key and caller_factory is AgentsCaller:
            raise RuntimeError("OpenAI credentials are not configured; no paid call was made")
        for number, document_id in enumerate(document_ids):
            with session_factory() as db:
                run = db.get(Run, run_id)
                if run.cancel_requested:
                    raise RunCancelled("Cancellation requested")
                document = db.get(Document, document_id)
                if document is None or document.project_id != project_id:
                    raise ValueError("Run references a source outside its project")
                if document.status in ("extracted", "excluded"):
                    continue
                if document.status == "quarantined":
                    result["abstentions"].append(f"{document.filename}: quarantined source")
                    continue
                source_key, source_hash = document.storage_key, document.sha256
                checkpoint = dict(document.checkpoint)
                previous_keys = dict(document.inventory.get("artifact_keys", {}))
                expected_protocol_hash = hashlib.sha256(protocol.model_dump_json().encode()).hexdigest()
                if checkpoint and (checkpoint.get("protocol_hash") != expected_protocol_hash or
                                   checkpoint.get("prompt_version") != PROMPT_VERSION):
                    checkpoint, previous_keys = {}, {}
                document.status = "extracting"
                run.progress = {"document_index": number + 1, "document_count": len(document_ids),
                                "document_id": document_id, "stage": "inspecting_source", "updated_at": utcnow().isoformat()}
                db.commit()
            with tempfile.TemporaryDirectory(prefix="source-run-", dir=settings.private_directory) as folder:
                root = Path(folder)
                source = store.materialize(source_key, root / "source.pdf")
                if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
                    raise ValueError("Stored document hash does not match the frozen source")
                artifacts = root / "evidence"
                artifacts.mkdir()
                artifact_keys = {}
                for filename, key in previous_keys.items():
                    if Path(filename).name != filename or not key.startswith(f"projects/{project_id}/runs/"):
                        raise ValueError("Checkpoint artifact identity is invalid")
                    if filename.endswith("-overlay.png") or filename.endswith("-digitization.json"):
                        store.materialize(key, artifacts / filename)
                        artifact_keys[filename] = key

                def publish_artifacts():
                    for path in artifacts.iterdir():
                        if path.is_file() and path.suffix in (".png", ".json"):
                            content = path.read_bytes()
                            digest = hashlib.sha256(content).hexdigest()
                            key = f"projects/{project_id}/runs/{run_id}/{document_id}/{digest}/{path.name}"
                            if artifact_keys.get(path.name) != key:
                                store.put(key, content, "image/png" if path.suffix == ".png" else "application/json")
                                artifact_keys[path.name] = key

                def checkpoint_saved(state):
                    publish_artifacts()
                    with session_factory() as db:
                        doc, run = db.get(Document, document_id), db.get(Run, run_id)
                        doc.checkpoint = state
                        doc.inventory = {**doc.inventory, "artifact_keys": dict(artifact_keys)}
                        run.progress = {**run.progress, "stage": "extracting_evidence",
                            "pages_completed": sum("verified" in p for p in state.get("pages", {}).values()),
                            "screening_complete": "screening" in state, "updated_at": utcnow().isoformat(),
                            "checkpoint_key": artifact_keys.get("checkpoint.json")}
                        db.commit()
                        if run.cancel_requested:
                            raise RunCancelled("Cancellation requested after saved checkpoint")

                caller = caller_factory(before_call=ledger.reserve, after_call=ledger.settle, release_call=ledger.release_unsent,
                    api_key=settings.openai_api_key, default_model=settings.extraction_model,
                    complex_model=settings.verification_model, artifact_root=artifacts)
                batch = asyncio.run(extract_pdf(source, protocol, caller, artifacts,
                                    checkpoint=checkpoint or None, on_checkpoint=checkpoint_saved))
                publish_artifacts()
                inventory = json.loads((artifacts / "inventory.json").read_text())
                with session_factory() as db:
                    document = db.get(Document, document_id)
                    document.doi, document.pages = batch.doi, inventory["page_count"]
                    document.status, document.error = ("extracted" if batch.eligible else "excluded"), None
                    document.inventory = {**document.inventory, "page_count": inventory["page_count"], "warnings": inventory["warnings"],
                        "coverage": [c.model_dump(mode="json") for c in batch.coverage], "artifact_keys": artifact_keys,
                        "eligibility_reason": batch.eligibility_reason, "eligible": batch.eligible}
                    persist_batch(db, project_id, document, batch, artifact_keys)
                    from .families import reconcile_families
                    reconcile_families(db, project_id)
                    result["documents"] = [r for r in result["documents"] if r["document_id"] != document_id] + [{
                        "document_id": document_id, "sha256": source_hash, "eligible": batch.eligible,
                        "experiments": len(batch.experiments), "measurements": sum(len(e.measurements) for e in batch.experiments),
                        "extraction_key": artifact_keys.get("extraction.json"), "coverage": [c.model_dump(mode="json") for c in batch.coverage]}]
                    result["abstentions"].extend(batch.abstentions)
                    if any(m.status == "uncertain" for e in batch.experiments for m in e.measurements):
                        result["abstentions"].append(f"{document.filename}: measurements require adjudication")
                    db.get(Run, run_id).result = result
                    project = db.get(Project, project_id)
                    project.last_extraction, project.synthesis_stale = utcnow(), True
                    if document.doi:
                        for pub in db.scalars(select(PublicationRecord).where(PublicationRecord.project_id == project_id,
                                                                             PublicationRecord.doi == document.doi)):
                            pub.state = "active" if batch.eligible else "screened_excluded"
                    db.commit()
        from .statistics.engine import descriptive
        with session_factory() as db:
            from .api import current_experiments, pending_count
            data = [Experiment.model_validate(e) for e in current_experiments(db, project_id)]
            project = db.get(Project, project_id)
            project.synthesis = {"descriptive": descriptive(data, protocol)}
            project.last_synthesis_update = utcnow()
            unresolved = list(db.scalars(select(Document).where(Document.project_id == project_id,
                Document.status.in_(("uploaded", "extracting", "failed", "stale", "quarantined")))))
            project.synthesis_stale = bool(unresolved or pending_count(db, project_id))
            run = db.get(Run, run_id)
            run.status = "completed_with_abstentions" if result["abstentions"] else "completed"
            run.result, run.finished_at = result, utcnow()
            run.progress = {**run.progress, "stage": "finished", "updated_at": utcnow().isoformat()}
            db.commit()
    except Exception as error:
        status = "budget_exhausted" if isinstance(error, BudgetExhausted) else "cancelled" if isinstance(error, RunCancelled) else "failed"
        # Exception text from providers may contain source or credentials. Persist a bounded classification.
        message = str(error) if isinstance(error, (BudgetExhausted, RunCancelled)) or str(error).startswith("OpenAI credentials") else f"{type(error).__name__}: execution stopped; inspect private checkpoints before resuming"
        with session_factory() as db:
            run = db.get(Run, run_id)
            run.status, run.error, run.result, run.finished_at = status, message, result, utcnow()
            for document in db.scalars(select(Document).where(Document.id.in_(document_ids), Document.status == "extracting")):
                document.status, document.error = "failed", message
            db.commit()
        logger.warning("Extraction run %s stopped (%s)", run_id, status)
