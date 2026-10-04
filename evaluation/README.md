# Evaluation assets

The public manifest contains frozen reference fingerprints and scope counts only.
Manual answers, source PDFs and private evidence are evaluator-owned inputs outside
the versioned repository. Synthetic fixtures are generated from code in tests and
`scripts/generate_fixtures.py`; they do not contain publisher material.

Run extraction first with the registered protocol and frozen prompt. Import the
manual reference only into the comparator. Preserve DOI/condition ambiguities and
resolve disagreements against the original source. Study-family partitions and
independent source adjudication are described in `docs/evaluation.md`.
