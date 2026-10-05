# Local architecture and versioned interfaces

The default interface is the `livingmeta` CLI and version **2** file contracts.
No API server, database, queue service, cloud storage, or provider SDK is imported by
the local engine. The optional hosted application retains its `/api/v1` contracts.

## Data flow

Primary-source folder → immutable source copies and inventory → screening jobs →
text/table and figure jobs → deterministic normalization/source verification →
independent specialist review → bounded discrepancy resolution → dataset →
deterministic synthesis → offline HTML/JSON/CSV/Parquet.

A private workspace contains `manifest.json`, `dataset.json`, immutable job requests,
mutable claim state, candidate responses, source/render artifacts, and append-only
`events.jsonl`. SHA-256 identities cover source bytes, job requests, inputs, protocol,
and engine/prompt information. Atomic writes and OS file locks coordinate bounded
workers without a database. Source and protocol changes invalidate dependent outputs.

## Public file contracts

- **Run manifest:** schema version, protocol snapshot/hash, source identity/format,
  run state/reason, timestamps, checkpoints, actual coverage, literature state, and
  synthesis freshness. Structural inventory is distinct from reviewed coverage.
- **Job request:** immutable request ID/hash, phase, document hash, permitted relative
  input/image paths and hashes, instructions, phase payload, and result JSON Schema.
  A claim adds worker/token/attempt/expiry outside the immutable request hash.
- **Agent response:** request ID/hash, claim token, result, and truthful runtime/model/
  usage metadata. Responses are untrusted and may not dictate accepted status or paths.
- **Evidence dataset:** publications, study families, experiments, conditions,
  measurements, attributes, exact evidence locations, statuses, qualifiers, uncertainty,
  independent/technical replication, and transformation provenance.
- **Report snapshot:** protocol, documents/publications, dataset, coverage, synthesis,
  benchmark state, run/provenance, and distinct metadata/extraction/synthesis dates.

PDF evidence uses real page indices and geometry. XML uses explicit element locations;
media uses source assets and calibration. Neither XML nor media invents a PDF page.
DOI, PMID, PMCID, publication version, and directed update relationships remain explicit.
Condition identity includes preparation context; a reused sample label alone cannot
merge distinct experiments.

`python scripts/schema_contracts.py` exports the local JSON schemas without hosting
dependencies. `--hosted` exports the legacy OpenAPI document with hosted extras installed.

## Agent transport

Portable agents claim and submit file jobs. The Codex adapter invokes the existing
CLI in fresh read-only contexts with schema-constrained final responses and image
inputs. It checks ChatGPT authentication, removes API-key variables, and refuses
alternate billing modes. Progress/usage are private event records; unknown monetary
cost is null. Authentication/account-limit failures pause rather than fall back.

## Offline report

A dedicated read-only entry bundles classic JavaScript, CSS, React/Plotly, and safely
escaped snapshot JSON into one HTML file. It does not load the hosted App, fetch API
routes, import module chunks, or use a CDN. Explicit local-file selection creates
temporary Blob previews after hash verification. Report opening never refreshes
metadata or re-labels a stale synthesis current.

## Optional sources and statistics

Explicit discovery uses public PubMed/Europe PMC/Crossref endpoints and permitted
PMC distribution; arXiv is optional. Downloads preserve licenses and checksums and
remain pending until extraction. Monitoring is metadata-only and has no agent access.

Descriptive calculations use deterministic Python functions. Eligible source-linked
contrasts use the fixed R/metafor/clubSandwich engine; missing sampling information,
compatibility, covariance, or engine availability produces a documented limitation.
