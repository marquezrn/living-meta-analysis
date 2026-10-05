"""Recover interrupted request creation without agent calls or private sources."""

import json

import pytest

from livingmeta.domain import Protocol
from livingmeta.local import workflow
from livingmeta.local.contracts import LocalWorkflowError
from livingmeta.local.workspace import (atomic_json, digest_json, load_jobs, load_manifest,
                                        read_json, workspace_lock)
from test_local_workspace import TEXT, make_pdf, prepared as prepared


def reuse_request(workspace, request):
    with workspace_lock(workspace):
        manifest = load_manifest(workspace)
        document = next(document for document in manifest["documents"] if document["sha256"] == request["document_hash"])
        return workflow._job(workspace, manifest, document, request["phase"], request["payload"],
                             inputs=request["inputs"], images=request["images"])


def frozen_request(workspace):
    job = load_jobs(workspace)[0]
    return read_json(workspace / "jobs/requests" / f"{job['id']}.json")


def test_resume_recovers_request_committed_before_state(tmp_path, monkeypatch):
    papers = tmp_path / "sources"
    papers.mkdir()
    make_pdf(papers / "synthetic.pdf", [TEXT])
    workspace = tmp_path / "private"
    original = workflow.atomic_json

    def interrupted_write(path, value):
        if path.parent.name == "state":
            raise OSError("Synthetic interruption before job state commit")
        return original(path, value)

    with monkeypatch.context() as patch:
        patch.setattr(workflow, "atomic_json", interrupted_write)
        with pytest.raises(OSError, match="interruption"):
            workflow.prepare_workspace(papers, workspace, Protocol())
    request_path = next(workspace.glob("jobs/requests/*.json"))
    before = request_path.read_bytes()
    assert not load_jobs(workspace)
    claim = workflow.next_job(workspace)
    assert claim["id"] == request_path.stem and claim["attempt"] == 1
    assert request_path.read_bytes() == before
    assert len(list(workspace.glob("jobs/requests/*.json"))) == 1
    events = [json.loads(line) for line in (workspace / "events.jsonl").read_text().splitlines()]
    recovered = [event for event in events if event["kind"] == "job_state_recovered"]
    assert len(recovered) == 1 and recovered[0]["model_calls"] == 0


def test_reuse_preserves_existing_claim_state_and_request_bytes(prepared):
    _, workspace = prepared
    claim = workflow.next_job(workspace)
    request = frozen_request(workspace)
    state_path = workspace / "jobs/state" / f"{claim['id']}.json"
    request_path = workspace / "jobs/requests" / f"{claim['id']}.json"
    state_bytes, request_bytes = state_path.read_bytes(), request_path.read_bytes()
    assert reuse_request(workspace, request) == claim["id"]
    assert state_path.read_bytes() == state_bytes
    assert request_path.read_bytes() == request_bytes


@pytest.mark.parametrize("corruption", ["schema", "hash", "rehash", "json"])
def test_invalid_or_changed_orphan_request_fails_closed(prepared, corruption):
    _, workspace = prepared
    request = frozen_request(workspace)
    path = workspace / "jobs/requests" / f"{request['id']}.json"
    state_path = workspace / "jobs/state" / f"{request['id']}.json"
    state_path.unlink()
    damaged = dict(request)
    if corruption == "schema":
        damaged["phase"] = "unknown_phase"
    elif corruption == "hash":
        damaged["request_hash"] = "0" * 64
    elif corruption == "rehash":
        damaged["instructions"] = "Changed frozen instructions"
        damaged["request_hash"] = digest_json({k: v for k, v in damaged.items() if k != "request_hash"})
    if corruption == "json":
        path.write_text("{incomplete", encoding="utf-8")
    else:
        atomic_json(path, damaged)
    corrupted_bytes = path.read_bytes()
    with pytest.raises(LocalWorkflowError, match="Stored job request"):
        reuse_request(workspace, request)
    assert not state_path.exists()
    assert path.read_bytes() == corrupted_bytes


def test_missing_state_with_committed_response_does_not_start_new_attempt(prepared):
    _, workspace = prepared
    request = frozen_request(workspace)
    (workspace / "jobs/state" / f"{request['id']}.json").unlink()
    atomic_json(workspace / "jobs/responses" / f"{request['id']}.json", {"result": {}})
    with pytest.raises(LocalWorkflowError, match="restore the checkpoint"):
        reuse_request(workspace, request)
    assert not load_jobs(workspace)
