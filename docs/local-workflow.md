# Local workflow and file contracts

The local edition separates agent interpretation from deterministic scientific
validation. Preparation and reporting are ordinary Python operations. Your existing
agent supplies candidate observations through files; the toolkit does not invoke a
provider SDK or require API credentials.

## Prepare sources

Keep primary articles and permitted supplements in a folder separate from reference
answers and derived summaries. Use a private output workspace outside the code checkout.

```sh
livingmeta prepare /path/to/papers --workspace /path/to/private-review
```

Preparation hashes sources, creates immutable copies, inventories text/tables/figures,
renders evidence images, and creates screening jobs. Repeated preparation reuses
hash-validated caches. `--ocr` enables installed Tesseract when needed; OCR output
requires visual verification. A structural candidate is not completed scientific review.
Optional downloaded PMC XML/media preserve element and asset locations instead of
invented PDF page numbers.

## Execute with an existing agent

A capable agent can claim and answer jobs without Codex:

```sh
livingmeta next-job --workspace /path/to/private-review --worker my-agent
livingmeta submit /path/to/response.json --workspace /path/to/private-review
```

The JSON request lists immutable identity, hashes, permitted relative paths, images,
phase instructions, phase-specific JSON Schema, and payload. The response envelope is:

```json
{
  "schema_version": 2,
  "request_id": "REQUEST_ID",
  "request_hash": "REQUEST_SHA256",
  "claim_token": "CLAIM_TOKEN",
  "result": {},
  "agent": {"runtime": "your-agent", "observed_model": null}
}
```

Populate `result` according to that request's schema. Keep metadata truthful; use
unknown values instead of guessing model IDs, monetary costs, or blinding. Responses
are limited to 5 MB. The engine validates identifiers, hashes, paths, source evidence,
and phase contracts. Agents cannot mark unsupported measurements accepted by simply
writing an accepted status.

Jobs cover screening, text/tables, figures, independent verification, and bounded
resolution. Atomic file claims prevent duplicate workers from processing the same
job. Two total attempts are permitted per job; exhausted jobs retain explicit
abstentions. Expired claims are reclaimed only on explicit resume/reclaim. Portable agents can resume a stopped workspace with next-job --reclaim; authentication/account interruptions do not consume the scientific failure allowance. Agent
transcripts and responses remain private. Requests committed before their initial state
are recovered without agent calls after schema and hash validation. Claim tokens prevent
an expired worker from releasing a newer worker's job. A committed response with missing
state requires checkpoint restoration rather than repeating extraction.

## Automatic Codex execution

```sh
livingmeta doctor --check-agent
livingmeta run --workspace /path/to/private-review --concurrency 2
```

`run --papers PATH` also prepares a new workspace. The runner checks installed CLI
capabilities and ChatGPT sign-in without a model turn, strips API-key environment
variables, forces ChatGPT authentication, ignores the user's execution configuration,
disables web search/MCP configuration, and creates fresh read-only specialist contexts.
It never changes billing modes. Ordinary agent runs remain **unblinded**: read-only
permissions and instructions alone do not prove inaccessible reference files.

The default time limit is 900 seconds per job. Concurrency may be 1–4. Use
`--max-jobs NUMBER` for a bounded pilot; remaining evidence stays pending. Authentication
or account-limit failures pause the run; `--resume` continues retained checkpoints.
Ctrl+C cancels active children and retains completed evidence. Account usage is recorded
when available, but no dollar amount is inferred from token counts.

## Validate, synthesize, and export

```sh
livingmeta validate --workspace /path/to/private-review
livingmeta synthesize --workspace /path/to/private-review
livingmeta report --workspace /path/to/private-review
livingmeta export --workspace /path/to/private-review --format json
livingmeta export --workspace /path/to/private-review --format csv
livingmeta export --workspace /path/to/private-review --format parquet
```

The dataset preserves original/normalized values, missingness, qualifications, units,
methods, sampling information, independent condition identity, evidence, and review
state. Calibration does not prove correct scientific assignment: uncertainty remains
visible when semantic adjudication is outstanding. Descriptive synthesis includes only
accepted compatible observations. Inferential analysis additionally requires valid
source-linked contrasts and the fixed R engine; missing requirements yield limitations.

Open `exports/report.html` directly in a browser. It embeds its data, styles, and
chart libraries. No hosting, authentication, server, CDN, or fetch request is used.
You may select local source PDFs/images for hash verification and evidence viewing;
the browser cannot automatically read arbitrary local paths. Sharing the report may
share source excerpts and research data, so generated reports remain private by default.

## Living updates

`discover` and `monitor-due` are explicit network operations against public sources.
They never call an AI provider or dispatch extraction. New sources remain pending.
Scheduling templates are opt-in and check hourly; the scientific due time is Monday
08:00 Europe/Madrid. A sleeping computer performs one overdue check on its next run.
Partial monitoring preserves the previous successful-check date. Changed content,
retractions, protocol changes, or pending evidence prevent a current synthesis label.

## Evaluation boundary

Freeze outputs before separately importing the manual reference with `benchmark-file`.
Comparison is agreement until independent primary-source adjudication. A blind evaluation
requires a fresh context and an isolated environment with inaccessible manual answers
and disabled external retrieval; ordinary folder separation cannot establish that.
The implementation supplies reproducible evaluator tools, not a claim that extraction
matches the manual reference. See `docs/evaluation.md`.
