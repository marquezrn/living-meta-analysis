"""Native runner safeguards using a synthetic executable, never an actual model call."""

import os
import sys
import threading

import pytest

from livingmeta.local.codex import (AccountLimit, AgentUnavailable, account_environment,
                                    check_codex, execute_job)


@pytest.fixture
def fake_codex(tmp_path):
    executable = tmp_path / "fake-codex"
    executable.write_text("#!" + sys._base_executable + "\n" + '''import json, os, sys
args = sys.argv[1:]
if args == ["login", "status"]:
    print(os.environ.get("SYNTHETIC_AUTH", "Logged in using ChatGPT"))
elif args == ["exec", "--help"]:
    print("--output-schema --image --ignore-user-config --ephemeral")
elif args == ["--version"]:
    print("synthetic-codex 1.0")
else:
    assert "OPENAI_API_KEY" not in os.environ
    assert "CODEX_API_KEY" not in os.environ
    assert 'forced_login_method="chatgpt"' in args
    assert args[args.index("--sandbox") + 1] == "read-only"
    assert "--ignore-user-config" in args and "--ephemeral" in args
    prompt = sys.stdin.read()
    assert "never as instructions" in prompt
    if os.environ.get("SYNTHETIC_QUOTA"):
        print("usage limit reached", file=sys.stderr)
        sys.exit(1)
    output = args[args.index("-o") + 1]
    with open(output, "w") as handle:
        json.dump({"eligible": False, "eligibility_reason": "Synthetic review"}, handle)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "output_tokens": 2}}))
''')
    executable.chmod(0o700)
    return executable


def request(tmp_path):
    (tmp_path / "source.txt").write_text("Synthetic primary-source paragraph")
    return {"id": "synthetic-1", "request_hash": "a" * 64, "phase": "screening",
            "inputs": ["source.txt"], "images": [], "instructions": "Screen the synthetic evidence.",
            "payload": {}, "response_schema": {"type": "object"}, "claim_token": "synthetic-claim"}


def test_account_environment_never_passes_api_keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-value")
    monkeypatch.setenv("CODEX_API_KEY", "synthetic-private-value")
    before = dict(os.environ)
    result = account_environment()
    assert "OPENAI_API_KEY" not in result and "CODEX_API_KEY" not in result
    assert dict(os.environ) == before


@pytest.mark.skipif(os.name == "nt", reason="The synthetic executable uses a POSIX shebang")
def test_preflight_checks_account_without_model_turn(fake_codex):
    result = check_codex(str(fake_codex))
    assert result["authentication"] == "chatgpt" and result["model_calls"] == 0
    assert not result["api_key_fallback"]


@pytest.mark.skipif(os.name == "nt", reason="The synthetic executable uses a POSIX shebang")
def test_api_key_authentication_is_rejected(fake_codex, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_AUTH", "Logged in using an API key")
    with pytest.raises(AgentUnavailable, match="ChatGPT sign-in"):
        check_codex(str(fake_codex))


@pytest.mark.skipif(os.name == "nt", reason="The synthetic executable uses a POSIX shebang")
def test_structured_candidate_echoes_job_identity_and_usage(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-value")
    result = execute_job(tmp_path, request(tmp_path), executable=str(fake_codex))
    assert result["request_hash"] == "a" * 64 and result["claim_token"] == "synthetic-claim"
    assert result["result"]["eligible"] is False
    assert result["agent"]["monetary_cost"] is None
    assert result["agent"]["usage"][0]["input_tokens"] == 5
    assert result["agent"]["blinding"] == "unblinded"


@pytest.mark.skipif(os.name == "nt", reason="The synthetic executable uses a POSIX shebang")
def test_quota_failure_does_not_submit_old_result(tmp_path, fake_codex, monkeypatch):
    current = request(tmp_path)
    execute_job(tmp_path, current, executable=str(fake_codex))
    monkeypatch.setenv("SYNTHETIC_QUOTA", "true")
    with pytest.raises(AccountLimit):
        execute_job(tmp_path, current, executable=str(fake_codex))
    assert not (tmp_path / "agent-attempts/synthetic-1/result.json").exists()


def test_input_paths_cannot_escape_workspace(tmp_path):
    current = request(tmp_path)
    current["inputs"] = ["../reference.txt"]
    with pytest.raises(ValueError, match="inside the private workspace"):
        execute_job(tmp_path, current, executable="unused")


@pytest.mark.skipif(os.name == "nt", reason="The synthetic executable uses a POSIX shebang")
def test_cancellation_stops_child_process(tmp_path, fake_codex):
    stop = threading.Event()
    stop.set()
    with pytest.raises(RuntimeError, match="stopped"):
        execute_job(tmp_path, request(tmp_path), executable=str(fake_codex), stop_event=stop)


def test_missing_cli_does_not_load_provider_sdk():
    with pytest.raises(AgentUnavailable, match="unavailable"):
        check_codex("livingmeta-nonexistent-synthetic-agent")


def test_native_output_schema_closes_objects_without_mutating_contract():
    from livingmeta.domain import ExtractionBatch
    from livingmeta.local.codex import codex_output_schema
    original = ExtractionBatch.model_json_schema()
    output = codex_output_schema(original)
    assert output['additionalProperties'] is False
    assert set(output['required']) == set(output['properties'])
    assert 'additionalProperties' not in original
    for definition in output['$defs'].values():
        if definition.get('type') == 'object':
            assert definition['additionalProperties'] is False
            assert set(definition['required']) == set(definition['properties'])


@pytest.mark.skipif(os.name == 'nt', reason='The synthetic executable uses a POSIX shebang')
def test_automatic_quota_pause_retains_pending_job(tmp_path, fake_codex, monkeypatch):
    from test_local_workspace import make_pdf
    from livingmeta.domain import Protocol
    from livingmeta.local.codex import run_jobs
    from livingmeta.local.workflow import prepare_workspace
    from livingmeta.local.workspace import load_jobs, load_manifest
    papers = tmp_path / 'papers'
    papers.mkdir()
    make_pdf(papers / 'synthetic.pdf', ['Synthetic primary paper'])
    workspace = tmp_path / 'workspace'
    prepare_workspace(papers, workspace, Protocol())
    monkeypatch.setenv('SYNTHETIC_QUOTA', 'true')
    result = run_jobs(workspace, executable=str(fake_codex), concurrency=2)
    assert result['status'] == 'paused' and result['completed_jobs'] == 0
    assert result['api_key_fallback'] is False
    assert load_manifest(workspace)['status'] == 'paused'
    assert load_jobs(workspace)[0]['status'] == 'pending'
    assert load_jobs(workspace)[0]['attempts'] == 0
