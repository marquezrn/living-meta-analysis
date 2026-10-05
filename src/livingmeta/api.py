"""Versioned private-project API and GitHub OAuth entry points."""

import hashlib
import hmac
import json
import mimetypes
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from itsdangerous import BadSignature, URLSafeTimedSerializer
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from .auth import COOKIE, audit, aware, issue_session, require_project, require_user, session_user, token_hash
from .budget import microusd
from .config import Settings, get_settings
from .contracts import DocumentView, MeasurementView, ProjectView, PublicationView, RunView
from .db import (AuthSession, Document, ExperimentRecord, Invitation, MeasurementRecord, Membership,
                 Project, PublicationRecord, Reservation, Run, User, Wallet, initialize_database,
                 make_database, utcnow)
from .domain import Condition, Experiment, Protocol, StudyFamily
from .exports import render_export
from .monitor import next_weekly_check
from .storage import ArtifactStore, safe_key


class CreateProject(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    protocol: Protocol = Field(default_factory=Protocol)


class StartRun(BaseModel):
    budget_usd: float = Field(default=100, gt=0, le=100)
    document_ids: list[str] = Field(default_factory=list)


class ReviewMeasurement(BaseModel):
    status: Literal["accepted", "uncertain", "rejected"]
    notes: str = Field(min_length=1, max_length=4000)


class Invite(BaseModel):
    role: Literal["editor", "reader"]


class AnalysisInput(BaseModel):
    contrasts: list[dict]
    spec: dict


class AcquireInput(BaseModel):
    version: str | None = None


ACTIVE = ("queued", "running")


def pending_count(db, project_id):
    return db.scalar(select(func.count()).select_from(PublicationRecord).where(
        PublicationRecord.project_id == project_id, PublicationRecord.state == "pending_extraction")) or 0


def project_payload(db, project, role=None):
    pending = pending_count(db, project.id)
    return {"id": project.id, "name": project.name, "protocol": project.protocol, "role": role,
            "created_at": project.created_at, "last_metadata_check": project.last_metadata_check,
            "last_extraction": project.last_extraction, "last_synthesis_update": project.last_synthesis_update,
            "next_metadata_check": project.next_metadata_check, "pending_count": pending,
            "synthesis_stale": project.synthesis_stale or pending > 0,
            "discovery_report": project.discovery_report}


def run_payload(run):
    return {"id": run.id, "project_id": run.project_id, "status": run.status,
            "budget_usd": run.budget_microusd / 1e6, "spent_usd": run.spent_microusd / 1e6,
            "reserved_usd": run.reserved_microusd / 1e6, "progress": run.progress, "result": run.result,
            "error": run.error, "created_at": run.created_at, "finished_at": run.finished_at,
            "cancel_requested": run.cancel_requested}


def document_payload(document):
    return {k: getattr(document, k) for k in ("id", "filename", "sha256", "doi", "status", "pages", "error", "created_at")}


def current_experiments(db, project_id):
    records = db.scalars(select(ExperimentRecord).where(ExperimentRecord.project_id == project_id)).all()
    output = []
    for record in records:
        payload = {**record.payload, "source_status": record.source_status, "document_id": record.document_id}
        payload["measurements"] = [m.payload for m in db.scalars(select(MeasurementRecord).where(
            MeasurementRecord.experiment_id == record.id))]
        document = db.get(Document, record.document_id)
        if record.source_status != "active" or document.status in ("quarantined", "stale"):
            payload["attributes"] = [{**a, "status": "uncertain", "validation_notes": a.get("validation_notes", []) +
                [f"Source status {record.source_status}; condition requires source reassessment"]} for a in payload.get("attributes", [])]
            payload["measurements"] = [{**m, "status": "stale", "validation_notes": m.get("validation_notes", []) +
                [f"Source status {record.source_status}; excluded until source reassessment"]} for m in payload["measurements"]]
        output.append(payload)
    return output


def create_app(settings: Settings | None = None, *, dispatch=None):
    settings = settings or get_settings()
    engine, sessions = make_database(settings)
    store = ArtifactStore(settings)
    initialize_database(engine)
    secret = settings.session_secret or secrets.token_urlsafe(48)
    signer = URLSafeTimedSerializer(secret, salt="github-oauth-state-v1")

    @asynccontextmanager
    async def lifespan(app):
        yield
        engine.dispose()

    app = FastAPI(title="Living Meta-Analysis", version="0.1.0", lifespan=lifespan,
                  description="Private, evidence-preserving research projects. All project endpoints require membership.")
    app.state.settings, app.state.sessions, app.state.store = settings, sessions, store

    @app.middleware("http")
    async def protect_mutations(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != settings.app_url.rstrip("/"):
                return Response("Cross-origin mutation denied", status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return Response("Cross-site mutation denied", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api/v1/documents/") and request.url.path.endswith("/file"):
            response.headers["X-Frame-Options"] = "SAMEORIGIN"
        if request.url.path.startswith(("/api/", "/auth/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    def enqueue(run_id, background, recovery=False):
        if dispatch:
            dispatch(run_id)
        elif settings.task_mode == "celery":
            from .tasks import execute_run
            execute_run.delay(run_id, recovery)
        else:
            from .worker import process_run
            background.add_task(process_run, run_id, sessions, settings, recovery=recovery)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/api/v1/session")
    def session(request: Request):
        with sessions() as db:
            user = session_user(request, db)
            return {"authenticated": bool(user), "user": {"id": user.id, "login": user.login,
                    "name": user.name, "is_owner": user.is_owner} if user else None,
                    "development_mode": not settings.production and settings.allow_local_development_login}

    @app.post("/api/v1/development/session")
    def development_session(request: Request, response: Response):
        if settings.production or not settings.allow_local_development_login:
            raise HTTPException(404, "Not found")
        if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "Development login is restricted to the local computer")
        with sessions() as db:
            user = db.scalar(select(User).where(User.github_id == settings.owner_github_id))
            if user is None:
                user = User(github_id=settings.owner_github_id, login=settings.owner_github_login,
                            name="Local development owner", is_owner=True)
                db.add(user)
                db.commit()
            issue_session(response, user, db, settings)
            return {"authenticated": True, "development_mode": True}

    @app.get("/auth/github/login")
    def oauth_login(return_to: str = "/"):
        if not settings.github_client_id or not settings.github_client_secret:
            raise HTTPException(503, "GitHub OAuth credentials have not been configured")
        if not return_to.startswith("/") or return_to.startswith("//") or "\\" in return_to:
            return_to = "/"
        state = secrets.token_urlsafe(32)
        response = RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode({
            "client_id": settings.github_client_id, "redirect_uri": settings.app_url + "/auth/github/callback",
            "scope": "read:user", "state": state}))
        response.set_cookie("livingmeta_oauth", signer.dumps({"state": state, "return_to": return_to}),
                            httponly=True, secure=settings.production, samesite="lax", max_age=600)
        return response

    @app.get("/auth/github/callback")
    async def oauth_callback(request: Request, state: str = "", code: str = ""):
        try:
            saved = signer.loads(request.cookies.get("livingmeta_oauth", ""), max_age=600)
            if not code or not hmac.compare_digest(saved["state"], state):
                raise BadSignature("State mismatch")
        except BadSignature:
            raise HTTPException(400, "Invalid or expired GitHub sign-in state") from None
        async with httpx.AsyncClient(timeout=30) as client:
            token_response = await client.post("https://github.com/login/oauth/access_token", json={
                "client_id": settings.github_client_id, "client_secret": settings.github_client_secret,
                "code": code, "redirect_uri": settings.app_url + "/auth/github/callback"},
                headers={"Accept": "application/json"})
            if token_response.status_code != 200 or not token_response.json().get("access_token"):
                raise HTTPException(502, "GitHub sign-in failed")
            profile_response = await client.get("https://api.github.com/user", headers={
                "Authorization": "Bearer " + token_response.json()["access_token"], "Accept": "application/vnd.github+json"})
            if profile_response.status_code != 200:
                raise HTTPException(502, "GitHub profile could not be verified")
            profile = profile_response.json()
        response = RedirectResponse(saved["return_to"])
        response.delete_cookie("livingmeta_oauth")
        with sessions() as db:
            github_id = str(profile["id"])
            user = db.scalar(select(User).where(User.github_id == github_id))
            if user is None:
                user = User(github_id=github_id, login=profile["login"], name=profile.get("name") or profile["login"],
                            is_owner=github_id == settings.owner_github_id)
                db.add(user)
                db.commit()
            else:
                user.login, user.name = profile["login"], profile.get("name") or profile["login"]
                user.is_owner = github_id == settings.owner_github_id
            issue_session(response, user, db, settings)
        return response

    @app.post("/auth/logout")
    def logout(request: Request, response: Response):
        with sessions() as db:
            token = request.cookies.get(COOKIE)
            if token:
                db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(token)))
                db.commit()
        response.delete_cookie(COOKIE)
        return {"authenticated": False}

    @app.get("/api/v1/projects", response_model=list[ProjectView])
    def projects(request: Request):
        with sessions() as db:
            user = require_user(request, db)
            memberships = db.scalars(select(Membership).where(Membership.user_id == user.id)).all()
            return [project_payload(db, db.get(Project, m.project_id), m.role) for m in memberships]

    @app.post("/api/v1/projects", status_code=201, response_model=ProjectView)
    def create_project(body: CreateProject, request: Request):
        with sessions() as db:
            user = require_user(request, db)
            if not user.is_owner:
                raise HTTPException(403, "Only the application owner can create projects")
            project = Project(name=body.name.strip(), owner_id=user.id, protocol=body.protocol.model_dump(),
                next_metadata_check=next_weekly_check(utcnow(), settings.monitor_timezone, settings.monitor_weekday, settings.monitor_hour))
            db.add(project)
            db.flush()
            db.add(Membership(project_id=project.id, user_id=user.id, role="owner"))
            audit(db, "project_created", project.id, user.id)
            db.commit()
            return project_payload(db, project, "owner")

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectView)
    def project(project_id: str, request: Request):
        with sessions() as db:
            _, project, membership = require_project(request, db, project_id)
            return project_payload(db, project, membership.role)

    @app.get("/api/v1/projects/{project_id}/protocol", response_model=Protocol)
    def get_protocol(project_id: str, request: Request):
        with sessions() as db:
            _, project, _ = require_project(request, db, project_id)
            return project.protocol

    @app.put("/api/v1/projects/{project_id}/protocol", response_model=Protocol)
    def put_protocol(project_id: str, body: Protocol, request: Request):
        with sessions() as db:
            user, project, _ = require_project(request, db, project_id, ("owner", "editor"))
            if db.scalar(select(Run).where(Run.project_id == project_id, Run.status.in_(ACTIVE))):
                raise HTTPException(409, "Wait for the active extraction before changing the protocol")
            project.protocol, project.synthesis_stale = body.model_dump(), True
            project.discovery_checkpoint = {}
            audit(db, "protocol_updated", project_id, user.id, protocol=project.protocol)
            db.commit()
            return project.protocol

    @app.get("/api/v1/projects/{project_id}/documents", response_model=list[DocumentView])
    def documents(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return [document_payload(d) for d in db.scalars(select(Document).where(Document.project_id == project_id))]

    @app.post("/api/v1/projects/{project_id}/documents", status_code=201, response_model=list[DocumentView])
    async def upload(project_id: str, request: Request, files: list[UploadFile] = File(...)):
        if len(files) > 30:
            raise HTTPException(422, "Upload at most 30 PDFs per request")
        with sessions() as db:
            user, project, _ = require_project(request, db, project_id, ("owner", "editor"))
            output = []
            for file in files:
                content = await file.read(settings.max_upload_bytes + 1)
                if len(content) > settings.max_upload_bytes:
                    raise HTTPException(413, "A document exceeds the configured upload size")
                if not content.startswith(b"%PDF-"):
                    raise HTTPException(422, "Only original PDF documents can enter extraction")
                digest = hashlib.sha256(content).hexdigest()
                document = db.scalar(select(Document).where(Document.project_id == project_id, Document.sha256 == digest))
                if document is None:
                    key = f"projects/{project_id}/documents/{digest}.pdf"
                    store.put(key, content, "application/pdf")
                    document = Document(project_id=project_id, filename=Path(file.filename or "source.pdf").name[:255],
                                        sha256=digest, storage_key=key)
                    db.add(document)
                    db.flush()
                    project.synthesis_stale = True
                    audit(db, "document_uploaded", project_id, user.id, document_id=document.id, sha256=digest)
                output.append(document_payload(document))
            db.commit()
            return output

    @app.get("/api/v1/documents/{document_id}/file")
    def document_file(document_id: str, request: Request):
        with sessions() as db:
            document = db.get(Document, document_id)
            if document is None:
                raise HTTPException(404, "Document not found")
            require_project(request, db, document.project_id)
            return Response(store.get(document.storage_key), media_type="application/pdf",
                            headers={"Content-Disposition": "inline"})

    @app.get("/api/v1/projects/{project_id}/artifacts")
    def artifact(project_id: str, key: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
        try:
            key = safe_key(key)
            if not key.startswith(f"projects/{project_id}/"):
                raise ValueError("Project key required")
            content = store.get(key)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "Artifact not found") from None
        media_type = mimetypes.guess_type(key)[0] or "application/octet-stream"
        if media_type not in ("image/png", "image/jpeg", "application/pdf", "application/json"):
            media_type = "application/octet-stream"
        return Response(content, media_type=media_type)

    @app.get("/api/v1/projects/{project_id}/runs", response_model=list[RunView])
    def runs(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return [run_payload(r) for r in db.scalars(select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc()))]

    @app.post("/api/v1/projects/{project_id}/runs", status_code=202, response_model=RunView)
    def start_run(project_id: str, body: StartRun, request: Request, background: BackgroundTasks):
        with sessions() as db:
            user, project, _ = require_project(request, db, project_id, ("owner",))
            project = db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            if db.scalar(select(Run).where(Run.project_id == project_id, Run.status.in_(ACTIVE))):
                raise HTTPException(409, "An extraction is already active for this project")
            if body.budget_usd > min(settings.max_run_openai_usd, settings.max_total_openai_usd):
                raise HTTPException(422, "Requested budget exceeds the configured evaluation allowance")
            selected = db.scalars(select(Document).where(Document.project_id == project_id)).all()
            if body.document_ids:
                selected = [d for d in selected if d.id in body.document_ids]
                if len(selected) != len(set(body.document_ids)):
                    raise HTTPException(422, "A selected document does not belong to this project")
            selected = [d for d in selected if d.status not in ("quarantined", "extracted", "excluded")]
            if not selected:
                raise HTTPException(422, "There are no pending source documents to extract")
            run = Run(project_id=project_id, budget_microusd=microusd(body.budget_usd),
                      document_ids=[d.id for d in selected], protocol_snapshot=project.protocol)
            db.add(run)
            db.flush()
            audit(db, "extraction_requested", project_id, user.id, run_id=run.id, budget_usd=body.budget_usd)
            db.commit()
            payload = run_payload(run)
        enqueue(run.id, background)
        return payload

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunView)
    def cancel(run_id: str, request: Request):
        with sessions() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "Run not found")
            user, _, _ = require_project(request, db, run.project_id, ("owner",))
            run.cancel_requested = True
            if run.status == "queued":
                run.status, run.finished_at = "cancelled", utcnow()
            audit(db, "cancellation_requested", run.project_id, user.id, run_id=run.id)
            db.commit()
            return run_payload(run)

    @app.post("/api/v1/runs/{run_id}/resume", status_code=202, response_model=RunView)
    def resume(run_id: str, request: Request, background: BackgroundTasks):
        with sessions() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise HTTPException(404, "Run not found")
            user, _, _ = require_project(request, db, run.project_id, ("owner",))
            if run.status not in ("failed", "cancelled", "budget_exhausted"):
                raise HTTPException(409, "Only a stopped run can be resumed")
            if db.scalar(select(Run).where(Run.project_id == run.project_id, Run.status.in_(ACTIVE))):
                raise HTTPException(409, "Another extraction is active")
            if run.spent_microusd + run.reserved_microusd >= run.budget_microusd:
                raise HTTPException(409, "This run has no remaining budget; resuming does not reset costs")
            run.cancel_requested, run.status, run.error, run.finished_at = False, "queued", None, None
            audit(db, "extraction_resumed", run.project_id, user.id, run_id=run.id)
            db.commit()
            payload = run_payload(run)
        enqueue(run_id, background, recovery=True)
        return payload

    @app.get("/api/v1/projects/{project_id}/experiments", response_model=list[Experiment])
    def experiments(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return current_experiments(db, project_id)

    @app.get("/api/v1/projects/{project_id}/conditions", response_model=list[Condition])
    def conditions(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return [{"id": e["id"] + ":condition", "experiment_id": e["id"], "sample_label": e["sample_label"],
                     "attributes": e["attributes"], "measurement_ids": [m["id"] for m in e["measurements"]]}
                    for e in current_experiments(db, project_id)]

    @app.get("/api/v1/projects/{project_id}/study-families", response_model=list[StudyFamily])
    def study_families(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            families = {}
            for e in current_experiments(db, project_id):
                f = families.setdefault(e["study_family"], {"id": e["study_family"], "publication_dois": [],
                    "document_ids": [], "experiment_ids": [], "relationship_basis": "DOI or explicit publication-version relationship"})
                if e.get("doi"):
                    f["publication_dois"] = sorted(set(f["publication_dois"] + [e["doi"]]))
                f["document_ids"] = sorted(set(f["document_ids"] + [e["document_id"]]))
                f["experiment_ids"].append(e["id"])
            return list(families.values())

    @app.get("/api/v1/projects/{project_id}/measurements", response_model=list[MeasurementView])
    def measurements(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return [{**m, "document_id": e["document_id"], "source_status": e["source_status"]}
                    for e in current_experiments(db, project_id) for m in e["measurements"]]

    @app.patch("/api/v1/measurements/{measurement_id}")
    def review(measurement_id: str, body: ReviewMeasurement, request: Request):
        with sessions() as db:
            record = db.get(MeasurementRecord, measurement_id)
            if record is None:
                raise HTTPException(404, "Measurement not found")
            user, project, _ = require_project(request, db, record.project_id, ("owner", "editor"))
            document = db.get(Document, record.document_id)
            experiment = db.get(ExperimentRecord, record.experiment_id)
            if document.status in ("quarantined", "stale") or experiment.source_status != "active":
                raise HTTPException(409, "Publication evidence requires reassessment before review")
            if body.status == "accepted" and (not record.payload.get("evidence") or record.payload.get("origin") == "curve_sample"):
                raise HTTPException(422, "Acceptance requires primary-source evidence and experimental observations")
            record.payload = {**record.payload, "status": body.status,
                "validation_notes": record.payload.get("validation_notes", []) + [f"Human review ({user.login}): {body.notes}"]}
            project.synthesis_stale = True
            audit(db, "measurement_reviewed", record.project_id, user.id, measurement_id=measurement_id,
                  status=body.status, notes=body.notes)
            db.commit()
            return record.payload

    @app.get("/api/v1/projects/{project_id}/publications", response_model=list[PublicationView])
    def publications(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return [{**p.payload, "id": p.id, "state": p.state, "sources": p.sources, "discovered_at": p.discovered_at}
                    for p in db.scalars(select(PublicationRecord).where(PublicationRecord.project_id == project_id))]

    @app.post("/api/v1/projects/{project_id}/discover")
    async def discovery(project_id: str, request: Request):
        with sessions() as db:
            user, _, _ = require_project(request, db, project_id, ("owner",))
            audit(db, "metadata_check_requested", project_id, user.id)
            db.commit()
        from .monitor import monitor_project
        return await monitor_project(project_id, sessions, settings)

    @app.post("/api/v1/publications/{publication_id}/acquire", status_code=201)
    async def acquire(publication_id: str, request: Request, body: AcquireInput | None = None):
        from .acquisition import acquire_publication
        with sessions() as db:
            publication = db.get(PublicationRecord, publication_id)
            if publication is None:
                raise HTTPException(404, "Publication not found")
            user, project, _ = require_project(request, db, publication.project_id, ("owner", "editor"))
            try:
                document = await acquire_publication(publication, db, store, settings, version=body.version if body else None)
            except (ValueError, httpx.HTTPError) as error:
                raise HTTPException(422, str(error) if isinstance(error, ValueError) else "Open access provider unavailable") from None
            project.synthesis_stale = True
            audit(db, "open_pdf_acquired", project.id, user.id, document_id=document.id, sha256=document.sha256)
            db.commit()
            return document_payload(document)

    @app.get("/api/v1/publications/{publication_id}/access")
    async def access_locations(publication_id: str, request: Request):
        from .acquisition import resolve_publication_access
        with sessions() as db:
            publication = db.get(PublicationRecord, publication_id)
            if publication is None:
                raise HTTPException(404, "Publication not found")
            require_project(request, db, publication.project_id)
            try:
                result = await resolve_publication_access(publication, settings)
            except (ValueError, httpx.HTTPError):
                raise HTTPException(422, "Access resolution unavailable; upload an authorized source PDF") from None
            return result.model_dump(mode="json")

    @app.get("/api/v1/projects/{project_id}/analysis")
    def analysis(project_id: str, request: Request):
        from .statistics.engine import descriptive
        with sessions() as db:
            _, project, _ = require_project(request, db, project_id)
            data = [Experiment.model_validate(e) for e in current_experiments(db, project_id)]
            result = descriptive(data, Protocol.model_validate(project.protocol))
            unresolved = db.scalar(select(func.count()).select_from(Document).where(Document.project_id == project_id,
                Document.status.in_(("uploaded", "extracting", "failed", "stale", "quarantined")))) or 0
            result["synthesis_stale"] = bool(project.synthesis_stale or pending_count(db, project_id) or unresolved)
            result["last_synthesis_update"] = project.last_synthesis_update
            inference = project.synthesis.get("inferential")
            inference_stale = bool(inference is not None and (result["synthesis_stale"] or
                project.synthesis.get("inferential_protocol") != project.protocol))
            result["inferential_stale"] = inference_stale
            result["inferential_result"] = None if inference_stale else inference
            result["historical_inferential_result"] = inference if inference_stale else None
            return result

    @app.post("/api/v1/projects/{project_id}/analysis")
    def synthesis(project_id: str, body: AnalysisInput, request: Request):
        from .statistics.engine import descriptive, inferential
        with sessions() as db:
            user, project, _ = require_project(request, db, project_id, ("owner", "editor"))
            protocol = Protocol.model_validate(project.protocol)
            data = [Experiment.model_validate(e) for e in current_experiments(db, project_id)]
            if body.contrasts:
                if protocol.analysis_mode != "inferential":
                    raise HTTPException(422, "Configure an inferential protocol before pooling effects")
                required = {"outcome": protocol.outcome, "outcome_definition": protocol.outcome_definition,
                            "measurement_method": protocol.measurement_method, "comparator": protocol.comparator,
                            "time_point": protocol.time_point, "unit": protocol.unit, "effect_measure": protocol.effect_measure}
                if any(not v for v in required.values()) or any(body.spec.get(k) != v for k, v in required.items()):
                    raise HTTPException(422, "Analysis specification must match the complete registered protocol")
                if body.spec.get("concentration_basis") != protocol.concentration_basis:
                    raise HTTPException(422, "Analysis concentration basis differs from the registered protocol")
                from .contrasts import evidence_contrasts
                validated = evidence_contrasts(db, project_id, body.contrasts, body.spec)
                result = inferential(validated, body.spec)
            else:
                result = descriptive(data, protocol)
            completed = not body.contrasts or result.get("status") == "completed"
            project.synthesis = {**project.synthesis, "latest_analysis_attempt": result}
            if completed:
                previous_inference = project.synthesis.get("inferential")
                if previous_inference is not None:
                    audit(db, "synthesis_superseded", project_id, user.id, kind="inferential",
                          result=previous_inference, protocol=project.synthesis.get("inferential_protocol"),
                          reason="inferential_update" if body.contrasts else "descriptive_update")
                current = {key: value for key, value in project.synthesis.items()
                           if key not in ("inferential", "inferential_protocol")}
                if body.contrasts:
                    current.update(inferential=result, inferential_protocol=protocol.model_dump(mode="json"))
                else:
                    current["descriptive"] = result
                project.synthesis = current
                project.last_synthesis_update = utcnow()
            unresolved = db.scalar(select(func.count()).select_from(Document).where(Document.project_id == project_id,
                Document.status.in_(("uploaded", "extracting", "failed", "stale", "quarantined")))) or 0
            if completed:
                project.synthesis_stale = bool(pending_count(db, project_id) or unresolved)
            audit(db, "synthesis_updated", project_id, user.id, mode="inferential" if body.contrasts else "descriptive",
                  status=result.get("status"), result=result)
            db.commit()
            return result

    @app.get("/api/v1/projects/{project_id}/benchmark")
    def benchmark(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return db.get(Project, project_id).benchmark

    @app.post("/api/v1/projects/{project_id}/benchmark")
    async def compare_reference(project_id: str, request: Request, reference_html: UploadFile = File(...)):
        from .benchmark.evaluate import compare
        from .benchmark.reference import import_reference_html
        with sessions() as db:
            user, project, _ = require_project(request, db, project_id, ("owner",))
            content = await reference_html.read(10 * 1024 * 1024 + 1)
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(413, "Reference file exceeds 10 MB")
            digest = hashlib.sha256(content).hexdigest()
            # Separate prefix and temporary directory; never passed to extraction agents.
            key = f"projects/{project_id}/benchmark-reference/{digest}.html"
            store.put(key, content, "text/html")
            import tempfile
            with tempfile.TemporaryDirectory(prefix="livingmeta-benchmark-") as folder:
                path = Path(folder) / "reference.html"
                path.write_bytes(content)
                try:
                    reference = import_reference_html(path)
                except (ValueError, json.JSONDecodeError) as error:
                    raise HTTPException(422, str(error)) from None
            doi = [d.doi for d in db.scalars(select(Document).where(Document.project_id == project_id)) if d.doi]
            data = [Experiment.model_validate(e) for e in current_experiments(db, project_id)]
            project.benchmark = {**compare(data, reference, doi), "reference_sha256": digest,
                                 "extraction_data_available": bool(data),
                                 "paid_evaluation_executed": bool(db.scalar(select(Run.id).where(
                                     Run.project_id == project_id, Run.spent_microusd > 0)))}
            if not data:
                project.benchmark = {**project.benchmark, "status": "not_run"}
            audit(db, "benchmark_compared", project_id, user.id, reference_sha256=digest)
            db.commit()
            return project.benchmark

    @app.get("/api/v1/projects/{project_id}/exports/{format_name}")
    def export(project_id: str, format_name: Literal["csv", "json", "parquet", "html"], request: Request):
        with sessions() as db:
            _, project, membership = require_project(request, db, project_id)
            info = project_payload(db, project, membership.role)
            for k, v in list(info.items()):
                if hasattr(v, "isoformat"):
                    info[k] = v.isoformat()
            content, media_type = render_export(format_name, current_experiments(db, project_id), info)
            return Response(content, media_type=media_type,
                            headers={"Content-Disposition": f'attachment; filename="evidence.{format_name}"'})

    @app.get("/api/v1/projects/{project_id}/members")
    def members(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id)
            return [{"id": m.user_id, "login": db.get(User, m.user_id).login, "role": m.role}
                    for m in db.scalars(select(Membership).where(Membership.project_id == project_id))]

    @app.post("/api/v1/projects/{project_id}/invitations", status_code=201)
    def invite(project_id: str, body: Invite, request: Request):
        with sessions() as db:
            user, _, _ = require_project(request, db, project_id, ("owner",))
            token, expires = secrets.token_urlsafe(48), utcnow() + timedelta(hours=settings.invitation_hours)
            db.add(Invitation(token_hash=token_hash(token), project_id=project_id, role=body.role, expires_at=expires))
            audit(db, "invitation_created", project_id, user.id, role=body.role)
            db.commit()
            return {"token": token, "url": settings.app_url + "/?invitation=" + token, "expires_at": expires}

    @app.post("/api/v1/invitations/{token}/accept")
    def accept_invitation(token: str, request: Request):
        with sessions() as db:
            if db.bind.dialect.name == "sqlite":
                from sqlalchemy import text
                db.execute(text("BEGIN IMMEDIATE"))
            user = require_user(request, db)
            invitation = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)).with_for_update())
            if invitation is None or aware(invitation.expires_at) < utcnow() or invitation.used_by:
                raise HTTPException(404, "Invitation is invalid, expired, or already used")
            member = db.scalar(select(Membership).where(Membership.project_id == invitation.project_id, Membership.user_id == user.id))
            if member is None:
                db.add(Membership(project_id=invitation.project_id, user_id=user.id, role=invitation.role))
            invitation.used_by = user.id
            audit(db, "invitation_accepted", invitation.project_id, user.id, role=invitation.role)
            db.commit()
            return {"project_id": invitation.project_id, "role": member.role if member else invitation.role}

    @app.get("/api/v1/projects/{project_id}/costs")
    def costs(project_id: str, request: Request):
        with sessions() as db:
            require_project(request, db, project_id, ("owner",))
            wallet = db.get(Wallet, "initial-evaluation")
            runs = db.scalars(select(Run).where(Run.project_id == project_id)).all()
            ledger = db.scalars(select(Reservation).where(Reservation.run_id.in_([r.id for r in runs]))).all() if runs else []
            return {"total_limit_usd": (wallet.limit_microusd / 1e6) if wallet else settings.max_total_openai_usd,
                    "total_spent_usd": (wallet.spent_microusd / 1e6) if wallet else 0,
                    "total_reserved_usd": (wallet.reserved_microusd / 1e6) if wallet else 0,
                    "calls": [{"id": r.id, "run_id": r.run_id, "model": r.model, "status": r.status,
                               "reserved_usd": r.reserved_microusd / 1e6, "actual_usd": None if r.actual_microusd is None else r.actual_microusd / 1e6,
                               "usage": r.usage} for r in ledger]}

    web = Path("web/dist")
    if web.is_dir():
        app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")

        @app.get("/")
        def interface():
            return FileResponse(web / "index.html")
    return app
