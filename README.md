# Living Meta-Analysis

[![Verification](https://github.com/marquezrn/living-meta-analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/marquezrn/living-meta-analysis/actions/workflows/ci.yml)

**Download the repository. Provide a papers folder. Let your existing coding agent
extract a traceable experimental dataset and generate an interactive offline report.**

The default edition runs on your computer. It requires no hosted server, GitHub
OAuth, database service, cloud storage, or paid API key. It supports chemistry and
chemical engineering, with a supplied nanocellulose Pickering emulsion protocol.
All code, prompts, documentation, interfaces, and reports are in English.

Scientific extraction accuracy remains **unvalidated**. The manual comparator has
103 records across 53 DOI; the supplied private corpus covers 91 records across
46 DOI. Software checks and synthetic examples do not establish extraction accuracy.
See the [comparison report](docs/comparison-report.md).

## Install once

Download the [repository ZIP](https://github.com/marquezrn/living-meta-analysis/archive/refs/heads/main.zip)
or clone it. Use Python 3.12–3.14; Python 3.12 is the primary verified runtime.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-core.lock
python -m pip install hatchling==1.32.4
python -m pip install --no-deps --no-build-isolation .
livingmeta doctor
```

On Windows activate with `.venv\Scripts\Activate.ps1` instead. No Node.js or Docker
is required. Dependencies are downloaded during installation; subsequent local
preparation, validation, statistics, and report generation work offline.
OCR additionally requires Tesseract. Inferential statistics require the
[pinned R engine](docs/installation.md); descriptive synthesis works without it.

## Use your existing agent

Open this repository in an agent that can read local files, run Python tools, and
inspect images. Give it this instruction, replacing both folder paths:

```text
Read AGENTS.md. Use /path/to/primary-source-papers as my papers folder and
/path/to/private-review as my output workspace. Apply the supplied nanocellulose
protocol. Complete the available extraction jobs, validate all evidence, calculate
eligible summaries, and generate the offline report. Preserve uncertain results.
```

The private workspace must be outside this code checkout. Keep primary papers
separate from manual answers, manuscripts, and derived summaries. The portable
workflow uses `prepare`, `next-job`, and `submit`; see [agent instructions](AGENTS.md)
and [local workflow](docs/local-workflow.md).

For automatic coordinated specialists with Codex:

```sh
codex login
livingmeta doctor --check-agent
livingmeta run --papers /path/to/primary-source-papers --workspace /path/to/private-review
```

The runner checks ChatGPT sign-in and refuses API-key fallback. It uses two
concurrent jobs by default, bounded attempts, fresh specialist contexts, and
resumable checkpoints. Your account's normal subscription, credits, and usage
limits still apply. Cloud agents need internet access. The toolkit does not buy
credits, change plans, or invoke a paid provider API itself.
[Codex authentication](https://learn.chatgpt.com/docs/auth) and
[structured execution](https://learn.chatgpt.com/docs/non-interactive-mode) describe
the underlying client behavior.

Open the resulting `exports/report.html` by double-clicking. It contains its own
interface, dataset, and chart libraries; it uses no application server or CDN.
Select local PDFs/images in the report to inspect their evidence and verify hashes.
Exports also include JSON, CSV, and Parquet.

```sh
livingmeta run --workspace /path/to/private-review --resume
livingmeta synthesize --workspace /path/to/private-review
livingmeta report --workspace /path/to/private-review
```

## Optional living updates

Papers already in your folder are sufficient. To explicitly check free sources:

```sh
livingmeta discover --workspace /path/to/private-review --contact-email researcher@example.org --download
livingmeta monitor-due --workspace /path/to/private-review --contact-email researcher@example.org
livingmeta schedule --workspace /path/to/private-review --contact-email researcher@example.org
```

PubMed and Europe PMC provide discovery; Crossref supplies broader scholarly
metadata and publication updates. arXiv is optional. Available PMC content comes
from its supported anonymous distribution, including structured text and original
figure assets. No AWS account is required. Contact email identifies your requests;
it is not an API credential. [Source documentation](docs/sources.md).

Scheduling is opt-in: `schedule` generates instructions and native scheduler
templates without registering a task. The local monitor checks Mondays at 08:00
Europe/Madrid across daylight-saving changes and performs one overdue check after
a missed run. It never starts extraction. New sources remain pending until you
start your agent. Offline, partial, pending, and stale states remain visible.

## Scientific guarantees and limits

- Publication → study family → experiment → condition → measurement, with
  original values, units, methods, uncertainty, replicates, and source locations.
- Exact source checks, deterministic conversions, immutable hashes, page/table/
  figure coverage, and calibrated geometry. Unsupported figures abstain.
- Experimental markers remain separate from interpolated curves. Microscopy
  counts never substitute for independent experimental preparations.
- Descriptive synthesis by default. Inferential contrasts require compatible
  outcomes, reported sampling information, and justified dependence handling.
- Model agreement is not independently verified accuracy. Manual-reference
  comparisons remain agreement until source adjudication.

Read [methodology](docs/methodology.md), [evaluation](docs/evaluation.md),
[file contracts](docs/architecture.md), and [local limitations](docs/local-workflow.md).
The 98% precision, 95% text/table recall, and 90% figure recall targets are not
achieved results.

## Development and legacy hosting

Development checks cover the local workflow, optional legacy hosted application,
offline report, and pinned R reference calculations. Install `.[dev,hosted]` or the
full `requirements.lock` for all checks. Rebuild the packaged report with
`node scripts/build_report.mjs`; end users receive the built assets.

The previous hosting code and configuration remain optional for compatibility;
they are excluded from ordinary installation and are not part of the local setup.
See [legacy deployment](docs/deployment.md). Render/R2 provisioning is not required.

## Attribution and license

Original code and documentation are MIT licensed. Cite this software with
[CITATION.cff](CITATION.cff) and cite every primary study. The retrospective reference
is Ronald Marquez Contreras's
[published Pickering emulsion explorer](https://github.com/marquezrn03/Pickering-Emulsions-Tappi-Nano-2025/).
Its dataset declares CC BY 4.0. Source PDFs, reference answers, private evidence,
and generated research reports are not distributed in this repository.
Dependencies and source materials retain their own terms; see
[third-party notices](THIRD_PARTY_NOTICES.md).
