# Local installation

Use Python 3.12–3.14. Python 3.12 is the primary verified version. Download or clone
the repository, then create a virtual environment and install the core hash lock:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements-core.lock
python -m pip install hatchling==1.32.4
python -m pip install --no-deps --no-build-isolation .
livingmeta doctor
```

Windows uses `.venv\Scripts\Activate.ps1`. No Node.js, Docker, database server,
Render account, R2 bucket, OAuth application, or provider API key is required.
Installation downloads dependencies once. Local preparation, validation, descriptive
analysis, and report generation subsequently work offline. Agent inference and optional
literature discovery depend on the selected agent/provider and internet availability.

The packaged wheel contains the offline report assets, nanocellulose protocol, and
fixed statistical script. Browser opening requires no compilation or service.

## Existing agent

Use a coding agent with local-file, shell, structured-output, and image capabilities.
Open this repository and follow `AGENTS.md`. Other agents use file jobs without an
adapter installation. Automatic coordination is tested with Codex CLI; sign in with
ChatGPT using the client's own sign-in flow, then run `livingmeta doctor --check-agent`.
The toolkit never reads credential files or uses API-key fallback.

## Optional OCR

Install Tesseract through your operating system's package manager. `livingmeta doctor`
reports availability; `prepare --ocr` uses it when appropriate. Missing OCR is an
explicit capability limitation. Model vision and rendered evidence remain available.

## Optional inferential statistics

Install R **4.5.1** and the exact package versions in `statistics/packages.lock.tsv`
using `statistics/install_packages.R`. The fixed engine checks its interpreter and
package versions and runs `--vanilla`; user-supplied R code is not accepted.
`metafor` is pinned to **4.8-0**, `clubSandwich` to **0.6.1**, and `jsonlite` to **2.0.0**.

The optional statistics container remains available for reproducible validation.
R/Docker are not necessary for descriptive synthesis. Missing or mismatched engines
produce explicit limitations, never substituted Python pooled estimates.

## Development and optional legacy hosting

The full `requirements.lock` includes developer and hosted dependencies. Install it
with hashes for all tests, then install the source package without dependency resolution.
The local-only dependency set is `requirements-core.lock`; `scripts/lock_core.py`
derives it from the full verified lock and dependency metadata.

Building the reader requires Node 24.19.0 and pnpm 11.19.0 only for maintainers:

```sh
cd web
pnpm install --frozen-lockfile
pnpm typecheck
pnpm test
cd ..
node scripts/build_report.mjs
```

Legacy hosting is optional: see `docs/hosted-installation.md` and `docs/deployment.md`.
Its services are excluded from the default local workflow. The legacy database monitor
command is now `livingmeta hosted-monitor-due`; `monitor-due` targets a file workspace.
