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

The application uses the OpenAI Agents SDK and Python SDK, FastAPI, SQLAlchemy,
Celery, PostgreSQL, Redis-compatible services, React, Plotly, pdfplumber/pdfminer,
PDFium, Tesseract, NumPy, SciPy, Pint, Arrow and other packages pinned in the
lockfiles. Each dependency retains its own license. The runtime packages include
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
