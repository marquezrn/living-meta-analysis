"""Idempotent weekly metadata monitoring, with no model calls or extraction dispatch."""

import asyncio
import re
import secrets
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from .auth import aware
from .db import Document, ExperimentRecord, MeasurementRecord, Project, PublicationRecord, utcnow
from .discovery.identity import canonical_doi, preserve_status
from .domain import Protocol


def next_weekly_check(now: datetime, timezone_name="Europe/Madrid", weekday=0, hour=8):
    local = aware(now).astimezone(ZoneInfo(timezone_name))
    delta = (weekday - local.weekday()) % 7
    target = (local + timedelta(days=delta)).replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= local:
        target += timedelta(days=7)
    return target.astimezone(timezone.utc)


def citation_key(citation):
    if doi := canonical_doi(citation.doi):
        return "doi:" + doi
    source_id = citation.source_id
    if citation.source == "arxiv":
        source_id = re.sub(r"v\d+$", "", source_id).removeprefix("http://").removeprefix("https://")
    return citation.source + ":" + source_id


def _claim(project_id, session_factory, now, scheduled):
    """A short transaction claims the project; network work occurs after it commits."""
    with session_factory() as db:
        if db.bind.dialect.name == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None:
            return None, {"error": "Project not found"}
        if scheduled and (project.next_metadata_check is None or aware(project.next_metadata_check) > now):
            return None, {"status": "not_due", "openai_calls": 0}
        checkpoint = dict(project.discovery_checkpoint or {})
        lease = checkpoint.get("monitor_lease")
        if lease and aware(datetime.fromisoformat(lease["expires_at"])) > now:
            return None, {"status": "already_running", "openai_calls": 0}
        token = secrets.token_hex(16)
        checkpoint["monitor_lease"] = {"token": token, "expires_at": (now + timedelta(minutes=15)).isoformat()}
        project.discovery_checkpoint = checkpoint
        publications = db.scalars(select(PublicationRecord).where(PublicationRecord.project_id == project.id)).all()
        documents = db.scalars(select(Document).where(Document.project_id == project.id)).all()
        known = sorted({doi for value in [r.doi for r in publications] + [d.doi for d in documents]
                        if (doi := canonical_doi(value))})
        snapshot = {"protocol": dict(project.protocol), "since": aware(project.last_metadata_check) if project.last_metadata_check else None,
                    "checkpoint": checkpoint, "known_dois": known, "token": token}
        db.commit()
        return snapshot, None


def _invalidate_evidence(db, project, doi, status):
    if not doi:
        return
    documents = db.scalars(select(Document).where(Document.project_id == project.id)).all()
    for document in documents:
        if canonical_doi(document.doi) != doi:
            continue
        if status == "active":
            continue
        document.status = "stale" if status == "corrected" else "quarantined"
        for experiment in db.scalars(select(ExperimentRecord).where(ExperimentRecord.document_id == document.id)):
            experiment.source_status = preserve_status(experiment.source_status, status)
        for measurement in db.scalars(select(MeasurementRecord).where(MeasurementRecord.document_id == document.id)):
            note = f"Publication status: {status}; evidence requires reassessment"
            notes = list(measurement.payload.get("validation_notes", []))
            if note not in notes:
                notes.append(note)
            measurement.payload = {**measurement.payload, "status": "stale", "validation_notes": notes}
        project.synthesis_stale = True


