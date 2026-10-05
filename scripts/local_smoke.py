"""Core-only installation smoke: synthetic evidence, zero network, zero model calls."""

import argparse
import json
import socket
import sys
import tempfile
from pathlib import Path

from livingmeta.domain import Protocol
from livingmeta.local.synthesis import synthesize_workspace
from livingmeta.local.workflow import finalize_workspace, next_job, prepare_workspace, submit_response
from livingmeta.local.workspace import load_snapshot
from livingmeta.report import export_snapshot, generate_report


def write_pdf(path):
    text = "Sample A: droplet size 7 um; SD 1 um; 3 independent preparations."
    stream = f"BT /F1 12 Tf 40 740 Td ({text}) Tj ET".encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
               f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"]
    output, offsets = bytearray(b"%PDF-1.4\n"), []
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(output)
    return text


def smoke(output=None):
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected network access during deterministic processing")
    socket.socket, socket.create_connection = blocked, blocked
    with tempfile.TemporaryDirectory(prefix="livingmeta-offline-") as directory:
        root = Path(directory)
        papers = root / "papers"
        papers.mkdir()
        text = write_pdf(papers / "synthetic.pdf")
        workspace = root / "workspace"
        prepare_workspace(papers, workspace, Protocol())
        phases = []
        while (job := next_job(workspace)) is not None:
            phases.append(job["phase"])
            if job["phase"] == "screening":
                result = {"eligible": True, "eligibility_reason": "Synthetic original experimental study"}
            elif job["phase"] == "text_table":
                evidence = {"document_hash": job["document_hash"], "page": 1,
                            "source_type": "text", "locator": "Synthetic paragraph", "excerpt": text}
                result = {"eligible": True, "eligibility_reason": "Synthetic original experimental study",
                          "experiments": [{"id": "candidate", "sample_label": "Sample A",
                            "study_family": "candidate", "measurements": [{"id": "candidate", "experiment_id": "candidate",
                            "outcome": "Droplet_Size_um", "raw_value": "7", "value": 7, "unit": "um",
                            "uncertainty": 1, "uncertainty_type": "SD", "n_independent": 3, "evidence": [evidence]}]}]}
            elif job["phase"] == "verification":
                result = {}
            else:
                raise AssertionError(f"Unexpected synthetic phase: {job['phase']}")
            submit_response(workspace, {"schema_version": 2, "request_id": job["id"],
                "request_hash": job["request_hash"], "claim_token": job["claim_token"], "result": result,
                "agent": {"runtime": "deterministic-synthetic-smoke", "model_calls": 0}})
        snapshot = finalize_workspace(workspace)
        assert snapshot["run"]["status"] == "completed"
        measurement = snapshot["experiments"][0]["measurements"][0]
        assert measurement["status"] == "accepted" and measurement["normalized_value"] == 7
        synthesize_workspace(workspace)
        snapshot = load_snapshot(workspace)
        destination = Path(output).resolve() if output else root / "exports"
        destination.mkdir(parents=True, exist_ok=True)
        generate_report(snapshot, destination / "report.html")
        for name in ("json", "csv", "parquet"):
            export_snapshot(snapshot, destination / f"dataset.{name}", name)
        for name in ("openai", "agents", "sqlalchemy", "fastapi", "boto3", "celery"):
            assert name not in sys.modules, f"Hosted dependency imported: {name}"
        return {"status": "passed", "network_access": "blocked", "model_calls": 0, "phases": phases,
                "accepted_measurements": 1, "formats": ["html", "json", "csv", "parquet"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    print(json.dumps(smoke(parser.parse_args().output), indent=2))
