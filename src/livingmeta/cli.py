"""Administration and offline evaluation commands; source and reference inputs stay separate."""

import asyncio
import hashlib
import json
import re
from pathlib import Path

import typer
import yaml
from sqlalchemy import select

from .config import get_settings
from .db import Document, Project, initialize_database, make_database
from .domain import Experiment, Protocol
from .storage import ArtifactStore

app = typer.Typer(no_args_is_help=True, help="Living Meta-Analysis research administration")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Start the private API and the built interface."""
    import uvicorn
    uvicorn.run("livingmeta.main:app", host=host, port=port, access_log=False)


@app.command("init-db")
def init_db():
    """Initialize a new database. Existing deployments should use Alembic migrations."""
    engine, _ = make_database(get_settings())
    initialize_database(engine)
    engine.dispose()
    typer.echo("Database initialized")


@app.command("monitor-due")
def monitor_due():
    """Run due weekly metadata checks with zero OpenAI calls."""
    from .monitor import run_due_monitoring
    settings = get_settings()
    engine, sessions = make_database(settings)
    reports = run_due_monitoring(sessions, settings)
    typer.echo(json.dumps({"projects_checked": len(reports), "openai_calls": 0,
                           "reports": reports}, indent=2))
    engine.dispose()


@app.command("ingest-folder")
def ingest_folder(folder: Path, project_id: str):
    """Import only primary-source PDFs into an existing local project; no paid calls."""
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
    reference = import_reference_html(reference_html)
    raw = json.loads(extracted_json.read_text())
    data = [Experiment.model_validate(e) for e in (raw["experiments"] if isinstance(raw, dict) else raw)]
    doi = json.loads(available_dois.read_text())
    result = compare(data, reference, doi)
    result["reference_sha256"] = hashlib.sha256(reference_html.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
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
