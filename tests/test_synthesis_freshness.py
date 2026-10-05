"""A superseded or invalidated pooled analysis must never appear current."""

import pytest
from sqlalchemy import select

from livingmeta.db import AuditEvent, Document, ExperimentRecord, Project
from test_api_security import authorize, prepare_inferential_sources, private_api as private_api


def complete_synthetic_fit(env, monkeypatch):
    authorize(env, "owner")
    spec, contrast = prepare_inferential_sources(env)
    result = {"status": "completed", "estimate": 2.345, "confidence_interval": [1, 3]}
    monkeypatch.setattr("livingmeta.statistics.engine.inferential", lambda *args: result)
    endpoint = f"/api/v1/projects/{env.ids['project']}/analysis"
    response = env.client.post(endpoint, json={"spec": spec, "contrasts": [contrast]})
    assert response.status_code == 200
    return endpoint, spec, result


def test_completed_fit_is_current_only_with_matching_protocol_and_resolved_evidence(private_api, monkeypatch):
    env = private_api
    endpoint, _, result = complete_synthetic_fit(env, monkeypatch)
    current = env.client.get(endpoint).json()
    assert current["inferential_result"] == result
    assert current["historical_inferential_result"] is None
    assert current["inferential_stale"] is False
    assert current["synthesis_stale"] is False


@pytest.mark.parametrize("change", ["evidence", "protocol", "stale_flag", "missing_protocol_snapshot"])
def test_invalidated_fit_is_returned_as_historical_only(private_api, monkeypatch, change):
    env = private_api
    endpoint, _, result = complete_synthetic_fit(env, monkeypatch)
    with env.app.state.sessions() as db:
        project = db.get(Project, env.ids["project"])
        if change == "evidence":
            db.get(Document, env.ids["document"]).status = "uploaded"
        elif change == "protocol":
            project.protocol = {**project.protocol, "outcome_definition": "Different registered diameter"}
        elif change == "stale_flag":
            project.synthesis_stale = True
        else:
            project.synthesis = {"inferential": result}
        db.commit()
    current = env.client.get(endpoint).json()
    assert current["inferential_result"] is None
    assert current["historical_inferential_result"] == result
    assert current["inferential_stale"] is True


@pytest.mark.parametrize("has_eligible_data", [True, False])
def test_descriptive_update_removes_old_inference_and_preserves_private_audit(private_api, monkeypatch, has_eligible_data):
    env = private_api
    endpoint, spec, result = complete_synthetic_fit(env, monkeypatch)
    if not has_eligible_data:
        with env.app.state.sessions() as db:
            for experiment in db.scalars(select(ExperimentRecord).where(ExperimentRecord.project_id == env.ids["project"])):
                experiment.source_status = "retracted"
            db.commit()
    response = env.client.post(endpoint, json={"spec": spec, "contrasts": []})
    assert response.status_code == 200
    current = env.client.get(endpoint).json()
    assert current["inferential_result"] is None
    assert current["historical_inferential_result"] is None
    if not has_eligible_data:
        assert not current["groups"]
    with env.app.state.sessions() as db:
        project = db.get(Project, env.ids["project"])
        assert "inferential" not in project.synthesis
        assert "inferential_protocol" not in project.synthesis
        superseded = db.scalars(select(AuditEvent).where(AuditEvent.project_id == project.id,
                                                      AuditEvent.action == "synthesis_superseded")).one()
        assert superseded.details["result"] == result
        assert superseded.details["reason"] == "descriptive_update"
        assert superseded.details["protocol"]["outcome_definition"] == spec["outcome_definition"]
        attempts = list(db.scalars(select(AuditEvent).where(AuditEvent.project_id == project.id,
                                                          AuditEvent.action == "synthesis_updated")))
        assert any(event.details["mode"] == "descriptive" and "result" in event.details for event in attempts)
