"""Administration and offline evaluation commands; source and reference inputs stay separate."""

import asyncio
import hashlib
import json
import re
import shutil
from importlib import resources
from pathlib import Path

import typer
import yaml
from .domain import Experiment, Protocol

app = typer.Typer(no_args_is_help=True, help="Living Meta-Analysis: local papers, agents, and offline reports")


def _protocol(path: Path | None) -> Protocol:
    if path is not None:
        return Protocol.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    packaged = resources.files("livingmeta").joinpath("protocols/nanocellulose.yaml")
    if packaged.is_file():
        return Protocol.model_validate(yaml.safe_load(packaged.read_text(encoding="utf-8")))
    checkout = Path(__file__).resolve().parents[2] / "protocols/nanocellulose.yaml"
    return Protocol.model_validate(yaml.safe_load(checkout.read_text())) if checkout.is_file() else Protocol()


def _emit(value):
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False, default=str))


@app.command()
def doctor(check_agent: bool = False):
    """Check installed local capabilities; do not execute a model or contact a provider."""
    from . import __version__
    result = {"version": __version__, "workflow": "local", "hosting_required": False,
              "api_key_required": False, "tesseract": bool(shutil.which("tesseract")),
              "Rscript": bool(shutil.which("Rscript")), "codex": bool(shutil.which("codex")),
              "model_calls": 0}
    if check_agent:
        from .local.codex import AgentUnavailable, check_codex
        try:
            result["agent"] = check_codex()
        except AgentUnavailable as exc:
            result["agent"] = {"ready": False, "reason": str(exc)}
    _emit(result)


@app.command()
def prepare(papers: Path, workspace: Path = typer.Option(...), protocol: Path | None = None,
            ocr: bool = False):
    """Prepare cached private evidence jobs from a primary-source folder; no network calls."""
    from .local.workflow import prepare_workspace
    _emit(prepare_workspace(papers, workspace, _protocol(protocol), use_ocr=ocr))


@app.command("next-job")
def next_job_command(workspace: Path = typer.Option(...), worker: str = "portable", reclaim: bool = False):
    """Claim the next job for an existing coding agent; return null when none is ready."""
    from .local.workspace import next_job
    _emit(next_job(workspace, worker=worker, reclaim=reclaim))


@app.command()
def submit(response: Path, workspace: Path = typer.Option(...)):
    """Validate a candidate response and continue the scientific workflow."""
    from .local.workflow import submit_response
    if response.stat().st_size > 5_000_000:
        raise typer.BadParameter("Agent response exceeds 5 MB")
    _emit(submit_response(workspace, json.loads(response.read_text(encoding="utf-8"))))


@app.command()
def run(workspace: Path = typer.Option(...), papers: Path | None = None, protocol: Path | None = None,
        agent: str = "codex", model: str | None = None, concurrency: int = 2, timeout: int = 900,
        resume: bool = False, max_jobs: int | None = None, ocr: bool = False):
    """Run bounded specialists using the existing ChatGPT-authenticated Codex CLI."""
    from .local.codex import AgentUnavailable, run_jobs
    from .local.workflow import prepare_workspace
    if agent != "codex":
        raise typer.BadParameter("Automatic execution currently supports codex. Other agents use next-job and submit.")
    if papers is not None:
        prepare_workspace(papers, workspace, _protocol(protocol), use_ocr=ocr)
    try:
        result = run_jobs(workspace, model=model, concurrency=concurrency, timeout=timeout,
                          resume=resume, max_jobs=max_jobs,
                          progress=lambda value: typer.echo(f"{value['phase']}: {value['state']}", err=True))
    except AgentUnavailable as exc:
        _emit({"status": "agent_unavailable", "reason": str(exc), "api_key_fallback": False})
        raise typer.Exit(2) from exc
    if result.get("snapshot") is None:
        from .local.workflow import finalize_workspace
        result["snapshot"] = finalize_workspace(workspace)
    if result.get("snapshot") is not None:
        from .local.synthesis import synthesize_workspace
        from .local.workspace import load_snapshot
        from .report import export_snapshot, generate_report
        synthesize_workspace(workspace)
        snapshot = load_snapshot(workspace)
        output = workspace / "exports"
        output.mkdir(parents=True, exist_ok=True)
        generate_report(snapshot, output / "report.html")
        for name in ("json", "csv", "parquet"):
            export_snapshot(snapshot, output / f"dataset.{name}", name)
        result.pop("snapshot")
        result["report"] = str((output / "report.html").resolve())
    _emit(result)


