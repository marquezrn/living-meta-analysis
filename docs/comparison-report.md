# Pickering emulsion retrospective comparison

Report date: 2026-10-04. Status: **source scope audited; paid extraction and independent accuracy evaluation not run**.

The comparator is the original literal dataset in Ronald Marquez Contreras's
[published nanocellulose Pickering emulsion explorer](https://github.com/marquezrn03/Pickering-Emulsions-Tappi-Nano-2025/).
The supplied HTML is frozen by SHA-256 in
[`reference_manifest.json`](../evaluation/reference_manifest.json). Its JavaScript
was not executed, and presentation imputation was not imported. Source PDFs and
manual reference answers remain private and separate from extraction inputs.

| Audited item | Result |
| --- | ---: |
| Original manual records | 103 |
| Distinct original records | 101 |
| Unique reference DOI | 53 |
| Original variables, including DOI | 21 |
| Nonmissing original fields, excluding DOI | 1,361 |
| Local primary-source PDF files | 59 |
| Local PDF pages | 902 |
| Pages with extractable text | 902 |
| Reference DOI identified in the local corpus | 46 |
| Raw reference records represented locally | 91 |
| Reference papers unavailable locally | 7 |
| Raw reference records from unavailable papers | 12 |
| Provisional development / holdout DOI families | 45 / 8 |

The DOI audit uses exact primary-source first-page text, PDF metadata and, where
needed, second-page text. It found no ambiguous DOI assignment among the 46
matched reference papers. Text availability does not establish experimental-data
recall, correct table interpretation, or recoverable figure coverage. The 59 files
also include sources outside the 53-DOI comparator; screening must determine their
eligibility. Two repeated manual records remain in the raw reference and are
reported separately from independent experiments.

The provisional split uses a fixed family hash and seed `livingmeta-v1`. Explicit
preprint/journal relationships must be reconciled before freezing an evaluation
partition. The achieved 45/8 split is reported rather than described as exactly
80/20. Holdout answers have not been supplied to extraction agents.

## Unavailable primary sources

These seven papers are outside the current local comparison scope. Their absence
must not count as an extraction failure:

| DOI |
| --- |
| 10.1016/j.carpta.2024.100574 |
| 10.1016/j.foodhyd.2024.109781 |
| 10.1016/j.foodhyd.2024.110427 |
| 10.1016/j.indcrop.2024.118967 |
| 10.1016/j.indcrop.2024.120098 |
| 10.1122/8.0000813 |
| 10.3390/foods13223706 |

## Evaluation results

| Metric | Current result | Validation target |
| --- | --- | --- |
| Accepted-field precision | Not evaluated | ≥98% |
| Recoverable text/table recall | Not evaluated | ≥95% |
| Recoverable figure recall | Not evaluated | ≥90% |
| Numerical error by field and unit | Not evaluated | Report measured distribution |
| Abstentions and page/table/figure coverage | Not evaluated | Report all attempted sources |
| Verified additions beyond manual extraction | Not evaluated | Primary-source adjudication |
| Paid OpenAI evaluation calls | 0 | Owner-initiated only |
| OpenAI evaluation expenditure | USD 0 | USD 100 total ceiling |

Offline synthetic tests verify software behavior: permissions, budget accounting,
checkpoint recovery, figure calibration, dimensional conversions, unsupported
uncertainty, source status, metadata discovery and statistical input safeguards.
They are not measurements of model extraction quality. The local host has no R
engine or Docker; GitHub's container check requires the pinned R engine and runs
its integration test. Repository CI records the verification status for each commit.

Local release verification: **124 Python tests passed; 3 skipped** (two PostgreSQL
concurrency checks and the pinned R integration check). **19 frontend tests passed**;
type checking, production build, Python linting, wheel packaging, fresh SQLite
migration and publication allowlist checks passed. The private interface was
inspected with the imported 59-source corpus and an explicitly unexecuted benchmark.
GitHub has additionally passed the PostgreSQL concurrency checks, the production
container build, exact R/package version assertions, and all 17 statistical
reference checks. The workflow badge links to verification for the current commit.

Follow the [reproducible evaluation procedure](evaluation.md) to execute a blinded
extraction, compare DOI and conditions, retain ambiguous matches, and independently
adjudicate against primary sources. Report agreement with manual extraction until
adjudication is complete. Publish denominators, family-level uncertainty, source
strata, limitations and costs whether the targets are met or missed. No claim that
the system matches or improves the manual extraction is supported yet.
