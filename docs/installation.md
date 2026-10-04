# Installation and operation

Python 3.12 is the tested interpreter. Install the hash-locked requirements,
`hatchling==1.32.4`, and the package as shown in the README. Build the interface
with pnpm 11.19.0 and Node >=22.13. Start the server from the repository root so
`web/dist` is found. No credentials are needed to inspect PDFs or run offline tests.

The direct local installation uses SQLite, a private local artifact directory,
and inline background execution. Its development sign-in is an explicit local-only
action; it is disabled in production. The Docker stack uses PostgreSQL 16, a
Redis-compatible queue, Celery and the pinned R engine. It requires GitHub OAuth
even locally. Register callback `http://localhost:8000/auth/github/callback` and
configure a long random session secret privately. Source documents, local database
files, rendered images and benchmark inputs belong under ignored `private/`.

## Import a bibliography

Create a project in the interface, then choose a PDF folder in Bibliography & runs.
The interface batches uploads in groups of 30 and skips non-PDF files. Server-side
hashing prevents duplicate imports within a project. Only original source PDFs
belong in this folder; keep manuscripts, manual answers and derived summaries
separate. Alternatively:

```sh
livingmeta ingest-folder /path/to/primary-source-pdfs --project-id PROJECT_ID
livingmeta inventory /path/to/primary-source-pdfs --output private/inventory.json
```

The inventory reports structure and DOI candidates; it is not a scientific
extraction or validated figure count. Configure the protocol before starting a
budgeted extraction. New runs select pending, failed or corrected source documents;
already completed and quarantined sources are not silently reprocessed.

## Recovery and exports

Cancellation takes effect before the next paid call or immediately after a saved
checkpoint. A call already sent can still be charged. Resuming stopped runs keeps
the same protocol snapshot, checkpoints and spent budget. A queued task recovered
after worker loss conservatively accounts for any unresolved reservations before
continuing. Keep the database and artifact store together when restoring a project.
Do not delete or recreate the budget wallet to reset the initial allowance.

PDF parsing, OCR and upstream APIs can fail. The run or source report records an
explicit limitation; it never replaces unavailable data with invented results.
Tesseract is bundled in Docker. If it is absent locally, OCR is reported unavailable.
The Python statistical bridge reports unavailable R instead of substituting a
different estimator. Export CSV, JSON, Parquet or a standalone HTML evidence report
from the interface; exports retain source links, missingness and review status.

## Verify an installation

```sh
pytest -q
ruff check src tests scripts
cd web
pnpm typecheck
pnpm test
pnpm build
cd ..
python scripts/check_publication.py
```

The tests use synthetic sources and fake agent responses. A skipped R integration
test on a host without R is an explicit limitation. The CI container job requires
the pinned R interpreter before running the statistical integration test.

To generate reusable synthetic PDF and calibrated graph fixtures without model
calls, run `python scripts/generate_fixtures.py`. Outputs go to ignored
`private/synthetic-fixtures/` and include a hash manifest with expected values.
Keep synthetic fixtures separate from a real evaluation bibliography.
