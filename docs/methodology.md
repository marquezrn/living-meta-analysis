# Evidence-preserving living synthesis

The initial protocol concerns original experiments on nanocellulose-stabilized
Pickering emulsions. Reviews support discovery. Films and coatings contribute only
when an identifiable emulsion experiment is reported. The protocol and source-native
queries are versioned; changes produce a new documented review state.

## Extraction and scientific units

Measurements retain their original text, numeric value or bounds, units, concentration
basis, statistical definition, method, measurement time, independent and technical
replicate counts, uncertainty type, and page/figure evidence. An uncertainty label is
not assumed to be SD, and microscopy particle counts do not replace independently
prepared experimental replicates. A digitized marker is distinguished from a sampled
interpolated curve. Missing values remain missing.

Mass fractions and volume fractions are separate outcomes. Converting them requires
reported component densities and a justified composition model. Distinct diameter
definitions, including microscopy diameters, DLS distributions, and volume-weighted
laser diffraction diameters, remain separate. Figure values must preserve calibrated
axes, panel and series identity, scale type, and digitization uncertainty.

## Descriptive and inferential synthesis

Descriptive summaries include accepted, finite, exact observations only. Censored
values, unknown bounds, curve samples, uncertain fields, and rejected fields remain
visible in the evidence dataset but do not become exact points in numerical summaries.
Groups are separated by outcome, normalized unit, measurement method, statistic,
measurement time, and concentration basis. A condition-level arithmetic mean describes
the extracted conditions; it is not an inferential pooled treatment effect. The number
of study families is reported separately from the number of conditions.

Inferential synthesis requires protocol-defined outcome definition, measurement
method, comparator, time point, and unit. Each retained contrast must match those
fields and concentration basis, declare independently replicated experimental arms,
and provide the mean, SD, and independent sample size for each arm. Sample sizes must
be integers of at least two. Censored means, nonfinite data, missing uncertainty,
paired designs without a supported paired variance model, and zero sampling variances
are explicitly excluded. Their experiments remain available descriptively.

For independent experimental arms:

- Mean difference: `yi = mean_treatment - mean_control`; sampling variance
  `vi = SD_treatment²/n_treatment + SD_control²/n_control`.
- Log response ratio: `yi = log(mean_treatment/mean_control)`; delta-method sampling
  variance `vi = SD_treatment²/(n_treatment × mean_treatment²) +
  SD_control²/(n_control × mean_control²)`. Both means must be positive.

The fixed R script fits REML through pinned `metafor` 4.8-0. Independent effects use
`test="adhoc"`, the safeguarded Knapp–Hartung adjustment that does not narrow a
standard error below its unadjusted value. Repeated effects within study families
require an explicit justified covariance matrix: correct retained-ID order, sampling
variances on its diagonal, symmetry, positive definiteness, and no unexplained
between-family covariance. The multilevel REML model uses `study_family/id` random
effects and `clubSandwich` 0.6.1 CR2 Satterthwaite inference. At least three families
are required for CR2; degrees of freedom below four suppress inferential intervals and p-values; only diagnostic estimates are returned. Two
independent effects may be fitted, with a few-studies warning.

The bridge sends validated JSON to a fixed script. Neither user-provided R expressions
nor agent-generated formulas are evaluated. It reports unavailable R, package-version
mismatch, timeout, and fit failures instead of silently substituting another estimator.
`statistics/Dockerfile` installs the R package pins; `jsonlite` is fixed at 2.0.0.
Synthetic analytical tests verify effect and covariance calculations. R integration
checks are skipped honestly on hosts where R is absent and must run in the pinned
container before a scientific pooled result is published.

Primary statistical references:
[metafor effect calculations](https://wviechtb.github.io/metafor/reference/escalc.html),
[REML and safeguarded Knapp–Hartung](https://wviechtb.github.io/metafor/reference/rma.uni.html),
[multilevel covariance models](https://wviechtb.github.io/metafor/reference/rma.mv.html),
and [clubSandwich CR2](https://jepusto.github.io/clubSandwich/reference/coef_test.html).

## Retrospective benchmark and independent adjudication

The supplied manual explorer's original literal `database` array contains 103 records
across 53 DOI and 1,361 nonmissing original data fields. The planned local comparison
covers 91 records across 46 DOI; unavailable sources are counted separately. Import
only the literal database array using a restricted parser; never execute the HTML's
JavaScript. Its presentation preprocessing imputes medians for missing droplet size
and aspect ratio and copies oil mass percent to a volume-percent column. Those
presentation transformations are excluded from the reference.

Gold references, reference-derived summaries, manuscripts, and comparator outputs
must not enter extractor inputs, prompts, retrieval stores, or evidence documents.
The parser and comparator are evaluator-only components. Stable study-family hashing
assigns development or holdout partitions with an 80/20 default split; all experiments
and linked publication versions from one family use the same family identifier.

Align DOI first, then reported experimental conditions. Multiple compatible candidates
or multiple extracted records claiming one manual row are ambiguous and are not
resolved by arbitrary input order. An exact numeric comparison allows the registered
2% relative tolerance after valid unit conversion, with inequalities and concentration
bases checked independently. Categorical comparisons use literal case-insensitive
text without silently introducing domain synonym matches. Each disagreement remains
available for primary-source adjudication. Potential new fields are reported as
unverified additions until that review is completed.

The default report is **agreement with the manual reference**. It does not claim
accuracy, precision, recall, or improvement over manual extraction. Numerical errors
are exposed per field; overall MAE across mixed units is explicitly limited. Original
manual nulls are excluded from expected-field denominators.

`adjudicated_metrics` accepts independent reviewer decisions with primary-source
locators, correctness, recoverability, candidate acceptance, and source type. Missing
and abstained candidates remain in recoverable-field recall denominators. Unreviewed
fields remain pending rather than becoming verified positives. It reports accepted
field precision, recoverable recall, text/table and figure recall, abstention rate,
verified additions, cost, and Wilson intervals. Wilson field-level intervals assume independence. The evaluator also reports seeded
study-family percentile bootstrap intervals from 1,000 resamples when at least two
adjudicated families are available. Few families and uniformly correct fields can
produce unstable or degenerate intervals; publication claims require a representative
adjudicated holdout sample. Targets are 98% accepted
precision, 95% recoverable text/table recall, and 90% recoverable figure recall; they
are targets rather than achieved results.

## Living-state integrity

Metadata checks do not trigger paid extraction. Newly found publications remain
pending until the user starts a budgeted extraction. Maintain separate last metadata
check, last completed extraction, and last completed synthesis timestamps. Source
failures, page-budget exhaustion, unprocessed evidence, and status changes prevent
an outdated synthesis from being labelled current. Corrections and retractions
preserve their history and identify affected evidence and analyses; absence of a
retraction flag from another provider never restores a withdrawn study.
