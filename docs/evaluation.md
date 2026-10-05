# Reproducible retrospective evaluation

This procedure separates software verification, agreement with the manual explorer,
and independently adjudicated accuracy. None substitutes for the others.

## Freeze and isolate

1. Freeze the original literal `database` array from the supplied HTML. Record the
   HTML and canonical array SHA-256, source URL, date, 103 raw rows, 53 DOI and two
   duplicate raw records. Do not execute the reference JavaScript or use its
   median-imputed export. `evaluation/reference_manifest.json` publishes counts and
   fingerprints, not answers.
2. Inventory the original PDF folder independently. The supplied case has 59 PDFs
   and 902 text-bearing pages. A first-page/metadata/second-page DOI check identifies
   46 benchmark DOI and 91 raw rows, without ambiguous DOI matches. Report seven
   unavailable papers separately rather than treating their fields as extraction failures.
3. Keep reference HTML, manuscripts, derived summaries and adjudicated answers outside
   source folders, agent inputs and retrieval stores. Only primary-source PDF/JATS/media inputs reach agents. Folder separation and instructions do not prove blinding. A published blind benchmark requires a fresh isolated environment that blocks manual answers and external retrieval; ordinary portable or automatic runs are labelled unblinded.
4. Partition by stable study-family hashing (`family_split`), default 80% development
   and 20% holdout. Explicit preprint/journal relationships share a family. Freeze the
   prompt and protocol before inspecting holdout answers. Publish the achieved family
   allocation and any deviations; the nominal split need not produce exact row percentages.

## Execute and compare

Record the software revision, lockfiles, source hashes, protocol, prompt version,
model IDs when reported, source-unit coverage, attempts and actual/unknown usage.
The default local runner uses the existing agent account and never falls back to a paid API key. Record account limits, pauses and available usage; do not infer dollar costs from token counts. The USD 100 ceiling applies only to an explicitly enabled legacy API evaluation, not to a subscription account allowance. Unfinished source units do not become fully covered evidence.

Export extraction JSON independently before importing the manual reference into
the evaluator. The local command freezes extraction bytes and records reference/output hashes before evaluation:

```sh
livingmeta benchmark-file /private/reference.html /private/extracted.json /private/available-dois.json --output /private/comparison.json
```

The comparator matches DOI and experimental conditions, retains ambiguous or
conflicting matches, checks units/basis and inequalities, and applies a preregistered
2% relative numerical agreement tolerance. It reports per-field numerical error;
mixed-unit overall MAE is not a scientific performance summary. Separate results
by field, unit, source type and family. Do not count two manual duplicate records
as two independently recovered experiments.

## Independent source adjudication

For each expected recoverable field, label recoverability against the primary
source, including figures and absent/abstained candidates. Record family, experiment,
field, source type, locator, reviewer, candidate acceptance and correctness. An exact
value match to a flawed manual row is agreement, not verified accuracy. Examine new
fields against primary evidence before calling them verified additions.

`livingmeta.benchmark.adjudication.adjudicated_metrics` reports accepted-field
precision, recoverable-field recall, source-specific recall, abstentions, verified
additions, cost and uncertainty. Wilson intervals accompany family bootstrap intervals
where enough independently adjudicated families exist. Missing adjudication stays
pending. Include a representative holdout sample; do not select only easily read plots.

Targets are ≥98% accepted-field precision, ≥95% recoverable text/table recall and
≥90% recoverable figure recall. Publish measured estimates, denominators, intervals,
remaining uncertainty, coverage and cost whether targets are met or missed. A claim
of matching or improving manual extraction requires completed independent validation.

## Current result

The source/reference scope has been audited. Offline tests exercise synthetic PDF,
figure, unit, authorization, budget, recovery, discovery and statistics contracts.
No primary-paper extraction or independent performance adjudication has yet run. A native Codex synthetic pilot first paused at the existing account usage limit without API fallback. After reset, three native specialist jobs recovered the fixture means (7 and 5 um), SDs (1 and 0.5 um), and independent n=3 for both arms. Unreported fields remained abstained. This validates transport and fixture behavior, not primary-paper performance.
Precision, recall, improvement and cost per recovered field are therefore unavailable.
See [comparison report](comparison-report.md).
