"""Private local persistence and atomic claims; no database or hosted services."""

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from uuid import uuid4

from .contracts import MAX_ATTEMPTS, LocalWorkflowError

BLOCKED_SOURCES = {"quarantined", "stale", "retracted", "withdrawn", "concern", "superseded", "corrected"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_signature(dataset):
    return digest_json({key: value for key, value in dataset.items() if key != "updated_at"})


def assert_private_workspace(workspace):
    root = Path(workspace).expanduser().resolve()
    for parent in (Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents):
        if (parent / "pyproject.toml").exists() and (parent / "src/livingmeta").exists():
            if root.is_relative_to(parent):
                raise LocalWorkflowError("The private workspace must be outside the code checkout")
            break
    return root


def safe_path(workspace, relative):
    base = Path(workspace).resolve()
    path = PurePosixPath(str(relative))
    if not str(relative) or not path.parts or path.is_absolute() or ".." in path.parts or "\\" in str(relative):
        raise LocalWorkflowError("Artifact paths must be relative and remain inside the workspace")
    target = base / path
    if not target.resolve().is_relative_to(base):
        raise LocalWorkflowError("Artifact path escapes the private workspace")
    current = target
    while current != base:
        if current.is_symlink():
            raise LocalWorkflowError("Symbolic links are not allowed in workspace artifacts")
        current = current.parent
    return target


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    content = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_manifest(workspace):
    manifest = read_json(safe_path(workspace, "manifest.json"))
    if manifest.get("schema_version") != 2:
        raise LocalWorkflowError("Unsupported local workspace schema")
    if manifest.get("protocol_hash"):
        if digest_json(manifest["protocol"]) != manifest["protocol_hash"]:
            raise LocalWorkflowError("Frozen protocol was modified")
        protocol_path = safe_path(workspace, "protocol.json")
        if not protocol_path.exists() or digest_json(read_json(protocol_path)) != manifest["protocol_hash"]:
            raise LocalWorkflowError("Frozen protocol file was modified")
    return manifest


def load_dataset(workspace):
    path = safe_path(workspace, "dataset.json")
    value = read_json(path) if path.exists() else {"schema_version": 2, "experiments": [],
                                                "publications": [], "limitations": []}
    if value.get("schema_version") != 2:
        raise LocalWorkflowError("Unsupported evidence dataset schema")
    return value


def load_snapshot(workspace):
    """Build the flat, read-only report contract from the latest durable state."""
    manifest, dataset = load_manifest(workspace), load_dataset(workspace)
    synthesis_path = safe_path(workspace, "synthesis.json")
    synthesis = read_json(synthesis_path) if synthesis_path.exists() else {}
    if synthesis:
        synthesis = {**synthesis, "status": synthesis.get("inferential", {}).get("status", "descriptive")}
        if synthesis.get("dataset_hash") != dataset_signature(dataset):
            synthesis.update(status="historical", synthesis_stale=True)
    artifacts = []
    responses = []
    for job in load_jobs(workspace):
        path = safe_path(workspace, f"jobs/responses/{job['id']}.json")
        if not path.exists():
            continue
        if job.get("response_hash") and file_digest(path) != job["response_hash"]:
            raise LocalWorkflowError("Committed agent response was modified")
        payload = read_json(path)
        responses.append({"request_id": job["id"], "phase": job["phase"], "agent": payload.get("agent", {}),
                          "metadata": payload.get("metadata", {}), "received_at": payload.get("received_at"),
                          "response_hash": job.get("response_hash")})
    artifacts_root = safe_path(workspace, "artifacts")
    if artifacts_root.exists():
        for path in sorted(artifacts_root.rglob("*")):
            if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".png", ".jpg", ".webp"}:
                relative = path.relative_to(Path(workspace).resolve()).as_posix()
                artifacts.append({"id": file_digest(path), "sha256": file_digest(path), "filename": path.name,
                                  "relative_path": relative, "artifact_key": relative})
    return {"schema_version": 2, "project": {"id": manifest["id"], "name": manifest["name"]},
            "protocol": manifest["protocol"], "documents": manifest["documents"],
            "experiments": dataset["experiments"], "publications": dataset.get("publications", []),
            "conditions": dataset.get("conditions", []), "study_families": dataset.get("study_families", []),
            "freshness": {**{key: manifest.get(key) for key in
                          ("last_metadata_check", "last_extraction", "last_synthesis_update")},
                          "synthesis_stale": bool(manifest.get("synthesis_stale", True) or synthesis.get("synthesis_stale"))},
            "coverage": {document["id"]: document.get("coverage", {}) for document in manifest["documents"]},
            "synthesis": synthesis, "benchmark": dataset.get("benchmark", {}),
            "run": {"id": manifest["id"], "status": manifest["status"], "jobs": load_jobs(workspace),
                    "limitations": dataset.get("limitations", [])},
            "provenance": {**{key: manifest.get(key) for key in
                           ("protocol_hash", "engine_hash", "prompt_version", "agent_usage", "blinding")},
                           "responses": responses},
            "artifacts": artifacts, "embedded_artifacts": []}


