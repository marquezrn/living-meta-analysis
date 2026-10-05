"""Export local file contracts by default; hosted OpenAPI is an explicit extra."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--hosted", action="store_true")
    arguments = parser.parse_args()
    if arguments.hosted:
        from livingmeta.api import create_app
        from livingmeta.config import Settings
        settings = Settings(_env_file=None, database_url="sqlite:///:memory:",
                            private_directory=Path("private/contracts"))
        schema = create_app(settings, dispatch=lambda run: None).openapi()
        output = arguments.output or Path("private/contracts/openapi.json")
    else:
        from livingmeta.domain import Citation, Condition, Evidence, Experiment, ExtractionBatch, Measurement, Protocol
        from livingmeta.local.contracts import EvidenceDataset, FileResponse, JobRequest, ReportSnapshot, RunManifest
        from livingmeta.extraction.figures import FigureReview
        from livingmeta.extraction.verification import VerificationReview
        models = (RunManifest, EvidenceDataset, ReportSnapshot, JobRequest, FileResponse, Citation, Condition, Evidence, Experiment,
                  ExtractionBatch, Measurement, Protocol, FigureReview, VerificationReview)
        schema = {"schema_version": 2, "contracts": {model.__name__: model.model_json_schema() for model in models}}
        output = arguments.output or Path("private/contracts/file-contracts.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
