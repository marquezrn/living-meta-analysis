"""Private-project authorization, evidence provenance, and session integration tests."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from livingmeta.api import create_app
from livingmeta.auth import COOKIE, token_hash
from livingmeta.config import Settings
from livingmeta.db import (AuthSession, Document, ExperimentRecord, Invitation, MeasurementRecord,
                           Membership, Project, User, utcnow)
from livingmeta.domain import Evidence, Experiment, Measurement, Protocol
from livingmeta.storage import ArtifactStore, safe_key


@pytest.fixture
def private_api(tmp_path):
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'application.sqlite'}",
                        private_directory=tmp_path / "private", app_url="http://testserver",
                        session_secret="synthetic-test-secret" * 3, owner_github_id="owner-id",
                        owner_github_login="owner", allow_local_development_login=False)
    dispatched = []
    app = create_app(settings, dispatch=dispatched.append)
    tokens, users = {}, {}
    with app.state.sessions() as db:
        for role in ("owner", "editor", "reader", "stranger"):
            user = User(github_id=role + "-id", login=role, name="Synthetic " + role, is_owner=role == "owner")
            db.add(user)
            db.flush()
            users[role] = user.id
            tokens[role] = "synthetic-session-" + role
            db.add(AuthSession(token_hash=token_hash(tokens[role]), user_id=user.id,
                               expires_at=utcnow() + timedelta(hours=2)))
        first = Project(name="Private Project A", owner_id=users["owner"], protocol=Protocol().model_dump())
        second = Project(name="Private Project B", owner_id=users["owner"], protocol=Protocol().model_dump())
        db.add_all([first, second])
        db.flush()
        for role in ("owner", "editor", "reader"):
            db.add(Membership(project_id=first.id, user_id=users[role], role=role))
        db.add(Membership(project_id=second.id, user_id=users["stranger"], role="reader"))
        doc = Document(project_id=first.id, filename="synthetic-source.pdf", sha256="a" * 64,
                       storage_key=f"projects/{first.id}/documents/{'a' * 64}.pdf", status="uploaded")
        db.add(doc)
        db.flush()
        observation = Measurement(id="synthetic-measurement", experiment_id="synthetic-experiment", outcome="Droplet_Size_um",
            raw_value="7", value=7, unit="um", status="uncertain", n_independent=3, uncertainty_type="SD", uncertainty=1,
            measurement_method="microscopy", evidence=[Evidence(document_hash=doc.sha256, page=1, source_type="text",
                locator="Synthetic paragraph", excerpt="Droplet diameter 7 um; SD 1 um; 3 independent preparations.")])
        experiment = Experiment(id="synthetic-experiment", sample_label="Synthetic A", study_family="synthetic-family",
                                measurements=[observation])
        db.add(ExperimentRecord(id=experiment.id, project_id=first.id, document_id=doc.id,
                                experiment_key=experiment.id, payload=experiment.model_dump()))
        db.add(MeasurementRecord(id=observation.id, project_id=first.id, document_id=doc.id,
                                experiment_id=experiment.id, payload=observation.model_dump()))
        db.commit()
        ids = {"project": first.id, "other_project": second.id, "document": doc.id, "measurement": observation.id}
    app.state.store.put(doc.storage_key, b"%PDF-1.4\nSynthetic fixture; never extracted")
    app.state.store.put(f"projects/{second.id}/private.json", b'{"private":"Project B only"}', "application/json")
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, tokens=tokens, users=users, ids=ids,
                              settings=settings, dispatched=dispatched)


def authorize(env, role):
    env.client.cookies.clear()
    env.client.cookies.set(COOKIE, env.tokens[role])


def test_anonymous_users_cannot_list_or_create_private_projects(private_api):
    env = private_api
    assert env.client.get("/api/v1/session").json()["authenticated"] is False
    assert env.client.get("/api/v1/projects").status_code == 401
    assert env.client.post("/api/v1/projects", json={"name": "Unauthorized"}).status_code == 401
    assert env.client.get(f"/api/v1/projects/{env.ids['project']}").status_code == 401


@pytest.mark.parametrize("resource", ["", "/protocol", "/documents", "/runs", "/experiments", "/measurements",
                                         "/publications", "/analysis", "/benchmark", "/members", "/exports/json"])
def test_project_membership_prevents_idor_for_every_read_surface(private_api, resource):
    env = private_api
    authorize(env, "stranger")
    response = env.client.get(f"/api/v1/projects/{env.ids['project']}{resource}")
    assert response.status_code == 404
    if not resource:
        assert env.client.get(f"/api/v1/projects/{env.ids['other_project']}").status_code == 200


@pytest.mark.parametrize("role", ["owner", "editor", "reader"])
def test_members_can_read_evidence_and_source_files(private_api, role):
    env = private_api
    authorize(env, role)
    response = env.client.get(f"/api/v1/documents/{env.ids['document']}/file")
    assert response.status_code == 200 and response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert env.client.get(f"/api/v1/projects/{env.ids['project']}/measurements").status_code == 200
    authorize(env, "stranger")
    assert env.client.get(f"/api/v1/documents/{env.ids['document']}/file").status_code == 404


def test_role_permissions_and_owner_only_paid_execution(private_api):
    env, project = private_api, private_api.ids["project"]
    authorize(env, "reader")
    assert env.client.put(f"/api/v1/projects/{project}/protocol", json=Protocol().model_dump()).status_code == 403
    assert env.client.patch(f"/api/v1/measurements/{env.ids['measurement']}", json={"status": "accepted", "notes": "Synthetic review"}).status_code == 403
    assert env.client.post(f"/api/v1/projects/{project}/runs", json={"budget_usd": 5}).status_code == 403
    authorize(env, "editor")
    assert env.client.put(f"/api/v1/projects/{project}/protocol", json=Protocol().model_dump()).status_code == 200
    assert env.client.patch(f"/api/v1/measurements/{env.ids['measurement']}", json={"status": "accepted", "notes": "Checked synthetic primary source"}).status_code == 200
    assert env.client.post(f"/api/v1/projects/{project}/runs", json={"budget_usd": 5}).status_code == 403
    assert env.client.post(f"/api/v1/projects/{project}/invitations", json={"role": "reader"}).status_code == 403
    assert env.client.get(f"/api/v1/projects/{project}/costs").status_code == 403
    authorize(env, "owner")
    run = env.client.post(f"/api/v1/projects/{project}/runs", json={"budget_usd": 5})
    assert run.status_code == 202
    assert env.dispatched == [run.json()["id"]]
    assert env.client.post(f"/api/v1/projects/{project}/runs", json={"budget_usd": 5}).status_code == 409
    authorize(env, "stranger")
    assert env.client.post(f"/api/v1/runs/{run.json()['id']}/cancel").status_code == 404
    authorize(env, "reader")
    assert env.client.post(f"/api/v1/runs/{run.json()['id']}/cancel").status_code == 403
    authorize(env, "owner")
    assert env.client.post(f"/api/v1/runs/{run.json()['id']}/cancel").json()["status"] == "cancelled"


def test_selected_foreign_document_cannot_enter_a_run(private_api):
    env = private_api
    authorize(env, "owner")
    response = env.client.post(f"/api/v1/projects/{env.ids['project']}/runs",
                               json={"budget_usd": 5, "document_ids": ["foreign-document"]})
    assert response.status_code == 422
    assert not env.dispatched


def test_owner_is_the_only_role_that_can_create_projects(private_api):
    env = private_api
    authorize(env, "editor")
    assert env.client.post("/api/v1/projects", json={"name": "Editor project"}).status_code == 403
    authorize(env, "owner")
    created = env.client.post("/api/v1/projects", json={"name": "New private project"})
    assert created.status_code == 201 and created.json()["role"] == "owner"
    authorize(env, "reader")
    assert env.client.get(f"/api/v1/projects/{created.json()['id']}").status_code == 404


def test_cross_origin_and_cross_site_mutations_are_rejected(private_api):
    env = private_api
    authorize(env, "owner")
    for headers in ({"origin": "https://untrusted.example"}, {"sec-fetch-site": "cross-site"}):
        assert env.client.post("/api/v1/projects", json={"name": "Unwanted project"}, headers=headers).status_code == 403
    assert env.client.post("/api/v1/projects", json={"name": "Same origin"}, headers={"origin": "http://testserver"}).status_code == 201


def test_artifact_project_prefix_and_path_traversal_are_rejected(private_api):
    env, project = private_api, private_api.ids["project"]
    authorize(env, "reader")
    keys = [f"projects/{env.ids['other_project']}/private.json", "../outside.txt", "/etc/passwd", "projects\\private.txt", "."]
    for key in keys:
        assert env.client.get(f"/api/v1/projects/{project}/artifacts", params={"key": key}).status_code == 404
    key = f"projects/{project}/safe.json"
    env.app.state.store.put(key, b'{"synthetic":true}', "application/json")
    assert env.client.get(f"/api/v1/projects/{project}/artifacts", params={"key": key}).json() == {"synthetic": True}


def test_invitation_expiration_single_use_and_no_owner_elevation(private_api):
    env, project = private_api, private_api.ids["project"]
    authorize(env, "owner")
    assert env.client.post(f"/api/v1/projects/{project}/invitations", json={"role": "owner"}).status_code == 422
    token = env.client.post(f"/api/v1/projects/{project}/invitations", json={"role": "reader"}).json()["token"]
    env.client.cookies.clear()
    assert env.client.post(f"/api/v1/invitations/{token}/accept").status_code == 401
    authorize(env, "stranger")
    assert env.client.post(f"/api/v1/invitations/{token}/accept").status_code == 200
    assert env.client.post(f"/api/v1/invitations/{token}/accept").status_code == 404
    assert env.client.get(f"/api/v1/projects/{project}").json()["role"] == "reader"
    authorize(env, "owner")
    expired = env.client.post(f"/api/v1/projects/{project}/invitations", json={"role": "reader"}).json()["token"]
    with env.app.state.sessions() as db:
        db.get(Invitation, token_hash(expired)).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    authorize(env, "stranger")
    assert env.client.post(f"/api/v1/invitations/{expired}/accept").status_code == 404


def test_session_expiry_and_logout_revoke_access(private_api):
    env = private_api
    authorize(env, "reader")
    assert env.client.get("/api/v1/session").json()["authenticated"]
    assert env.client.post("/auth/logout").status_code == 200
    authorize(env, "reader")
    assert env.client.get("/api/v1/projects").status_code == 401
    with env.app.state.sessions() as db:
        db.get(AuthSession, token_hash(env.tokens["editor"])).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    authorize(env, "editor")
    assert env.client.get("/api/v1/projects").status_code == 401


def test_curve_samples_and_quarantined_sources_cannot_be_accepted(private_api):
    env = private_api
    authorize(env, "editor")
    with env.app.state.sessions() as db:
        record = db.get(MeasurementRecord, env.ids["measurement"])
        record.payload = {**record.payload, "origin": "curve_sample"}
        db.commit()
    endpoint = f"/api/v1/measurements/{env.ids['measurement']}"
    assert env.client.patch(endpoint, json={"status": "accepted", "notes": "Synthetic attempt"}).status_code == 422
    with env.app.state.sessions() as db:
        db.get(Document, env.ids["document"]).status = "quarantined"
        db.commit()
    assert env.client.patch(endpoint, json={"status": "accepted", "notes": "Synthetic attempt"}).status_code == 409


def test_local_artifact_store_refuses_symlink_escape(tmp_path):
    store = ArtifactStore(Settings(_env_file=None, private_directory=tmp_path / "private"))
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sensitive.txt").write_text("Private outside storage")
    (store.root / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="outside|symlink|Invalid|escape"):
        store.get("escape/sensitive.txt")
    with pytest.raises(ValueError, match="outside|symlink|Invalid|escape"):
        store.put("escape/overwrite.txt", b"Unwanted mutation")
    assert not (outside / "overwrite.txt").exists()


def test_artifact_key_cannot_normalize_to_storage_root():
    with pytest.raises(ValueError):
        safe_key(".")


def prepare_inferential_sources(env):
    """Seed explicitly compatible synthetic arms; all quantities are fixtures."""
    from livingmeta.domain import Attribute
    protocol = Protocol(analysis_mode="inferential", outcome="Droplet_Size_um", outcome_definition="Mean droplet diameter",
                        measurement_method="microscopy", comparator="matched control", time_point="0 day",
                        concentration_basis="mass", unit="um", effect_measure="MD")
    definitions = [Attribute(name="outcome_definition", text=protocol.outcome_definition, status="accepted", evidence=[Evidence(document_hash="a" * 64, page=1, source_type="text", locator="Synthetic design", excerpt="Independent arms; volume-weighted diameter; unmodified CNC.")]),
                   Attribute(name="comparator", text=protocol.comparator, status="accepted", evidence=[Evidence(document_hash="a" * 64, page=1, source_type="text", locator="Synthetic comparator", excerpt="unmodified CNC")]),
                   Attribute(name="arms_independent", text="true", status="accepted", evidence=[Evidence(document_hash="a" * 64, page=1, source_type="text", locator="Synthetic design", excerpt="Independently prepared experimental arms")])]
    with env.app.state.sessions() as db:
        db.get(Project, env.ids["project"]).protocol = protocol.model_dump()
        db.get(Document, env.ids["document"]).status = "extracted"
        record = db.get(MeasurementRecord, env.ids["measurement"])
        record.payload = {**record.payload, "status": "accepted", "statistic": "mean", "time_value": 0,
                          "time_unit": "day", "concentration_basis": "mass"}
        experiment = db.get(ExperimentRecord, "synthetic-experiment")
        experiment.payload = {**experiment.payload, "attributes": [a.model_dump() for a in definitions]}
        control = Measurement.model_validate({**record.payload, "id": "synthetic-control", "experiment_id": "synthetic-control-experiment",
                                              "raw_value": "5", "value": 5, "uncertainty": 0.5})
        control_exp = Experiment(id=control.experiment_id, sample_label="Synthetic control", study_family="synthetic-family",
                                 measurements=[control], attributes=definitions)
        db.add(ExperimentRecord(id=control_exp.id, project_id=env.ids["project"], document_id=env.ids["document"],
                                experiment_key=control_exp.id, payload=control_exp.model_dump()))
        db.add(MeasurementRecord(id=control.id, project_id=env.ids["project"], document_id=env.ids["document"],
                                 experiment_id=control_exp.id, payload=control.model_dump()))
        db.commit()
    spec = {k: getattr(protocol, k) for k in ("outcome", "outcome_definition", "measurement_method", "comparator", "time_point",
                                            "concentration_basis", "unit", "effect_measure")}
    contrast = {"id": "synthetic-contrast", "study_family": "synthetic-family", "arms_independent": True,
                "treatment_measurement_id": env.ids["measurement"], "control_measurement_id": "synthetic-control",
                "source_measurement_ids": [env.ids["measurement"], "synthetic-control"]}
    return spec, contrast


def test_fabricated_inferential_values_cannot_borrow_accepted_evidence_ids(private_api, monkeypatch):
    env = private_api
    authorize(env, "owner")
    spec, contrast = prepare_inferential_sources(env)
    called = []
    monkeypatch.setattr("livingmeta.statistics.engine.inferential", lambda *args: called.append(args) or {"status": "completed"})
    endpoint = f"/api/v1/projects/{env.ids['project']}/analysis"
    baseline = env.client.post(endpoint, json={"contrasts": [contrast], "spec": spec})
    assert baseline.status_code == 200
    assert called[0][0][0]["treatment"] == {"mean": 7, "sd": 1, "n_independent": 3, "qualifier": "exact"}
    called.clear()
    fabricated = {**contrast, "treatment": {"mean": 999, "sd": 0.01, "n_independent": 5000},
                 "control": {"mean": 1, "sd": 0.01, "n_independent": 5000}}
    response = env.client.post(endpoint, json={"contrasts": [fabricated], "spec": spec})
    assert response.status_code == 422
    assert not called


@pytest.mark.parametrize("document_status,source_status", [("stale", "active"), ("quarantined", "active"), ("extracted", "retracted")])
def test_inferential_sources_must_remain_active(private_api, monkeypatch, document_status, source_status):
    env = private_api
    authorize(env, "owner")
    spec, contrast = prepare_inferential_sources(env)
    with env.app.state.sessions() as db:
        db.get(Document, env.ids["document"]).status = document_status
        db.get(ExperimentRecord, "synthetic-experiment").source_status = source_status
        db.commit()
    called = []
    monkeypatch.setattr("livingmeta.statistics.engine.inferential", lambda *args: called.append(args) or {"status": "completed"})
    response = env.client.post(f"/api/v1/projects/{env.ids['project']}/analysis", json={"contrasts": [contrast], "spec": spec})
    assert response.status_code == 422
    assert not called


def test_nonactive_sources_cannot_enter_descriptive_synthesis(private_api):
    env = private_api
    authorize(env, "reader")
    with env.app.state.sessions() as db:
        measurement = db.get(MeasurementRecord, env.ids["measurement"])
        measurement.payload = {**measurement.payload, "status": "accepted"}
        db.get(ExperimentRecord, "synthetic-experiment").source_status = "retracted"
        db.get(Document, env.ids["document"]).status = "quarantined"
        db.commit()
    response = env.client.get(f"/api/v1/projects/{env.ids['project']}/analysis")
    assert response.status_code == 200
    assert response.json()["counts"]["observations"] == 0
