"""Synthetic sources and portable agent responses; no network or paid model calls."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from livingmeta.domain import Protocol
from livingmeta.local.contracts import LocalWorkflowError
from livingmeta.local.workflow import (finalize_workspace, next_job, prepare_workspace,
                                       refresh_workspace, submit_response)
from livingmeta.local.workspace import (atomic_json, file_digest, load_jobs, load_manifest,
                                        load_snapshot, read_json, release_job, safe_path,
                                        update_run_status)

TEXT = "Droplet size 7 um; SD 1 um; 3 independent preparations."


def make_pdf(path, texts):
    """Minimal synthetic PDF, independent of optional hosted extraction fixtures."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    ids = []
    for text in texts:
        page, stream = len(objects) + 1, len(objects) + 2
        ids.append(page)
        text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = f"BT /F1 12 Tf 40 740 Td ({text}) Tj ET".encode("ascii")
        objects.extend([f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream} 0 R >>".encode(),
                        f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream"])
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in ids)}] /Count {len(ids)} >>".encode()
    output, offsets = bytearray(b"%PDF-1.4\n"), [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(output)


@pytest.fixture
def prepared(tmp_path):
    papers = tmp_path / "papers"
    papers.mkdir()
    make_pdf(papers / "synthetic.pdf", [TEXT])
    workspace = tmp_path / "private"
    prepare_workspace(papers, workspace, Protocol())
    return papers, workspace


def response(job, result):
    return {"schema_version": 2, "request_id": job["id"], "request_hash": job["request_hash"],
            "claim_token": job["claim_token"], "agent": {"client": "synthetic-test", "model": "fake"},
            "result": result}


def extraction(job, value=7, experiments=None):
    evidence = {"document_hash": job["document_hash"], "page": job["payload"].get("page"),
                "source_type": "text", "locator": "Synthetic paragraph", "excerpt": TEXT}
    if job["payload"].get("source_format") == "xml":
        evidence.update(source_format="xml", xml_element="id:p1")
    measurement = {"experiment_id": "untrusted", "id": "untrusted", "outcome": "Droplet_Size_um",
                   "raw_value": str(value), "value": value, "unit": "um", "uncertainty": 1,
                   "uncertainty_type": "SD", "n_independent": 3, "normalized_value": 999,
                   "normalized_unit": "forged", "status": "accepted", "evidence": [evidence]}
    return {"eligible": True, "eligibility_reason": "Synthetic original experiment", "experiments": experiments or [
        {"id": "untrusted", "sample_label": "Sample A", "study_family": "untrusted",
         "attributes": [], "measurements": [measurement]}]}


def drain(workspace, uncertain=False):
    phases = []
    while (job := next_job(workspace)) is not None:
        phases.append(job["phase"])
        if job["phase"] == "screening":
            result = {"eligible": True, "eligibility_reason": "Synthetic original experiment"}
        elif job["phase"] == "text_table":
            result = extraction(job)
        elif job["phase"] == "figure":
            result = {"plots": [], "microscopy": [], "abstentions": []}
        elif job["phase"] == "verification" and uncertain:
            result = {"uncertain_measurement_ids": [job["payload"]["candidates"]["experiments"][0]["measurements"][0]["id"]],
                      "reasons": ["Synthetic unresolved sample assignment"]}
        else:
            result = {}
        submit_response(workspace, response(job, result))
    return phases


def test_full_portable_run_preserves_evidence_and_normalizes(prepared):
    _, workspace = prepared
    assert drain(workspace) == ["screening", "text_table", "verification"]
    snapshot = finalize_workspace(workspace)
    assert snapshot["run"]["status"] == "completed"
    measurement = snapshot["experiments"][0]["measurements"][0]
    assert measurement["status"] == "accepted"
    assert measurement["normalized_value"] == 7
    assert measurement["normalized_unit"] != "forged"
    assert measurement["id"] != "untrusted"
    assert measurement["evidence"][0]["document_hash"] == snapshot["documents"][0]["sha256"]
    assert snapshot["provenance"]["agent_usage"]["separate_api_calls"] == 0
    assert snapshot["documents"][0]["coverage"]["source_units"][0]["verification_completed"]
    assert all(Path(path).suffix == ".json" for path in workspace.glob("jobs/responses/*.json"))


def test_committed_responses_are_idempotent_and_immutable(prepared):
    _, workspace = prepared
    job = next_job(workspace)
    data = response(job, {"eligible": True, "eligibility_reason": "Synthetic original experiment"})
    submit_response(workspace, data)
    assert submit_response(workspace, data)["status"] == "already_received"
    data["result"]["eligible"] = False
    with pytest.raises(LocalWorkflowError, match="immutable"):
        submit_response(workspace, data)
    assert len(list(workspace.glob("jobs/responses/*.json"))) == 1


def test_preparation_reuses_inventory_and_deduplicates_bytes(prepared, monkeypatch):
    papers, workspace = prepared
    (papers / "duplicate.pdf").write_bytes((papers / "synthetic.pdf").read_bytes())
    monkeypatch.setattr("livingmeta.local.workflow.inspect_pdf", lambda *a, **k: pytest.fail("Unexpected render"))
    assert len(prepare_workspace(papers, workspace, Protocol())["documents"]) == 1
    with pytest.raises(LocalWorkflowError, match="Protocol"):
        prepare_workspace(papers, workspace, Protocol(version="different"))


def test_concurrent_claims_never_assign_one_job_twice(prepared):
    _, workspace = prepared
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(lambda n: next_job(workspace, worker=f"worker-{n}"), range(8)))
    assert sum(job is not None for job in claims) == 1
    assert load_jobs(workspace)[0]["attempts"] == 1


