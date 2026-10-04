# Living Meta-Analysis

[![Offline verification](https://github.com/marquezrn/living-meta-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/marquezrn/living-meta-analysis/actions/workflows/ci.yml)

Turn a bibliography of primary-source PDFs into a traceable experimental evidence
dataset, explore compatible outcomes, and follow new literature without recurring
paid model calls. The first case study concerns nanocellulose-stabilized Pickering
emulsions. All application text, code, documentation and reports are in English.

**Scientific validation is pending.** Synthetic tests do not establish extraction
accuracy. The frozen manual reference contains 103 records across 53 DOI; the
supplied local corpus represents 91 records across 46 DOI. Read the
[comparison report](docs/comparison-report.md) for coverage and outstanding evaluation.
Original PDFs and reference answers are not distributed here.

## Features

- Private projects, GitHub sign-in, owner/editor/reader permissions and single-use invitations.
- PDF folder upload, content-hash deduplication, protocol configuration, bounded
  extraction runs, progress, cancellation and checkpoint recovery.
- Specialist agents for screening, text/tables, figures, verification and disagreements;
  deterministic normalization and statistics.
- Source-linked original and normalized values, missingness, inequalities, independent
  versus technical replicates, uncertainty and measurement definitions.
- Calibrated linear/log Cartesian graphs and reproducible overlays. Microscopy requires
  verified spatial calibration; unsupported plots abstain.
- Descriptive synthesis by default. Source-linked inferential contrasts require reported
  SD, independent sample sizes, compatible definitions and dependence handling.
- OpenAlex, Crossref, PubMed, Europe PMC and arXiv metadata discovery; Scopus requires
  separately verified institutional server authorization.
- Permitted open-PDF acquisition, private evidence viewing, manual-reference comparison,
  and CSV/JSON/Parquet/HTML exports.
- Monday 08:00 Europe/Madrid metadata checks with daylight-saving handling and
  correction/retraction checks. New citations remain pending until extraction is requested.

## Start locally

Use Python **3.12**, Node **24.19.0**, and pnpm **11.19.0**.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.lock
python -m pip install hatchling==1.32.4
python -m pip install --no-deps --no-build-isolation .
cp .env.example .env
cd web
pnpm install --frozen-lockfile
pnpm build
cd ..
livingmeta serve
```

Open <http://localhost:8000>. The explicit local development sign-in is restricted
to this computer. Create a project, configure its protocol, and upload PDFs.
Uploading and inspecting a folder do not make paid model calls. Set `OPENAI_API_KEY`
privately in `.env` before starting extraction.

Local SQLite and inline execution support development. The production stack uses
PostgreSQL, Celery, a Redis-compatible queue and private R2 objects. The pinned R
engine is included in Docker; a host without R returns `engine_unavailable` for
inferential synthesis. For the full local container stack, configure GitHub OAuth
and `SESSION_SECRET` in `.env`, then run `docker compose up --build`.
See [installation](docs/installation.md).

## Budget and living updates

The initial OpenAI evaluation allowance is **USD 100 total**, shared across projects,
workers and resumed runs. Each call reserves conservative input/image and maximum
output costs before execution. Reported usage settles the reservation; unknown or
interrupted usage consumes the full reservation. SDK retries are disabled. Resuming
never resets costs. See [budget behavior](docs/budget.md).

Routine extraction uses `gpt-6.1-sol`; figures and difficult disagreements use
`gpt-6-astra` through the OpenAI Agents SDK. Source hashes, prompt versions, models,
usage, price dates, transformations and checkpoints are recorded.

The hosted metadata monitor receives no OpenAI key and never dispatches extraction.
It records separate dates for the last complete metadata check, extraction and
synthesis. Pending or invalidated evidence prevents a synthesis from being labelled
current. An hourly UTC Render trigger checks database due times; the research
schedule stays Monday 08:00 in Madrid across seasonal clock changes.

## Deployment and scientific evaluation

[`render.yaml`](render.yaml) defines the application, worker, PostgreSQL, queue and
metadata-only trigger. Configure GitHub OAuth, private R2, source credentials and
the worker OpenAI key as secrets. Hosting charges are separate from the evaluation
allowance. Follow [deployment](docs/deployment.md).

The hierarchy is publication → study family → experiment → condition → measurement.
Conditions preserve attributes and sample labels; individual measurements preserve
method, time, basis, qualifier and uncertainty. Explicit publication-version DOI
relationships group dependent evidence into families.

Figure-derived data remain uncertain until semantic adjudication. A source excerpt
containing a number does not by itself establish correct experimental assignment.
[Methodology](docs/methodology.md) covers scientific comparability and deterministic
REML/Knapp–Hartung and covariance-aware models. [Evaluation](docs/evaluation.md)
specifies blinding, family-level partitions, matching and independent adjudication.
Targets of 98% precision, 95% text/table recall and 90% figure recall are **not achieved results**.

## Development

```sh
pytest -q
ruff check src tests scripts
cd web && pnpm typecheck && pnpm test && pnpm build
cd ..
python scripts/check_publication.py
python scripts/schema_contracts.py
livingmeta inventory /path/to/primary-source-pdfs
livingmeta ingest-folder /path/to/primary-source-pdfs --project-id PROJECT_ID
livingmeta monitor-due
```

Versioned routes are under `/api/v1`; OpenAPI documentation is at `/docs`. Contracts
cover projects, protocols, publications, study families, experiments, conditions,
measurements, evidence, documents and runs. See [architecture](docs/architecture.md).

## Attribution and license

Original code and documentation are MIT licensed. The retrospective comparator is
Ronald Marquez Contreras's [published Pickering emulsion explorer](https://github.com/marquezrn03/Pickering-Emulsions-Tappi-Nano-2025/),
whose dataset declares CC BY 4.0. Its presentation preprocessing is excluded from
the frozen reference. Use [CITATION.cff](CITATION.cff) and cite every primary study.
Dependency and source-material terms remain separate; see
[third-party notices](THIRD_PARTY_NOTICES.md).
