"""Metadata scheduling, source-status preservation, and zero-inference integration tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from livingmeta.auth import aware
from livingmeta.config import Settings
from livingmeta.db import (Document, ExperimentRecord, MeasurementRecord, Project, PublicationRecord,
                           Reservation, User, initialize_database, make_database)
from livingmeta.discovery.service import DiscoveryResult
from livingmeta.domain import Citation, Protocol
from livingmeta.monitor import citation_key, monitor_project, next_weekly_check, run_due_monitoring

NOW = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    settings = Settings(database_url="sqlite:///:memory:", private_directory=tmp_path, task_mode="inline",
                        ncbi_api_key="synthetic-ncbi-key")
    engine, sessions = make_database(settings)
    initialize_database(engine)
    monkeypatch.setattr("livingmeta.monitor.utcnow", lambda: NOW)
    with sessions() as db:
        user = User(github_id="synthetic-user", login="synthetic", name="Synthetic Researcher")
        db.add(user)
        db.flush()
        project = Project(name="Synthetic research", owner_id=user.id, protocol=Protocol().model_dump(),
                          next_metadata_check=NOW - timedelta(minutes=1))
        db.add(project)
        db.commit()
        project_id = project.id
    return settings, sessions, project_id


@pytest.fixture
def fake_discovery(monkeypatch):
    calls = []

    async def discover(protocol, since, credentials, checkpoint=None):
        calls.append({"since": since, "credentials": credentials, "checkpoint": checkpoint})
        return DiscoveryResult(source_runs=[{"source": "crossref", "status": "completed", "complete": True}],
                               complete=True)

    async def check(dois, credentials, checkpoint=None):
        return DiscoveryResult(complete=True)
    monkeypatch.setattr("livingmeta.discovery.service.discover", discover)
    monkeypatch.setattr("livingmeta.discovery.service.check_publication_updates", check)
    return calls


def test_madrid_dst_and_exact_due_time():
    march = next_weekly_check(datetime(2026, 3, 29, 7, tzinfo=timezone.utc))
    october = next_weekly_check(datetime(2026, 10, 25, 7, tzinfo=timezone.utc))
    assert march == datetime(2026, 3, 30, 6, tzinfo=timezone.utc)
    assert october == datetime(2026, 10, 26, 7, tzinfo=timezone.utc)
    assert next_weekly_check(october) == datetime(2026, 11, 2, 7, tzinfo=timezone.utc)


def test_identifier_canonicalization_preserves_arxiv_hostname():
    citation = Citation(source="crossref", source_id="source", doi="https://doi.org/10.1234/ABC", title="Title")
    assert citation_key(citation) == "doi:10.1234/abc"
    arxiv = Citation(source="arxiv", source_id="https://arxiv.org/abs/2601.12345v2", title="Title")
    assert citation_key(arxiv) == "arxiv:arxiv.org/abs/2601.12345"


def test_due_schedule_is_idempotent_and_never_calls_openai(environment, fake_discovery, monkeypatch):
    from agents import Runner

    async def prohibited(*args, **kwargs):
        pytest.fail("Metadata monitoring must never call OpenAI")
    monkeypatch.setattr(Runner, "run", prohibited)
    settings, sessions, project_id = environment
    first = run_due_monitoring(sessions, settings)
    second = run_due_monitoring(sessions, settings)
    assert len(first) == 1 and second == []
    assert first[0]["openai_calls"] == 0 and first[0]["complete"]
    assert fake_discovery[0]["credentials"]["pubmed_api_key"] == "synthetic-ncbi-key"
    with sessions() as db:
        project = db.get(Project, project_id)
        assert aware(project.last_metadata_check) == NOW
        assert aware(project.next_metadata_check) == datetime(2026, 10, 12, 6, tzinfo=timezone.utc)
        assert db.scalars(select(Reservation)).all() == []


@pytest.mark.asyncio
async def test_active_lease_prevents_duplicate_monitor(environment, fake_discovery):
    settings, sessions, project_id = environment
    with sessions() as db:
        project = db.get(Project, project_id)
        project.discovery_checkpoint = {"monitor_lease": {"token": "synthetic-lease",
                                                          "expires_at": (NOW + timedelta(minutes=5)).isoformat()}}
        db.commit()
    result = await monitor_project(project_id, sessions, settings)
    assert result["status"] == "already_running" and fake_discovery == []


@pytest.mark.asyncio
async def test_partial_run_does_not_advance_freshness(environment, fake_discovery, monkeypatch):
    settings, sessions, project_id = environment
    previous = NOW - timedelta(days=7)
    with sessions() as db:
        project = db.get(Project, project_id)
        project.last_metadata_check = previous
        db.commit()

    async def partial(*args, **kwargs):
        return DiscoveryResult(source_runs=[{"source": "crossref", "complete": False, "status": "partial"}],
                               errors=["Page budget reached"], checkpoint={"sources": {"crossref": {"cursor": "next"}}})
    monkeypatch.setattr("livingmeta.discovery.service.discover", partial)
    result = await monitor_project(project_id, sessions, settings)
    assert not result["complete"]
    with sessions() as db:
        project = db.get(Project, project_id)
        assert aware(project.last_metadata_check) == previous
        assert aware(project.next_metadata_check) == NOW + timedelta(hours=1)
        assert project.discovery_checkpoint["sources"]["crossref"]["cursor"] == "next"
        assert "monitor_lease" not in project.discovery_checkpoint
        assert project.synthesis_stale


@pytest.mark.asyncio
async def test_known_doi_retraction_without_topic_result_invalidates_evidence(environment, fake_discovery, monkeypatch):
    settings, sessions, project_id = environment
    with sessions() as db:
        publication = PublicationRecord(project_id=project_id, canonical_id="doi:10.1234/test", doi="10.1234/test",
                                        state="active", sources=["crossref"], payload={"status": "active"})
        document = Document(project_id=project_id, filename="synthetic.pdf", sha256="hash", storage_key="test/key",
                            doi="https://doi.org/10.1234/TEST", status="extracted")
        db.add_all([publication, document])
        db.flush()
        experiment = ExperimentRecord(project_id=project_id, document_id=document.id,
                                      experiment_key="experiment", payload={}, source_status="active")
        db.add(experiment)
        db.flush()
        measurement = MeasurementRecord(id="measurement", project_id=project_id, document_id=document.id,
                                        experiment_id=experiment.id, payload={"status": "accepted"})
        db.add(measurement)
        db.commit()
        document_id, experiment_id = document.id, experiment.id
    checked = []

    async def known(dois, credentials, checkpoint=None):
        checked.extend(dois)
        return DiscoveryResult(complete=True, source_runs=[{"source": "crossref_known_updates", "complete": True,
                                                            "status_updates": {"10.1234/test": "retracted"}}])
    monkeypatch.setattr("livingmeta.discovery.service.check_publication_updates", known)
    result = await monitor_project(project_id, sessions, settings)
    assert checked == ["10.1234/test"]
    assert result["added"] == 0 and result["changed"] == 1
    with sessions() as db:
        assert db.get(Document, document_id).status == "quarantined"
        assert db.get(ExperimentRecord, experiment_id).source_status == "retracted"
        assert db.get(MeasurementRecord, "measurement").payload["status"] == "stale"
        publication = db.scalar(select(PublicationRecord))
        assert publication.payload["status"] == "retracted"
        assert publication.state == "quarantined"


@pytest.mark.asyncio
async def test_prior_retraction_is_not_downgraded_by_active_provider(environment, fake_discovery, monkeypatch):
    settings, sessions, project_id = environment
    with sessions() as db:
        db.add(PublicationRecord(project_id=project_id, canonical_id="doi:10.1234/test", doi="10.1234/test",
                                 state="quarantined", sources=["pubmed"], payload={"status": "retracted"}))
        db.commit()

    async def active(*args, **kwargs):
        return DiscoveryResult(complete=True, source_runs=[{"source": "openalex", "complete": True}],
                               citations=[Citation(source="openalex", source_id="W1", doi="10.1234/test",
                                                   title="Original article", status="active")])
    monkeypatch.setattr("livingmeta.discovery.service.discover", active)
    await monitor_project(project_id, sessions, settings)
    with sessions() as db:
        record = db.scalar(select(PublicationRecord))
        assert record.payload["status"] == "retracted"
        assert record.state == "quarantined"
        assert record.sources == ["openalex", "pubmed"]


@pytest.mark.asyncio
async def test_hosted_workers_receive_provider_wide_redis_pacing(environment, fake_discovery):
    settings, sessions, project_id = environment
    settings.task_mode = "celery"
    settings.broker_url = "redis://synthetic-redis:6379/0"
    await monitor_project(project_id, sessions, settings)
    assert fake_discovery[0]["credentials"]["redis_url"] == settings.broker_url


@pytest.mark.asyncio
async def test_unverified_scopus_is_visible_in_incomplete_status(environment, fake_discovery):
    settings, sessions, project_id = environment
    with sessions() as db:
        project = db.get(Project, project_id)
        protocol = Protocol(enabled_sources=["scopus", "crossref"])
        project.protocol = protocol.model_dump()
        db.commit()
    result = await monitor_project(project_id, sessions, settings)
    assert not result["complete"]
    assert any(run["source"] == "scopus" and not run["complete"] for run in result["source_runs"])
    with sessions() as db:
        assert db.get(Project, project_id).last_metadata_check is None


@pytest.mark.asyncio
async def test_topic_correction_notices_are_not_pending_experiments(environment, fake_discovery, monkeypatch):
    settings, sessions, project_id = environment

    async def notices(*args, **kwargs):
        return DiscoveryResult(complete=True, citations=[Citation(source="crossref", source_id="notice",
                                                                  doi="10.1234/notice", title="Correction notice")],
                               source_runs=[{"source": "crossref", "complete": True,
                                             "notice_dois": ["10.1234/notice"],
                                             "status_updates": {"10.1234/original": "corrected"}}])
    monkeypatch.setattr("livingmeta.discovery.service.discover", notices)
    result = await monitor_project(project_id, sessions, settings)
    assert result["added"] == 0
    with sessions() as db:
        assert db.scalars(select(PublicationRecord)).all() == []
