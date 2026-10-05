"""Self-contained, read-only reports; no server, authentication, or provider calls."""

import csv
import base64
import hashlib
import copy
import html
import io
import json
import os
import re
import tempfile
from importlib.resources import files
from pathlib import Path

from .domain import Experiment
from .exports import measurement_rows

SCHEMA_VERSION = 2
SECTIONS = ("project", "protocol", "freshness", "publications", "documents", "experiments", "conditions", "study_families", "coverage",
            "synthesis", "benchmark", "run", "provenance", "artifacts", "embedded_artifacts")
LIST_SECTIONS = {"publications", "documents", "experiments", "conditions", "study_families", "artifacts", "embedded_artifacts"}


def normalize_snapshot(snapshot: dict) -> dict:
    """Validate an offline snapshot without inventing observations or review decisions."""
    if not isinstance(snapshot, dict) or snapshot.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("Expected a schema_version 2 offline snapshot")
    # Strict JSON serialization catches non-finite numbers and unsupported objects anywhere.
    json.dumps(snapshot, allow_nan=False)
    snapshot = copy.deepcopy(snapshot)
    output = {"schema_version": SCHEMA_VERSION}
    for section in SECTIONS:
        value = snapshot.get(section)
        if value is None:
            value = [] if section in LIST_SECTIONS else {}
        if section in LIST_SECTIONS and (not isinstance(value, list) or any(not isinstance(v, dict) for v in value)):
            raise ValueError(f"{section} must be an array of objects")
        if section not in LIST_SECTIONS and not isinstance(value, (dict, list)):
            raise ValueError(f"{section} must be an object or array")
        output[section] = value
    if not isinstance(output["project"], dict) or not isinstance(output["protocol"], dict) or not isinstance(output["freshness"], dict):
        raise ValueError("Project, protocol, and freshness must be objects")
    if output["project"].get("name") is not None and not isinstance(output["project"]["name"], str):
        raise ValueError("Project name must be text")
    experiment_ids, measurement_ids = set(), set()
    for experiment in output["experiments"]:
        if experiment.get("id") in experiment_ids:
            raise ValueError("Experiment IDs must be unique")
        experiment_ids.add(experiment.get("id"))
        canonical = Experiment.model_validate(experiment).model_dump(mode="json")
        for measurement in experiment.get("measurements", []):
            if not measurement.get("id") or measurement["id"] in measurement_ids:
                raise ValueError("Measurement IDs must be present and unique")
            measurement_ids.add(measurement["id"])
            if measurement.get("experiment_id") != experiment["id"]:
                raise ValueError("Measurement experiment_id does not match its parent experiment")
        canonical["measurements"] = [{**original, **parsed} for original, parsed in zip(experiment.get("measurements", []), canonical["measurements"])]
        canonical["attributes"] = [{**original, **parsed} for original, parsed in zip(experiment.get("attributes", []), canonical["attributes"])]
        experiment.update(canonical)
    for document in output["documents"]:
        if any(not isinstance(document.get(key), str) for key in ("id", "filename", "sha256", "status")):
            raise ValueError("Documents need a text ID, filename, SHA-256, and status")
    for artifact in output["embedded_artifacts"]:
        if artifact.get("mime_type") not in ("image/png", "image/jpeg", "image/webp"):
            raise ValueError("Only explicit private PNG, JPEG, and WebP images may be embedded")
        try:
            image = base64.b64decode(artifact["base64"], validate=True)
        except (KeyError, ValueError, TypeError):
            raise ValueError("Embedded image data is invalid") from None
        if len(image) > 25 * 1024 * 1024 or hashlib.sha256(image).hexdigest() != artifact.get("sha256"):
            raise ValueError("Embedded image size or SHA-256 integrity validation failed")
    return output


def _atomic_write(output: Path, content: bytes) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return output


def generate_report(snapshot: dict, output: Path) -> Path:
    """Write a portable HTML snapshot with packaged CSS, Plotly, and reader code.

    Reports may contain private source excerpts. They belong in the user's private
    output folder; this function never publishes or copies original source PDFs.
    """
    snapshot = normalize_snapshot(snapshot)
    assets = files("livingmeta").joinpath("report_assets")
    try:
        reader = assets.joinpath("reader.js").read_text(encoding="utf-8")
        stylesheet = assets.joinpath("reader.css").read_text(encoding="utf-8")
        notices = assets.joinpath("NOTICES.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RuntimeError("Packaged offline reader assets are unavailable; reinstall the complete package") from None
    # Prevent HTML parser break-out from both JSON and bundled JavaScript strings.
    payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c")
    reader = re.sub(r"</script", r"<\\/script", reader, flags=re.I)
    stylesheet = re.sub(r"</style", "", stylesheet, flags=re.I)
    project = snapshot.get("project") or {}
    title = html.escape((project.get("name") or "Living Meta-Analysis") if isinstance(project, dict) else "Living Meta-Analysis")
    policy = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
              "connect-src 'none'; img-src data: blob:; frame-src blob:; object-src blob:; "
              "base-uri 'none'; form-action 'none'; font-src 'none'")
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{html.escape(policy, quote=True)}">
<meta name="color-scheme" content="light"><title>{title}</title><style>{stylesheet}</style></head>
<body><div id="root"></div><noscript>This report requires JavaScript for local filtering and charts. No server connection is required.</noscript>
<script type="application/json" id="report-data">{payload}</script><script>{reader}</script>
<details class="report-notices"><summary>Software license notices</summary><pre>{html.escape(notices)}</pre></details>
</body></html>'''
    return _atomic_write(output, document.encode("utf-8"))


def export_snapshot(snapshot: dict, output: Path, format_name: str) -> Path:
    """Write scientific JSON, spreadsheet-safe CSV, or Parquet without imputation."""
    snapshot = normalize_snapshot(snapshot)
    if format_name == "json":
        content = json.dumps(snapshot, indent=2, ensure_ascii=False, allow_nan=False).encode()
    else:
        rows = measurement_rows(snapshot["experiments"])
        if format_name == "csv":
            stream = io.StringIO()
            columns = sorted({key for row in rows for key in row}) or ["experiment_id", "doi", "outcome"]
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: "'" + value if isinstance(value, str) and value.startswith(("=", "+", "-", "@")) else value
                                 for key, value in row.items()})
            content = stream.getvalue().encode()
        elif format_name == "parquet":
            import pyarrow as pa
            import pyarrow.parquet as pq
            table = pa.Table.from_pylist([{key: json.dumps(value) if isinstance(value, (dict, list)) else value
                                          for key, value in row.items()} for row in rows])
            stream = io.BytesIO()
            pq.write_table(table, stream)
            content = stream.getvalue()
        else:
            raise ValueError("Supported offline export formats: json, csv, parquet")
    return _atomic_write(output, content)
