# Pickering emulsion retrospective comparison

Report date: 2026-10-04. Status: **source scope audited; local edition implemented; primary-paper extraction and independent accuracy evaluation not run**.

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
| Separate paid OpenAI API evaluation calls | 0 | Excluded from the local workflow |
| Separate paid API evaluation expenditure | USD 0 | Legacy USD 100 ceiling if explicitly enabled |
| Existing-account agent usage | Synthetic pilot paused at account limit | Record reported usage; no inferred dollar cost |

Offline synthetic tests verify software behavior: permissions, budget accounting,
checkpoint recovery, figure calibration, dimensional conversions, unsupported
uncertainty, source status, metadata discovery and statistical input safeguards.
They are not measurements of model extraction quality. The local host has no R
engine or Docker; GitHub's container check requires the pinned R engine and runs
its integration test. Repository CI records the verification status for each commit.

Local edition verification (October 5, 2026): **234 Python tests passed; 3 skipped**
(two PostgreSQL checks and the pinned R host integration check). **53 frontend tests
passed**, including standalone reader startup with network and eval blocked. Python
linting, TypeScript checking, the preserved static demo build, wheel construction,
and a fresh core-only wheel install passed. The deterministic synthetic workflow
completed preparation, verification, descriptive synthesis, and HTML/JSON/CSV/Parquet
exports with network access blocked and no hosted SDK imports. Additional release
checks are recorded in CI for each published revision.

The real Codex transport authenticated through ChatGPT and paused cleanly at the
account usage limit without API fallback. After reset, three native screening,
text/table, and verification jobs recovered the synthetic fixture means (7 and 5 um),
SDs (1 and 0.5 um), and independent n=3 for both arms. Unreported fields remained
abstained. This is successful native transport/fixture validation; extraction accuracy
on primary papers remains unmeasured. See [machine-readable software results](../evaluation/local_validation.json). The native browser policy prevented
visual inspection through `file://`; automated file-URL DOM tests passed, but they
do not replace desktop visual verification. Cross-platform core installation jobs
are configured in CI; their results must be checked before claiming those platforms
verified. Connector requests were mocked, so live provider coverage is unmeasured.

The earlier hosted release's
[verification run](https://github.com/marquezrn/living-meta-analysis/actions/runs/37191366690)
passed its PostgreSQL concurrency and pinned R container checks. That historical
result does not validate new local code or establish extraction performance.

Follow the [reproducible evaluation procedure](evaluation.md) to execute an isolated
extraction, compare DOI and conditions, retain ambiguous matches, and independently
adjudicate against primary sources. Report agreement with manual extraction until
adjudication is complete. Publish denominators, family-level uncertainty, source
strata, limitations and costs whether the targets are met or missed. No claim that
the system matches or improves the manual extraction is supported yet.
