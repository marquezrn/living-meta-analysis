# Attribution and third-party notices

The original Living Meta-Analysis code and documentation are MIT licensed. This
license does not grant rights to research articles, figures, reference datasets,
API data, dependency packages or hosted service accounts.

The retrospective manual comparator is Ronald Marquez Contreras's
[Pickering-Emulsions-Tappi-Nano-2025 explorer](https://github.com/marquezrn03/Pickering-Emulsions-Tappi-Nano-2025/).
Its dataset declares Creative Commons Attribution 4.0 and its code declares MIT.
The new repository credits that work and publishes scope fingerprints rather than
redistributing its answers. Presentation median imputation and mass-to-volume
relabelling are excluded from evaluation. Any reused reference data must retain
the attribution required by [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

The local core uses Pydantic, HTTPX, pdfplumber/pdfminer, PDFium, Pillow, NumPy, SciPy, Pint, PyYAML, Arrow and Typer, pinned in requirements-core.lock. Tesseract is optional. The legacy hosted extra additionally uses OpenAI SDK/Agents SDK, FastAPI, SQLAlchemy, Celery, PostgreSQL and storage/queue libraries from requirements.lock. Each dependency retains its own license. The runtime packages include
their upstream license/copyright files; inspect those notices before distributing
container images or vendored libraries.

The fixed statistical engine uses R, metafor and clubSandwich. R and several
statistical dependencies use GPL licenses; their license and source availability
requirements remain applicable to redistributed runtimes. The MIT license on this
repository does not relicense those packages. The Dockerfiles install upstream
packages rather than copying their source into this repository.

Source PDFs, page renders, extracted figures, overlays and private evidence remain
outside public version control. Publication discovery or institutional API access
does not confer unrestricted rights to publisher full text. Open-access locations
retain per-version license metadata, and research reports should cite their primary
sources. No publisher artwork or commercial font is bundled with the interface.

## Embedded offline reader

The checked-in reader bundles React, React DOM, scheduler and Plotly under MIT,
and Lucide under ISC with applicable Feather MIT attribution. Exact upstream
copyright and license texts are included in
[src/livingmeta/report_assets/NOTICES.txt](src/livingmeta/report_assets/NOTICES.txt)
and embedded in each HTML report. The checked-in source and pnpm lockfile support
rebuilding these assets. The reader includes no commercial fonts or primary papers.

## Public literature sources

Crossref metadata, PubMed records, Europe PMC records, PMC article versions and arXiv
records retain their own reuse terms. Public access does not grant universal rights
to abstracts or original figures. The private source bundle records per-version
licenses and checksums; review restrictions before distributing research reports.
PMC anonymous distribution requires no AWS account and does not authorize scraping
the main PMC site. See [PMC copyright](https://pmc.ncbi.nlm.nih.gov/about/copyright/)
and [Crossref reuse guidance](https://www.crossref.org/documentation/retrieve-metadata/).