def test_failed_attempts_are_bounded_and_report_abstention(prepared):
    _, workspace = prepared
    first = next_job(workspace)
    release_job(workspace, first["id"], "Synthetic interruption", claim_token=first["claim_token"])
    second = next_job(workspace)
    assert second["attempt"] == 2 and second["claim_token"] != first["claim_token"]
    with pytest.raises(LocalWorkflowError, match="currently claimed"):
        submit_response(workspace, response(first, {"eligible": True, "eligibility_reason": "Late result"}))
    release_job(workspace, second["id"], "Synthetic second failure", claim_token=second["claim_token"])
    assert next_job(workspace) is None
    final = finalize_workspace(workspace)
    assert final["run"]["status"] == "completed_with_abstentions"
    assert "Synthetic second failure" in final["run"]["limitations"]


def test_claim_expiry_requires_explicit_resume(prepared):
    _, workspace = prepared
    first = next_job(workspace)
    path = workspace / "jobs/state" / f"{first['id']}.json"
    state = read_json(path)
    state["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    atomic_json(path, state)
    assert next_job(workspace) is None
    resumed = next_job(workspace, reclaim=True)
    assert resumed["attempt"] == 2
    assert resumed["claim_token"] != first["claim_token"]


@pytest.mark.parametrize("status", ["paused", "cancelled", "failed"])
def test_stopped_run_blocks_agents_until_explicit_resume(prepared, status):
    _, workspace = prepared
    update_run_status(workspace, status)
    assert next_job(workspace) is None
    assert finalize_workspace(workspace)["run"]["status"] == status
    update_run_status(workspace, "ready")
    assert next_job(workspace)["phase"] == "screening"


def test_frozen_input_and_response_hashes_are_checked(prepared):
    _, workspace = prepared
    job = next_job(workspace)
    data = response(job, {"eligible": True, "eligibility_reason": "Synthetic"})
    data["request_hash"] = "0" * 64
    with pytest.raises(LocalWorkflowError, match="hash"):
        submit_response(workspace, data)
    data["request_hash"] = job["request_hash"]
    source = safe_path(workspace, load_manifest(workspace)["documents"][0]["source_path"])
    source.write_bytes(source.read_bytes() + b" altered")
    with pytest.raises(LocalWorkflowError, match="modified"):
        submit_response(workspace, data)
    assert not list(workspace.glob("jobs/responses/*.json"))


def test_unverified_or_disputed_measurements_cannot_become_accepted(prepared):
    _, workspace = prepared
    screen = next_job(workspace)
    submit_response(workspace, response(screen, {"eligible": True, "eligibility_reason": "Synthetic"}))
    text = next_job(workspace)
    submit_response(workspace, response(text, extraction(text)))
    partial = finalize_workspace(workspace)
    assert partial["experiments"][0]["measurements"][0]["status"] == "uncertain"
    review = next_job(workspace)
    mid = review["payload"]["candidates"]["experiments"][0]["measurements"][0]["id"]
    submit_response(workspace, response(review, {"uncertain_measurement_ids": [mid]}))
    resolution = next_job(workspace)
    assert resolution["phase"] == "resolution"
    submit_response(workspace, response(resolution, {}))
    final = finalize_workspace(workspace)
    assert final["run"]["status"] == "completed_with_abstentions"
    assert final["experiments"][0]["measurements"][0]["status"] == "uncertain"
    assert next_job(workspace) is None


def test_wrong_numeric_value_remains_rejected(prepared):
    _, workspace = prepared
    screen = next_job(workspace)
    submit_response(workspace, response(screen, {"eligible": True, "eligibility_reason": "Synthetic"}))
    text = next_job(workspace)
    submit_response(workspace, response(text, extraction(text, value=17)))
    verify = next_job(workspace)
    submit_response(workspace, response(verify, {}))
    result = finalize_workspace(workspace)["experiments"][0]["measurements"][0]
    assert result["status"] == "rejected"
    assert result["normalized_value"] == 17  # Retained for inspection, excluded from synthesis.


def test_unsafe_paths_and_missing_claim_tokens_are_rejected(prepared, tmp_path):
    _, workspace = prepared
    for path in ("../private.json", "/tmp/escape", "nested/../../escape", "folder\\escape", "."):
        with pytest.raises(LocalWorkflowError):
            safe_path(workspace, path)
    (workspace / "linked").symlink_to(tmp_path)
    with pytest.raises(LocalWorkflowError):
        safe_path(workspace, "linked/escape.json")
    job = next_job(workspace)
    data = response(job, {"eligible": False, "eligibility_reason": "Synthetic"})
    del data["claim_token"]
    with pytest.raises(ValidationError):
        submit_response(workspace, data)


def test_private_workspace_cannot_be_inside_public_checkout(prepared):
    papers, _ = prepared
    checkout = Path(__file__).resolve().parents[1]
    with pytest.raises(LocalWorkflowError, match="outside"):
        prepare_workspace(papers, checkout / "private-evidence", Protocol())


def test_downloaded_source_refresh_preserves_completed_checkpoints(prepared, tmp_path):
    _, workspace = prepared
    drain(workspace)
    finalize_workspace(workspace)
    document = tmp_path / "new.pdf"
    make_pdf(document, [TEXT, "Synthetic additional page."])
    digest = file_digest(document)
    relative = f"sources/{digest}/new.pdf"
    destination = safe_path(workspace, relative)
    destination.parent.mkdir(parents=True)
    destination.write_bytes(document.read_bytes())
    manifest = load_manifest(workspace)
    manifest["documents"].append({"id": digest, "sha256": digest, "document_hash": digest,
        "filename": "new.pdf", "source_path": relative, "source_format": "pdf", "status": "pending"})
    atomic_json(workspace / "manifest.json", manifest)
    refresh_workspace(workspace)
    assert next_job(workspace)["document_hash"] == digest
    assert sum(job["status"] == "completed" for job in load_jobs(workspace)) == 3
    assert load_snapshot(workspace)["freshness"]["synthesis_stale"]


def test_events_record_agent_usage_without_fabricating_price(prepared):
    _, workspace = prepared
    drain(workspace)
    events = [json.loads(line) for line in (workspace / "events.jsonl").read_text().splitlines()]
    assert len([event for event in events if event["kind"] == "response_submitted"]) == 3
    assert load_manifest(workspace)["agent_usage"]["monetary_cost"] is None


def test_protocol_changes_after_preparation_are_rejected(prepared):
    _, workspace = prepared
    path = workspace / "protocol.json"
    protocol = read_json(path)
    protocol["analysis_mode"] = "inferential"
    atomic_json(path, protocol)
    with pytest.raises(LocalWorkflowError, match="protocol file"):
        next_job(workspace)


def test_failed_runner_attempt_retries_once(prepared):
    _, workspace = prepared
    first = next_job(workspace)
    released = release_job(workspace, first['id'], 'Malformed result', claim_token=first['claim_token'], failed=True)
    assert released['status'] == 'pending'
    second = next_job(workspace)
    assert second['attempt'] == 2
    assert release_job(workspace, second['id'], 'Malformed again', claim_token=second['claim_token'], failed=True)['status'] == 'abstained'


def test_account_interruption_preserves_failure_allowance(prepared):
    _, workspace = prepared
    first = next_job(workspace)
    released = release_job(workspace, first['id'], 'Account allowance unavailable', claim_token=first['claim_token'], interrupted=True)
    assert released['attempts'] == 0 and released['interruption_count'] == 1
    update_run_status(workspace, 'paused')
    assert next_job(workspace) is None
    resumed = next_job(workspace, reclaim=True)
    assert resumed['attempt'] == 1 and resumed['claim_token'] != first['claim_token']


def test_response_state_crash_window_recovers_without_new_model_call(prepared, monkeypatch):
    _, workspace = prepared
    screen = next_job(workspace)
    submit_response(workspace, response(screen, {'eligible': True, 'eligibility_reason': 'Synthetic'}))
    job = next_job(workspace)
    state_path = workspace / 'jobs/state' / f"{job['id']}.json"
    old_state = read_json(state_path)
    submit_response(workspace, response(job, extraction(job)))
    atomic_json(state_path, old_state)
    recovered = next_job(workspace)
    assert recovered['phase'] == 'verification'
    state = read_json(state_path)
    assert state['status'] == 'completed' and state['attempts'] == 1
    assert state['batch_hash'] and state['response_hash']


def test_hierarchy_and_extraction_timestamp_remain_stable(prepared):
    _, workspace = prepared
    drain(workspace)
    first = finalize_workspace(workspace)
    experiment = first['experiments'][0]
    measurement = experiment['measurements'][0]
    condition = first['conditions'][0]
    family = first['study_families'][0]
    assert measurement['condition_id'] == condition['id']
    assert condition['experiment_id'] == experiment['id']
    assert measurement['id'] in condition['measurement_ids']
    assert experiment['id'] in family['experiment_ids']
    assert family['publication_ids'][0] == first['publications'][0]['id']
    assert finalize_workspace(workspace)['freshness']['last_extraction'] == first['freshness']['last_extraction']


def test_native_lease_renewal_rejects_old_owner(prepared):
    from livingmeta.local.workspace import renew_claim
    _, workspace = prepared
    job = next_job(workspace)
    path = workspace / 'jobs/state' / f"{job['id']}.json"
    state = read_json(path)
    state['expires_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    atomic_json(path, state)
    renew_claim(workspace, job['id'], job['claim_token'])
    assert next_job(workspace, reclaim=True) is None
    with pytest.raises(LocalWorkflowError, match='owns'):
        renew_claim(workspace, job['id'], 'old-owner')


def test_expired_worker_cannot_release_new_workers_claim(prepared):
    _, workspace = prepared
    old = next_job(workspace, worker='old-worker')
    path = workspace / 'jobs/state' / f"{old['id']}.json"
    state = read_json(path)
    state['expires_at'] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    atomic_json(path, state)
    current = next_job(workspace, worker='current-worker', reclaim=True)
    before = read_json(path)
    released = release_job(workspace, old['id'], 'Lease renewal failed',
                           claim_token=old['claim_token'], interrupted=True)
    assert released == before == read_json(path)
    assert released['claim_token'] == current['claim_token']
    assert released['status'] == 'claimed' and released['attempts'] == 2