@contextmanager
def workspace_lock(workspace):
    root = assert_private_workspace(workspace)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".workspace.lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield root
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def event(workspace, kind, **payload):
    record = {"timestamp": now(), "kind": kind, **payload}
    with safe_path(workspace, "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_jobs(workspace):
    return [read_json(path) for path in sorted(safe_path(workspace, "jobs/state").glob("*.json"))]


def pending_job_count(workspace):
    documents = {d["sha256"]: d for d in load_manifest(workspace)["documents"]}
    return sum(job["status"] in {"pending", "claimed"} and job["document_hash"] in documents and
               documents[job["document_hash"]].get("status") not in BLOCKED_SOURCES and
               job.get("inventory_hash") == documents[job["document_hash"]].get("inventory_hash")
               for job in load_jobs(workspace))


def update_run_status(workspace, status, reason=None):
    if status not in {"ready", "running", "paused", "failed", "cancelled", "completed", "completed_with_abstentions"}:
        raise LocalWorkflowError("Unknown local run status")
    with workspace_lock(workspace):
        manifest = load_manifest(workspace)
        manifest.update(status=status, updated_at=now())
        if reason:
            manifest["status_reason"] = str(reason)[:500]
        atomic_json(safe_path(workspace, "manifest.json"), manifest)
        event(workspace, "run_status", status=status, reason=str(reason)[:500] if reason else None)
    return manifest


def next_job(workspace, worker="portable", reclaim=False):
    from .workflow import recover_committed_responses, synchronize
    with workspace_lock(workspace):
        manifest = load_manifest(workspace)
        if manifest["status"] in {"paused", "cancelled", "failed"} and not reclaim:
            return None
        if reclaim:
            manifest.update(status="ready", updated_at=now())
            atomic_json(safe_path(workspace, "manifest.json"), manifest)
        recover_committed_responses(workspace, manifest)
        synchronize(workspace, manifest)
        documents = {document["sha256"]: document for document in manifest["documents"]}
        for job in load_jobs(workspace):
            document = documents.get(job["document_hash"])
            if (not document or document.get("status") in BLOCKED_SOURCES or
                    job.get("inventory_hash") != document.get("inventory_hash")):
                continue
            if job["status"] == "claimed" and reclaim:
                expires = datetime.fromisoformat(job["expires_at"])
                if expires <= datetime.now(timezone.utc):
                    job["status"] = "pending" if job["attempts"] < MAX_ATTEMPTS else "abstained"
                    job["reason"] = "Expired claim explicitly reclaimed; prior agent usage is unknown"
                    atomic_json(safe_path(workspace, f"jobs/state/{job['id']}.json"), job)
                    event(workspace, "claim_reclaimed", request_id=job["id"])
            if job["status"] != "pending" or job["attempts"] >= MAX_ATTEMPTS:
                continue
            request = read_json(safe_path(workspace, f"jobs/requests/{job['id']}.json"))
            job.update(status="claimed", worker=str(worker)[:100], attempts=job["attempts"] + 1,
                       claim_token=uuid4().hex, claimed_at=now(),
                       expires_at=(datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat())
            atomic_json(safe_path(workspace, f"jobs/state/{job['id']}.json"), job)
            manifest.update(status="running", updated_at=now())
            atomic_json(safe_path(workspace, "manifest.json"), manifest)
            event(workspace, "job_claimed", request_id=job["id"], phase=request["phase"],
                  worker=job["worker"], attempt=job["attempts"])
            return {**request, "claim_token": job["claim_token"], "attempt": job["attempts"],
                    "worker": job["worker"], "expires_at": job["expires_at"]}
        return None


def release_job(workspace, request_id, reason, *, claim_token, failed=False, interrupted=False):
    """Release only the lease owned by this worker; stale workers cannot change it."""
    with workspace_lock(workspace):
        path = safe_path(workspace, f"jobs/state/{request_id}.json")
        job = read_json(path)
        if job["status"] != "claimed" or job.get("claim_token") != claim_token:
            return job
        if interrupted and job["status"] == "claimed":
            job["attempts"] = max(0, job["attempts"] - 1)
            job["interruption_count"] = job.get("interruption_count", 0) + 1
        terminal = job["attempts"] >= MAX_ATTEMPTS
        job.update(status="abstained" if terminal else "pending", reason=str(reason)[:500], released_at=now())
        atomic_json(path, job)
        event(workspace, "job_abstained" if terminal else "job_released", request_id=request_id,
              reason=job["reason"], attempt=job["attempts"], failed=failed, interrupted=interrupted)
        from .workflow import synchronize
        synchronize(workspace, load_manifest(workspace))
    return job


def renew_claim(workspace, request_id, claim_token):
    """Keep a running native process leased so explicit resume cannot duplicate it."""
    with workspace_lock(workspace):
        path = safe_path(workspace, f"jobs/state/{request_id}.json")
        job = read_json(path)
        if job["status"] != "claimed" or job.get("claim_token") != claim_token:
            raise LocalWorkflowError("The running process no longer owns this job")
        job["expires_at"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        atomic_json(path, job)
