# Reproducible retrospective evaluation

This procedure separates software verification, agreement with the manual explorer,
and independently adjudicated accuracy. None substitutes for the others.

## Freeze and blind

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
   source folders, agent inputs and retrieval stores. Only primary PDF pages reach agents.
4. Partition by stable study-family hashing (`family_split`), default 80% development
   and 20% holdout. Explicit preprint/journal relationships share a family. Freeze the
   prompt and protocol before inspecting holdout answers. Publish the achieved family
   allocation and any deviations; the nominal split need not produce exact row percentages.

## Execute and compare

Record the software revision, lockfiles, source hashes, protocol, prompt version,
model IDs, price date, source page coverage, reservations and actual/unknown usage.
Run the evaluation within the shared USD 100 allowance. Budget exhaustion is a
reported outcome; unfinished pages do not become fully covered evidence.

Export extraction JSON independently before importing the manual reference into
the evaluator. Use the interface benchmark import or:

```sh
livingmeta benchmark-file /private/reference.html /private/extracted.json /private/available-dois.json --output private/comparison.json
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
No real paid extraction or independent performance adjudication has yet run.
Precision, recall, improvement and cost per recovered field are therefore unavailable.
See [comparison report](comparison-report.md).
