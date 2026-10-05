"""Explicit, keyless literature checks for a local workspace; no AI or server database."""

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from livingmeta.discovery.fulltext import _atomic_bytes, convert_pmc_ids, download_asset, inspect_jats, resolve_pmc, safe_asset_filename
from livingmeta.discovery.identity import canonical_doi, preserve_status
from livingmeta.discovery.service import DiscoveryResult, ProviderClient, _pubmed, check_publication_updates, discover
from livingmeta.domain import Citation, Protocol
from livingmeta.local.workspace import atomic_json, event, load_dataset, load_manifest, safe_path, workspace_lock

KEYLESS_SOURCES = frozenset({"pubmed", "europepmc", "crossref", "arxiv"})
MADRID = ZoneInfo("Europe/Madrid")


def _utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Monitoring timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def next_weekly_check(after: datetime) -> datetime:
    """Monday 08:00 Europe/Madrid, with DST and a single catch-up after sleep."""
    local = _utc(after).astimezone(MADRID)
    candidate = (local + timedelta(days=(7 - local.weekday()) % 7)).replace(hour=8, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def _identifiers(value: dict | Citation) -> set[str]:
    if isinstance(value, Citation):
        value = value.model_dump()
    result = {f"doi:{doi}"} if (doi := canonical_doi(value.get("doi"))) else set()
    for name in ("pmid", "pmcid"):
        if value.get(name):
            result.add(f"{name}:{value[name]}")
    if value.get("source_id"):
        result.add(f"{value.get('source')}:{value['source_id']}")
    result.update(value.get("identifier_aliases", []))
    result.update(f"{source['source']}:{source['id']}" for source in value.get("source_ids", []))
    return result


def _merge_publications(existing: list[dict], incoming: list[Citation]) -> list[dict]:
    records = [dict(record) for record in existing]
    for citation in incoming:
        matches = [record for record in records if _identifiers(record) & _identifiers(citation)]
        prior = {}
        for match in reversed(matches):
            prior.update(match)
        payload = citation.model_dump(mode="json")
        merged = {**prior, **{key: value for key, value in payload.items() if value is not None}}
        merged["status"] = citation.status
        for match in matches:
            merged["status"] = preserve_status(merged["status"], match.get("status", "active"))
        merged["id"] = prior.get("id") or sorted(_identifiers(citation))[0]
        merged["id_aliases"] = sorted({alias for match in matches for alias in
                                       [match.get("id"), *match.get("id_aliases", [])] if alias and alias != merged["id"]})
        merged["identifier_aliases"] = sorted(_identifiers(citation) | set().union(*(_identifiers(m) for m in matches)))
        merged["state"] = prior.get("state", "pending_extraction")
        sources = [source for match in matches for source in match.get("source_ids", [])]
        sources.append({"source": citation.source, "id": citation.source_id})
        merged["source_ids"] = list({(item["source"], item["id"]): item for item in sources}.values())
        relations = [relation for match in matches for relation in match.get("update_relations", [])] + payload["update_relations"]
        merged["update_relations"] = list({json.dumps(item, sort_keys=True): item for item in relations}.values())
        if any(r["direction"] == "updates" and r["relation"] not in
               ("CorrectedandRepublishedFrom", "RetractedandRepublishedFrom") for r in merged["update_relations"]):
            merged["state"] = "publication_notice"
        if matches:
            index = records.index(matches[0])
            records = [record for record in records if record not in matches]
            records.insert(index, merged)
        else:
            records.append(merged)
    # Outgoing notice relationships affect the target, never the notice itself.
    for record in records:
        for relation in record.get("update_relations", []):
            if relation["direction"] != "updates":
                continue
            targets = {f"{kind}:{relation[f'target_{kind}']}" for kind in ("doi", "pmid", "pmcid")
                       if relation.get(f"target_{kind}")}
            for target in records:
                if _identifiers(target) & targets:
                    target["status"] = preserve_status(target.get("status", "active"), relation["status"])
    return records


def _invalidate(manifest: dict, dataset: dict, publication: dict, reason: str):
    doi = canonical_doi(publication.get("doi"))
    affected = set()
    for document in manifest.get("documents", []):
        if (document.get("publication_id") in {publication["id"], *publication.get("id_aliases", [])} or
                (doi and canonical_doi(document.get("doi")) == doi)):
            document["status"] = "quarantined" if publication.get("status") in ("retracted", "withdrawn", "concern") else "stale"
            affected.add(document.get("sha256") or document.get("document_hash"))
    for experiment in dataset.get("experiments", []):
        for measurement in experiment.get("measurements", []):
            if ((doi and canonical_doi(experiment.get("doi")) == doi) or
                    any(e.get("document_hash") in affected for e in measurement.get("evidence", []))):
                measurement["status"] = "stale"
                notes = measurement.setdefault("validation_notes", [])
                if reason not in notes:
                    notes.append(reason)
    manifest["synthesis_stale"] = dataset["synthesis_stale"] = True
    if isinstance(dataset.get("analysis"), dict):
        dataset["analysis"]["synthesis_stale"] = True
    publication["state"] = "needs_review"


async def _known_pubmed(pmids: list[str], credentials: dict) -> DiscoveryResult:
    result = DiscoveryResult(complete=True)
    if not pmids:
        return result
    run = {"source": "pubmed_known_updates", "complete": True, "status": "completed"}
    try:
        async with ProviderClient(credentials) as client:
            for offset in range(0, len(pmids), 200):
                batch = pmids[offset:offset + 200]
                params = {"db": "pubmed", "id": ",".join(batch), "retmode": "xml", "tool": "livingmeta"}
                if credentials.get("contact_email"):
                    params["email"] = credentials["contact_email"]
                citations = _pubmed((await client.get("pubmed",
                    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params=params)).text)
                result.citations.extend(citations)
                if set(batch) - {citation.pmid for citation in citations}:
                    result.complete = False
                    run.update(complete=False, status="partial")
                    result.errors.append("Known PubMed refresh omitted records; absence is not a retraction")
    except Exception as error:
        result.complete = False
        run.update(complete=False, status="unavailable")
        result.errors.append(f"Known PubMed refresh failed: {type(error).__name__}")
    result.source_runs.append(run)
    return result


def _metadata_hash(metadata: dict) -> str:
    return hashlib.sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def _acquire(workspace: Path, citation: Citation, credentials: dict, previous: dict | None) -> dict:
    pmcid = citation.pmcid
    if not pmcid and citation.doi:
        pmcid = (await convert_pmc_ids([citation.doi], credentials)).get(citation.doi)
    if not pmcid:
        return {"status": "not_available", "limitation": "No permitted PMC version is available; upload an authorized source."}
    resolution = await resolve_pmc(pmcid, credentials)
    if resolution.publication_status != "active":
        return {"status": "needs_review", "publication_status": resolution.publication_status, "pmcid": pmcid}
    versions = resolution.version_metadata
    if not versions:
        return {"status": "source_unavailable", "pmcid": pmcid, "limitation": "No distributed version is currently available; this is not a scientific exclusion or a retraction."}
    if previous and previous.get("version") in versions:
        version = previous["version"]
    elif previous and previous.get("version"):
        return {"status": "source_unavailable", "pmcid": pmcid, "versions": list(versions),
                "limitation": "The previously selected version is no longer distributed; reassessment and explicit version selection are required."}
    else:
        published = [name for name, data in versions.items() if str(data.get("is_manuscript", "no")).lower() == "no"]
        choices = published or list(versions)
        if len(choices) != 1:
            return {"status": "version_selection_required", "pmcid": pmcid, "versions": list(versions)}
        version = choices[0]
    metadata = versions[version]
    checksum = _metadata_hash(metadata)
    license_code = str(metadata.get("license_code") or "")
    if not re.fullmatch(r"(?:CC[- ]?BY(?:[- ](?:NC|ND|SA)){0,3}|CC0|TDM|PD|PUBLIC[- ]DOMAIN)(?:[- ]?\d+(?:\.\d+)?)?", license_code.upper()):
        return {"status": "license_review_required", "pmcid": pmcid, "version": version, "license": license_code,
                "metadata_hash": checksum}
    relative = f"sources/{version}/{checksum}"
    folder = safe_path(workspace, relative)
    folder.mkdir(parents=True, exist_ok=True)
    atomic_json(folder / "source-metadata.json", metadata)
    locations = [location for location in resolution.locations if location.version == version]
    if license_code.upper() == "TDM":
        locations = [location for location in locations if location.kind in ("xml", "text")]
    if len(locations) > 100:
        raise ValueError("PMC asset count exceeds the bounded download limit")
    assets, byte_count = [], 0
    old_assets = {asset["url"]: asset for asset in (previous or {}).get("assets", [])}
    filenames = set()
    for location in locations:
        filename = safe_asset_filename(location.url)
        if filename in filenames:
            raise ValueError("PMC bundle contains conflicting asset filenames")
        filenames.add(filename)
        old = old_assets.get(location.url)
        old_path = safe_path(workspace, old["path"]) if old and old.get("path") else None
        old_bytes = (old_path.read_bytes() if old_path and old_path.is_file() and old_path.stat().st_size <= 100 * 1024 * 1024
                     and location.md5 and re.fullmatch(r"[a-fA-F0-9]{32}", location.md5) else None)
        if (old_bytes is not None and hashlib.sha256(old_bytes).hexdigest() == old["sha256"] and
                hashlib.md5(old_bytes, usedforsecurity=False).hexdigest() == location.md5.lower()):
            asset = {**old, **location.model_dump(mode="json")}
            target = safe_path(workspace, f"{relative}/{asset['filename']}")
            if target != old_path:
                _atomic_bytes(target, old_bytes)
            asset["path"] = f"{relative}/{asset['filename']}"
        else:
            asset = await download_asset(location, folder, credentials, max_bytes=min(100 * 1024 * 1024, 500 * 1024 * 1024 - byte_count))
            asset["path"] = f"{relative}/{asset['filename']}"
        byte_count += asset["bytes"]
        if byte_count > 500 * 1024 * 1024:
            raise ValueError("PMC bundle exceeds the download size limit")
        assets.append(asset)
    primary = next((asset for kind in ("xml", "pdf") for asset in assets if asset["kind"] == kind), None)
    if primary is None:
        return {"status": "not_available", "pmcid": pmcid, "limitation": "No supported primary XML or PDF was distributed."}
    if primary["kind"] == "xml":
        inspect_jats(safe_path(workspace, primary["path"]), folder)
    document = {"id": primary["sha256"], "document_hash": primary["sha256"], "sha256": primary["sha256"],
                "filename": primary["filename"], "source_path": primary["path"], "source_format": primary["kind"],
                "status": "pending", "doi": citation.doi or resolution.doi, "title": citation.title,
                "pmcid": pmcid, "version": version, "license": license_code, "assets": assets,
                "bundle_hash": _metadata_hash({"metadata_hash": checksum, "assets": [a["sha256"] for a in assets]}),
                "publication_id": next(iter(sorted(_identifiers(citation)))), "access": {
                    "provider": "pmc_aws", "license": license_code, "metadata_hash": checksum}}
    return {"status": "downloaded", "pmcid": pmcid, "version": version, "metadata_hash": checksum,
            "assets": assets, "document": document, "publication_status": "active"}


async def discover_workspace(workspace: Path | str, contact_email: str | None,
                             sources: list[str] | None = None, download: bool = False) -> dict:
    """Explicit network operation; downloaded evidence remains pending until local extraction."""
    workspace = Path(workspace).resolve()
    now = datetime.now(timezone.utc)
    token = str(uuid4())
    with workspace_lock(workspace):
        manifest, dataset = load_manifest(workspace), load_dataset(workspace)
        protocol = Protocol.model_validate(manifest.get("protocol", {}))
        enabled = list(sources if sources is not None else protocol.enabled_sources)
        if not enabled or set(enabled) - KEYLESS_SOURCES:
            raise ValueError("Local discovery supports only keyless PubMed, Europe PMC, Crossref, and optional arXiv")
        protocol.enabled_sources = enabled
        literature = dict(manifest.get("literature", {}))
        lease = literature.get("lease", {})
        if lease.get("expires_at") and _utc(lease["expires_at"]) > now:
            return {"status": "busy", "openai_calls": 0}
        query_hash = _metadata_hash({"sources": enabled, "queries": {s: protocol.queries.get(s) for s in enabled}})
        changed_query = literature.get("query_hash") != query_hash
        if changed_query:
            literature.pop("checkpoint", None)
            literature.pop("known_doi_checkpoint", None)
            literature.pop("check_started_at", None)
        literature.update(lease={"token": token, "expires_at": (now + timedelta(minutes=30)).isoformat()},
                          check_started_at=literature.get("check_started_at") or now.isoformat(), sources=enabled,
                          query_hash=query_hash)
        manifest["literature"] = literature
        atomic_json(workspace / "manifest.json", manifest)
        known = list(dataset.get("publications", []))
    credentials = {"pace_directory": str(workspace / "artifacts" / "provider-pacing")}
    if contact_email:
        credentials["contact_email"] = contact_email
    result = await discover(protocol, None if changed_query else _utc(literature.get("last_metadata_check")),
                            credentials, literature.get("checkpoint"))
    if "crossref" in enabled:
        updates = await check_publication_updates([r["doi"] for r in known if r.get("doi")], credentials,
                                                  literature.get("known_doi_checkpoint"))
        result.source_runs.extend(updates.source_runs)
        result.errors.extend(updates.errors)
        result.citations.extend(updates.citations)
        result.complete = result.complete and updates.complete
        literature["known_doi_checkpoint"] = updates.checkpoint
    if "pubmed" in enabled:
        updates = await _known_pubmed(sorted({r["pmid"] for r in known if r.get("pmid")}), credentials)
        result.source_runs.extend(updates.source_runs)
        result.errors.extend(updates.errors)
        result.citations.extend(updates.citations)
        result.complete = result.complete and updates.complete
    records = _merge_publications(known, result.citations)
    downloads, source_changes = {}, []
    tracked = literature.get("bundles", {})
    for record in records:
        previous = tracked.get(record["id"])
        if record.get("status") != "active" or record.get("state") == "publication_notice":
            continue
        try:
            if download:
                downloads[record["id"]] = await _acquire(workspace, Citation.model_validate(record), credentials, previous)
            elif previous:
                resolution = await resolve_pmc(previous["pmcid"], credentials)
                current_metadata = resolution.version_metadata.get(previous["version"])
                if resolution.publication_status != "active":
                    record["status"] = preserve_status(record["status"], resolution.publication_status)
                    source_changes.append(record["id"])
                elif current_metadata is None or _metadata_hash(current_metadata) != previous.get("metadata_hash"):
                    source_changes.append(record["id"])
                    record["access_status"] = "source_changed" if current_metadata else "source_unavailable"
        except Exception as error:
            result.complete = False
            result.errors.append(f"Access check failed for {record['id']}: {type(error).__name__}")
    with workspace_lock(workspace):
        manifest, dataset = load_manifest(workspace), load_dataset(workspace)
        if manifest.get("literature", {}).get("lease", {}).get("token") != token:
            return {"status": "superseded", "openai_calls": 0}
        # Rebase against the latest dataset so concurrent extraction/review cannot be lost.
        merged = _merge_publications(dataset.get("publications", []), [Citation.model_validate(r) for r in records])
        by_id = {r["id"]: r for r in merged}
        for snapshot in records:
            match = next((r for r in merged if _identifiers(r) & _identifiers(snapshot)), None)
            if match and snapshot.get("access_status"):
                match["access_status"] = snapshot["access_status"]
        for record in merged:
            if record.get("status") != "active" or record["id"] in source_changes:
                _invalidate(manifest, dataset, record, f"Publication or primary source changed: {record.get('status', 'active')}; reassessment required")
        bundles = dict(manifest.get("literature", {}).get("bundles", {}))
        documents = manifest.setdefault("documents", [])
        for identifier, acquisition in downloads.items():
            record = by_id.get(identifier)
            if record is None:
                continue
            record["access_status"] = acquisition["status"]
            if acquisition["status"] == "downloaded":
                previous = bundles.get(identifier)
                if previous and previous.get("metadata_hash") != acquisition["metadata_hash"]:
                    _invalidate(manifest, dataset, record, "Primary source content or metadata changed; reassessment required")
                document = acquisition["document"]
                document["publication_id"] = record["id"]
                existing = next((d for d in documents if d.get("sha256") == document["sha256"]), None)
                changed = previous is not None and previous.get("metadata_hash") != acquisition["metadata_hash"]
                if existing is None:
                    documents.append(document)
                elif changed:
                    # Reinspect even when XML is unchanged but linked figure bytes changed.
                    existing.update(document)
                    existing.pop("inventory_path", None)
                    existing.pop("inventory_hash", None)
                if existing is None or changed:
                    record["state"] = "pending_extraction"
                bundles[identifier] = acquisition
            elif (acquisition["status"] in ("needs_review", "source_unavailable") or
                  (bundles.get(identifier) and acquisition["status"] in ("license_review_required", "version_selection_required", "not_available"))):
                if acquisition.get("publication_status"):
                    record["status"] = preserve_status(record["status"], acquisition["publication_status"])
                _invalidate(manifest, dataset, record, "Primary source is unavailable or flagged; reassessment required")
        dataset["publications"] = merged
        pending = sum(r.get("state") == "pending_extraction" for r in merged)
        if pending:
            manifest["synthesis_stale"] = dataset["synthesis_stale"] = True
        report = {"status": "completed" if result.complete else "partial", "complete": result.complete,
                  "checked_at": now.isoformat(), "source_runs": result.source_runs, "errors": result.errors,
                  "publication_count": len(merged), "pending_extraction": pending,
                  "downloads": {key: value["status"] for key, value in downloads.items()}, "openai_calls": 0}
        literature.update(bundles=bundles, last_attempt=now.isoformat(), report=report, checkpoint=result.checkpoint)
        literature.pop("lease", None)
        if result.complete:
            literature["last_metadata_check"] = literature.pop("check_started_at", now.isoformat())
            literature["next_metadata_check"] = next_weekly_check(now).isoformat()
            literature.pop("next_retry", None)
            literature["checkpoint"] = {}
            literature["known_doi_checkpoint"] = {}
            manifest["last_metadata_check"] = literature["last_metadata_check"]
        else:
            literature["next_retry"] = (now + timedelta(hours=1)).isoformat()
        manifest["literature"] = literature
        manifest["updated_at"] = now.isoformat()
        atomic_json(workspace / "dataset.json", dataset)
        atomic_json(workspace / "manifest.json", manifest)
        event(workspace, "literature_checked", complete=result.complete, publications=len(merged), openai_calls=0)
    return report


async def monitor_due(workspace: Path | str, contact_email: str | None, now: datetime | None = None) -> dict:
    workspace = Path(workspace).resolve()
    now = _utc(now or datetime.now(timezone.utc))
    with workspace_lock(workspace):
        manifest = load_manifest(workspace)
        literature = manifest.get("literature", {})
        due = _utc(literature.get("next_metadata_check"))
        if due is None:
            created = _utc(manifest.get("created_at")) or now
            due = next_weekly_check(created)
        retry = _utc(literature.get("next_retry"))
        if retry:
            due = min(due, retry)
        if now < due:
            return {"status": "not_due", "next_metadata_check": due.isoformat(), "openai_calls": 0}
    return await discover_workspace(workspace, contact_email, sources=literature.get("sources"), download=False)
