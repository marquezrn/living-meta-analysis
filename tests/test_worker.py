"""Offline execution and recovery against synthetic evidence and fake model usage."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from livingmeta.agents.provider import AgentsCaller
from livingmeta.budget import token_cost
from livingmeta.config import Settings
from livingmeta.db import (Document, ExperimentRecord, MeasurementRecord, Project, Reservation,
                           Run, User, Wallet, initialize_database, make_database)
from livingmeta.domain import Evidence, Experiment, ExtractionBatch, Measurement, Protocol
from livingmeta.extraction.verification import VerificationReview
from livingmeta.storage import ArtifactStore
from livingmeta.worker import persist_batch, process_run
from test_extraction import make_pdf


SOURCE_TEXT = "Droplet size 7 um; SD 1 um; 3 independent preparations."


class SimulatedWorkerLoss(BaseException):
    """A process loss bypasses the worker's normal exception handling."""


def fake_caller_factory(calls, *, interrupt_verification=False):
    """Strictly follow the real adapter signature while never contacting OpenAI."""
    pending_loss = interrupt_verification

    class OfflineCaller:
        def __init__(self, *, before_call, after_call, **kwargs):
            self.before_call = before_call
            self.after_call = after_call

        async def __call__(self, agent_name, instructions, input_text, output_type, images=None, model=None):
            nonlocal pending_loss
            reservation = self.before_call(model, 100, 100)
            calls.append(agent_name)
            if agent_name == "verification" and pending_loss:
                pending_loss = False
                # A sent request has unknown usage when the worker disappears.
                raise SimulatedWorkerLoss()
            self.after_call(reservation, 100, 30)
            if agent_name == "coordinator":
                return ExtractionBatch(eligible=True, eligibility_reason="Synthetic experimental study",
                                       publication_type="experimental")
            if agent_name == "text_and_tables":
                context = json.loads(input_text)
                measurement = Measurement(experiment_id="untrusted-model-id", outcome="Droplet_Size_um",
                    raw_value="7", value=7, unit="um", uncertainty_type="SD", uncertainty=1, n_independent=3,
                    evidence=[Evidence(document_hash=context["document_hash"], page=1, source_type="text",
                                       locator="Synthetic paragraph", excerpt=SOURCE_TEXT)])
                return ExtractionBatch(eligible=True, eligibility_reason="Synthetic source", experiments=[
                    Experiment(id="untrusted-model-id", sample_label="Synthetic A", study_family="model-family",
                               measurements=[measurement])])
            assert agent_name == "verification"
            return VerificationReview()

    return OfflineCaller


@pytest.fixture
def worker_database(tmp_path, monkeypatch):
    monkeypatch.setattr("livingmeta.extraction.layout.ocr_image", lambda path: {
        "status": "unavailable", "text": "", "words": []})
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'worker.sqlite'}",
                        private_directory=tmp_path / "private", openai_api_key="offline-fixture")
    engine, sessions = make_database(settings)
    initialize_database(engine)
    source = tmp_path / "synthetic-study.pdf"
    make_pdf(source, [SOURCE_TEXT])
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    protocol = Protocol()
    with sessions() as db:
        owner = User(github_id="synthetic-worker", login="synthetic", name="Synthetic owner", is_owner=True)
        db.add(owner)
        db.flush()
        project = Project(name="Synthetic extraction", owner_id=owner.id, protocol=protocol.model_dump())
        db.add(project)
        db.flush()
        key = f"projects/{project.id}/documents/{digest}.pdf"
        document = Document(project_id=project.id, filename=source.name, sha256=digest,
                            storage_key=key, status="uploaded", inventory={"acquisition_version": "publishedVersion"})
        db.add(document)
        db.flush()
        run = Run(project_id=project.id, document_ids=[document.id], budget_microusd=1_000_000,
                  protocol_snapshot=protocol.model_dump())
        db.add(run)
        db.commit()
        ids = {"run": run.id, "project": project.id, "document": document.id}
    store = ArtifactStore(settings)
    store.put(key, source.read_bytes(), "application/pdf")
    yield SimpleNamespace(settings=settings, sessions=sessions, ids=ids, store=store, digest=digest)
    engine.dispose()