@app.command()
def validate(workspace: Path = typer.Option(...)):
    """Validate frozen inputs, current evidence and workflow state without agents."""
    from .local.workflow import finalize_workspace
    _emit(finalize_workspace(workspace))


@app.command()
def synthesize(workspace: Path = typer.Option(...)):
    """Calculate source-compatible summaries with the deterministic scientific engine."""
    from .local.synthesis import synthesize_workspace
    _emit(synthesize_workspace(workspace))


@app.command()
def report(workspace: Path = typer.Option(...), output: Path | None = None):
    """Generate an interactive HTML report that opens without a server."""
    from .local.workspace import load_snapshot
    from .report import generate_report
    _emit({"report": str(generate_report(load_snapshot(workspace), output or workspace / "exports/report.html"))})


@app.command("export")
def export_command(workspace: Path = typer.Option(...), format: str = "json", output: Path | None = None):
    """Export the private evidence snapshot as JSON, CSV or Parquet."""
    from .local.workspace import load_snapshot
    from .report import export_snapshot
    if format not in {"json", "csv", "parquet"}:
        raise typer.BadParameter("Choose json, csv, or parquet")
    destination = output or workspace / f"exports/dataset.{format}"
    result = export_snapshot(load_snapshot(workspace), destination, format)
    _emit({"export": str(result or destination)})


@app.command()
def discover(workspace: Path = typer.Option(...), contact_email: str = typer.Option(...),
             sources: str | None = None, download: bool = False):
    """Explicitly check free literature sources; discovered papers remain pending."""
    from .local.literature import discover_workspace
    selected = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    _emit(asyncio.run(discover_workspace(workspace, contact_email, sources=selected, download=download)))