async def monitor_project(project_id, session_factory, settings, *, scheduled=False):
    from .discovery.service import DiscoveryResult, check_publication_updates, discover
    started = utcnow()
    snapshot, early = _claim(project_id, session_factory, started, scheduled)
    if early is not None:
        return early
    protocol = Protocol.model_validate(snapshot["protocol"])
    checkpoint = snapshot["checkpoint"]
    skipped_scopus = "scopus" in protocol.enabled_sources and not settings.scopus_verified
    if skipped_scopus:
        protocol.enabled_sources = [source for source in protocol.enabled_sources if source != "scopus"]
    credentials = {"contact_email": settings.contact_email,
                   "openalex_api_key": settings.openalex_api_key, "pubmed_api_key": settings.ncbi_api_key,
                   "scopus_api_key": settings.elsevier_api_key, "scopus_insttoken": settings.elsevier_insttoken}
    if settings.task_mode == "celery" or settings.production:
        credentials["redis_url"] = settings.broker_url

    async def metadata_checks():
        discovery = await discover(protocol, since=snapshot["since"], credentials=credentials,
                                   checkpoint=checkpoint)
        updates = await check_publication_updates(snapshot["known_dois"], credentials,
                                                  checkpoint.get("known_updates"))
        discovery.source_runs.extend(updates.source_runs)
        discovery.errors.extend(updates.errors)
        # Correction notices are signals, not new experiments pending extraction.
        discovery.checkpoint["known_updates"] = updates.checkpoint
        discovery.complete = discovery.complete and updates.complete
        return discovery
    try:
        # A crashed monitoring worker cannot retain its claim indefinitely.
        result = await asyncio.wait_for(metadata_checks(), timeout=600)
    except Exception as exc:
        result = DiscoveryResult(errors=[f"Metadata monitoring failed: {type(exc).__name__}"],
                                 checkpoint={k: v for k, v in checkpoint.items() if k != "monitor_lease"})
    errors = list(result.errors)
    if skipped_scopus:
        errors.append("Scopus skipped: server entitlement has not been verified")
        result.complete = False
        result.source_runs.append({"source": "scopus", "status": "unavailable", "complete": False})
    added, changed = 0, 0
    status_updates = {}
    notice_dois = set()
    for source_run in result.source_runs:
        notice_dois.update(source_run.get("notice_dois", []))
        for value, status in source_run.get("status_updates", {}).items():
            if doi := canonical_doi(value):
                status_updates[doi] = preserve_status(status_updates.get(doi, "active"), status)
    with session_factory() as db:
        project = db.get(Project, project_id)
        if project is None:
            return {"error": "Project removed during monitoring", "openai_calls": 0}
        current_lease = (project.discovery_checkpoint or {}).get("monitor_lease", {})
        if current_lease.get("token") != snapshot["token"]:
            return {"status": "superseded", "openai_calls": 0}
        records = db.scalars(select(PublicationRecord).where(PublicationRecord.project_id == project.id)).all()
        records_by_key = {}
        for record in records:
            canonical = canonical_doi(record.doi or record.payload.get("doi"))
            key = "doi:" + canonical if canonical else record.canonical_id
            records_by_key[key] = record
            if key != record.canonical_id and not any(r.canonical_id == key for r in records):
                record.canonical_id, record.doi = key, canonical
        for citation in result.citations:
            if canonical_doi(citation.doi) in notice_dois:
                continue
            key = citation_key(citation)
            record = records_by_key.get(key)
            value = citation.model_dump(mode="json")
            value["doi"] = canonical_doi(citation.doi)
            prior_status = record.payload.get("status", "active") if record else "active"
            value["status"] = preserve_status(prior_status,
                                               preserve_status(citation.status, status_updates.get(value["doi"], "active")))
            if record is None:
                record = PublicationRecord(project_id=project.id, canonical_id=key, doi=value["doi"],
                                           payload=value, sources=[citation.source], state="pending_extraction")
                db.add(record)
                records_by_key[key] = record
                added += 1
            else:
                if record.payload != value:
                    changed += 1
                    record.payload = value
                record.sources = sorted(set((record.sources or []) + [citation.source]))
            if value["status"] != "active":
                record.state = "stale" if value["status"] == "corrected" else "quarantined"
                _invalidate_evidence(db, project, value["doi"], value["status"])
            else:
                matching = db.scalars(select(Document).where(Document.project_id == project.id)).all()
                matching = [d for d in matching if canonical_doi(d.doi) == value["doi"] and value["doi"]]
                if matching:
                    record.state = "active" if all(d.status == "extracted" for d in matching) else "pending_extraction"
        # Apply notices to previously known papers even when absent from topic results.
        for doi, status in status_updates.items():
            if status == "active":
                continue
            record = records_by_key.get("doi:" + doi)
            if record:
                prior = record.payload.get("status", "active")
                effective = preserve_status(prior, status)
                if effective != prior:
                    record.payload = {**record.payload, "status": effective}
                    changed += 1
                record.state = "stale" if effective == "corrected" else "quarantined"
                _invalidate_evidence(db, project, doi, effective)
            else:
                _invalidate_evidence(db, project, doi, status)
        finished = utcnow()
        project.discovery_checkpoint = {} if result.complete else result.checkpoint
        payload = result.model_dump(mode="json")
        project.discovery_report = {**payload, "added": added, "changed": changed, "errors": errors,
                                    "mode": "metadata_only", "openai_calls": 0,
                                    "last_attempt_at": finished.isoformat(), "complete": result.complete}
        if result.complete:
            project.last_metadata_check = started
            project.next_metadata_check = next_weekly_check(finished, settings.monitor_timezone,
                                                            settings.monitor_weekday, settings.monitor_hour)
        else:
            # Resume failed or bounded metadata checks without relabelling their freshness.
            project.next_metadata_check = finished + timedelta(hours=1)
        if added or changed or not result.complete:
            project.synthesis_stale = True
        from .families import reconcile_families
        reconcile_families(db, project_id)
        db.commit()
        return project.discovery_report


def run_due_monitoring(session_factory, settings):
    with session_factory() as db:
        if db.bind.dialect.name == "postgresql":
            if not db.scalar(text("SELECT pg_try_advisory_lock(713026)")):
                return []
        try:
            due = db.scalars(select(Project.id).where(Project.next_metadata_check.is_not(None),
                                                      Project.next_metadata_check <= utcnow())).all()
            return [asyncio.run(monitor_project(project_id, session_factory, settings, scheduled=True))
                    for project_id in due]
        finally:
            if db.bind.dialect.name == "postgresql":
                db.execute(text("SELECT pg_advisory_unlock(713026)"))