def test_worker_persists_source_evidence_artifacts_and_cost_without_model_calls(worker_database):
    env, calls = worker_database, []
    process_run(env.ids["run"], env.sessions, env.settings, caller_factory=fake_caller_factory(calls))
    assert calls == ["coordinator", "text_and_tables", "verification"]
    with env.sessions() as db:
        run = db.get(Run, env.ids["run"])
        document = db.get(Document, env.ids["document"])
        project = db.get(Project, env.ids["project"])
        experiments = list(db.scalars(select(ExperimentRecord)))
        measurements = list(db.scalars(select(MeasurementRecord)))
        wallet = db.get(Wallet, "initial-evaluation")
        assert run.status == "completed" and document.status == "extracted"
        assert len(experiments) == len(measurements) == 1
        observation = measurements[0].payload
        assert observation["status"] == "accepted" and observation["normalized_value"] == 7
        assert observation["n_independent"] == 3 and observation["uncertainty"] == 1
        assert observation["evidence"][0]["document_hash"] == env.digest
        assert observation["evidence"][0]["page"] == 1
        assert experiments[0].id != "untrusted-model-id"
        assert document.inventory["acquisition_version"] == "publishedVersion"
        assert document.checkpoint["complete"]
        assert project.last_extraction and project.last_synthesis_update
        assert project.synthesis["descriptive"] and not project.synthesis_stale
        assert run.spent_microusd == wallet.spent_microusd == 3 * token_cost("gpt-6.1-sol", 100, 30)
        assert run.reserved_microusd == wallet.reserved_microusd == 0
        extraction_key = run.result["documents"][0]["extraction_key"]
        extracted = json.loads(env.store.get(extraction_key))
        assert extracted["experiments"][0]["measurements"][0]["status"] == "accepted"
        assert env.store.get(document.storage_key).startswith(b"%PDF")
    # Duplicate task delivery must neither repeat agents nor create extra charges.
    process_run(env.ids["run"], env.sessions, env.settings, caller_factory=fake_caller_factory(calls))
    assert len(calls) == 3


def test_missing_credentials_fail_before_any_paid_call(worker_database, monkeypatch):
    env, calls = worker_database, []
    env.settings.openai_api_key = ""
    async def forbidden_call(self, *args, **kwargs):
        calls.append(True)
        raise AssertionError("A provider must never be called without configured credentials")
    monkeypatch.setattr(AgentsCaller, "__call__", forbidden_call)
    process_run(env.ids["run"], env.sessions, env.settings)
    assert not calls
    with env.sessions() as db:
        run = db.get(Run, env.ids["run"])
        assert run.status == "failed" and "no paid call was made" in run.error
        assert run.spent_microusd == run.reserved_microusd == 0
        assert not list(db.scalars(select(Reservation)))
        assert db.get(Wallet, "initial-evaluation") is None
        assert db.get(Document, env.ids["document"]).status == "uploaded"


def test_reassessed_source_cannot_retain_old_accepted_results_in_synthesis(worker_database):
    from livingmeta.api import current_experiments
    from livingmeta.statistics.engine import descriptive
    env = worker_database
    process_run(env.ids["run"], env.sessions, env.settings, caller_factory=fake_caller_factory([]))
    with env.sessions() as db:
        document = db.get(Document, env.ids["document"])
        reassessment = ExtractionBatch(eligible=False, eligibility_reason="Synthetic source excluded on reassessment")
        persist_batch(db, env.ids["project"], document, reassessment, {})
        document.status = "excluded"
        db.commit()
        current = [Experiment.model_validate(e) for e in current_experiments(db, env.ids["project"])]
        assert current[0].measurements[0].status == "stale"
        assert descriptive(current, Protocol())["groups"] == []


def test_worker_loss_charges_unknown_usage_and_resumes_without_repeating_finished_agents(worker_database):
    env, calls = worker_database, []
    factory = fake_caller_factory(calls, interrupt_verification=True)
    with pytest.raises(SimulatedWorkerLoss):
        process_run(env.ids["run"], env.sessions, env.settings, caller_factory=factory)
    with env.sessions() as db:
        assert db.get(Run, env.ids["run"]).status == "running"
        document = db.get(Document, env.ids["document"])
        assert "text" in document.checkpoint["pages"]["1"]
        assert "verified" not in document.checkpoint["pages"]["1"]
        assert len(list(db.scalars(select(Reservation).where(Reservation.status == "held")))) == 1
    process_run(env.ids["run"], env.sessions, env.settings, recovery=True, caller_factory=factory)
    assert calls == ["coordinator", "text_and_tables", "verification", "verification"]
    with env.sessions() as db:
        run = db.get(Run, env.ids["run"])
        reservations = list(db.scalars(select(Reservation)))
        unknown = [reservation for reservation in reservations if not reservation.usage["usage_known"]]
        assert run.status == "completed" and len(reservations) == 4
        assert len(unknown) == 1 and unknown[0].usage["interrupted_worker"] is True
        assert unknown[0].actual_microusd == unknown[0].reserved_microusd
        expected = 3 * token_cost("gpt-6.1-sol", 100, 30) + token_cost("gpt-6.1-sol", 100, 100)
        assert run.spent_microusd == expected
        assert run.reserved_microusd == db.get(Wallet, "initial-evaluation").reserved_microusd == 0
        assert len(list(db.scalars(select(MeasurementRecord)))) == 1