@app.command("monitor-due")
def local_monitor_due(workspace: Path = typer.Option(...), contact_email: str = typer.Option(...)):
    """Perform one due Monday metadata check; never run extraction."""
    from .local.literature import monitor_due
    _emit(asyncio.run(monitor_due(workspace, contact_email)))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Start the private API and the built interface."""
    import uvicorn
    uvicorn.run("livingmeta.main:app", host=host, port=port, access_log=False)


@app.command("init-db")
def init_db():
    """Initialize a new database. Existing deployments should use Alembic migrations."""
    from .config import get_settings
    from .db import initialize_database, make_database
    engine, _ = make_database(get_settings())
    initialize_database(engine)
    engine.dispose()
    typer.echo("Database initialized")


@app.command("hosted-monitor-due")
def monitor_due():
    """Run due weekly metadata checks with zero OpenAI calls."""
    from .monitor import run_due_monitoring
    from .config import get_settings
    from .db import make_database
    settings = get_settings()
    engine, sessions = make_database(settings)
    reports = run_due_monitoring(sessions, settings)
    typer.echo(json.dumps({"projects_checked": len(reports), "openai_calls": 0,
                           "reports": reports}, indent=2))
    engine.dispose()


@app.command("ingest-folder")
def ingest_folder(folder: Path, project_id: str):
    """Import only primary-source PDFs into an existing local project; no paid calls."""
    from sqlalchemy import select
    from .config import get_settings
    from .db import Document, Project, make_database
    from .storage import ArtifactStore
    settings = get_settings()
    engine, sessions = make_database(settings)
    store = ArtifactStore(settings)
    added = duplicates = 0
    with sessions() as db:
        project = db.get(Project, project_id)
        if not project:
            raise typer.BadParameter("Create a project in the application first")
        for path in sorted(folder.glob("*.pdf")):
            content = path.read_bytes()
            if not content.startswith(b"%PDF-") or len(content) > settings.max_upload_bytes:
                raise typer.BadParameter(f"Invalid or oversized PDF: {path.name}")
            digest = hashlib.sha256(content).hexdigest()
            if db.scalar(select(Document).where(Document.project_id == project_id, Document.sha256 == digest)):
                duplicates += 1
                continue
            key = f"projects/{project_id}/documents/{digest}.pdf"
            store.put(key, content, "application/pdf")
            db.add(Document(project_id=project_id, filename=path.name, sha256=digest, storage_key=key))
            db.flush()
            added += 1
        project.synthesis_stale = project.synthesis_stale or added > 0
        db.commit()
    engine.dispose()
    typer.echo(json.dumps({"added": added, "duplicates": duplicates, "paid_calls": 0}))


@app.command()
def inventory(folder: Path, output: Path = Path("private/corpus-inventory.json")):
    """Inspect primary-source structure without reference answers or OpenAI calls."""
    from .extraction.layout import inspect_pdf
    documents = []
    paths = sorted(folder.glob("*.pdf"))
    for index, path in enumerate(paths, 1):
        record = inspect_pdf(path, use_ocr=False)
        # First-page candidates are metadata hints, never a verified publication identity.
        first_page = record["pages"][0]["text"] if record["pages"] else ""
        doi = sorted(set(re.findall(r"10\.\d{4,9}/[^\s\"<>]+", first_page, re.I)))
        documents.append({"filename": path.name, "sha256": record["document_hash"],
            "pages": record["page_count"], "doi_candidates": [v.rstrip(".,;)").lower() for v in doi],
            "text_pages": sum(bool(p["text"].strip()) for p in record["pages"]),
            "table_candidates": sum(len(p["tables"]) for p in record["pages"]),
            "figure_candidates": sum(len(p["figures"]) for p in record["pages"]), "warnings": record["warnings"]})
        typer.echo(f"Inspected {index}/{len(paths)} source PDFs", err=True)
    payload = {"mode": "structural_inventory", "paid_calls": 0, "documents": documents,
               "document_count": len(documents), "page_count": sum(d["pages"] for d in documents),
               "limitations": ["Structural candidates do not establish scientific extraction coverage",
                               "DOI candidates require primary-source identity verification"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    typer.echo(str(output.resolve()))


@app.command("benchmark-file")
def benchmark_file(reference_html: Path, extracted_json: Path, available_dois: Path,
                   output: Path = Path("private/comparison.json")):
    """Compare an already completed extraction with a private frozen original reference."""
    from .benchmark.evaluate import compare
    from .benchmark.reference import import_reference_html
    # Freeze extraction bytes before the separate evaluator reads manual answers.
    frozen = extracted_json.read_bytes()
    extraction_hash = hashlib.sha256(frozen).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    frozen_path = output.parent / f"frozen-extraction-{extraction_hash}.json"
    if frozen_path.exists() and frozen_path.read_bytes() != frozen:
        raise typer.BadParameter("Frozen extraction integrity mismatch")
    frozen_path.write_bytes(frozen)
    raw = json.loads(frozen)
    payload = raw.get("dataset", raw) if isinstance(raw, dict) else raw
    data = [Experiment.model_validate(e) for e in (payload["experiments"] if isinstance(payload, dict) else payload)]
    reference = import_reference_html(reference_html)
    doi = json.loads(available_dois.read_text())
    result = compare(data, reference, doi)
    result["reference_sha256"] = hashlib.sha256(reference_html.read_bytes()).hexdigest()
    result["extraction_sha256"] = extraction_hash
    result["evaluation"] = {"blinding": "unblinded", "interpretation": "agreement_before_adjudication",
                            "frozen_extraction": frozen_path.name}
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    typer.echo(str(output.resolve()))


@app.command("validate-protocol")
def validate_protocol(path: Path = Path("protocols/nanocellulose.yaml")):
    """Validate an English YAML research protocol against the shared schema."""
    protocol = Protocol.model_validate(yaml.safe_load(path.read_text()))
    typer.echo(protocol.model_dump_json(indent=2))


@app.command("check-access")
def check_access(doi: str):
    """Resolve open access locations; never downloads or starts paid extraction."""
    from .discovery.fulltext import resolve_unpaywall
    from .config import get_settings
    result = asyncio.run(resolve_unpaywall(doi, {"contact_email": get_settings().contact_email}))
    typer.echo(result.model_dump_json(indent=2))


@app.command("adjudicate-file")
def adjudicate_file(decisions_json: Path, output: Path = Path("private/adjudicated-metrics.json"),
                    total_cost_usd: float | None = None):
    """Calculate performance from independent reviewer decisions, never model confidence."""
    from .benchmark.adjudication import adjudicated_metrics
    result = adjudicated_metrics(json.loads(decisions_json.read_text()), total_cost_usd)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    typer.echo(str(output.resolve()))


@app.command()
def schedule(workspace: Path = typer.Option(...), contact_email: str = typer.Option(...)):
    """Generate opt-in local scheduling templates; do not install a recurring task."""
    from .local.scheduling import write_templates
    _emit(write_templates(workspace, contact_email))


if __name__ == "__main__":
    app()
