# syntax=docker/dockerfile:1
# The application and its fixed statistics bridge share one reproducible image.
FROM node:24.19.0-bookworm-slim AS frontend
ARG PNPM_VERSION=11.19.0
WORKDIR /frontend
RUN npm install --global pnpm@${PNPM_VERSION}
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
COPY src/livingmeta/report_assets/ /src/livingmeta/report_assets/
RUN pnpm typecheck && pnpm test && pnpm build

FROM rocker/r-ver:4.5.1 AS application-base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:$PATH \
    PRIVATE_DIRECTORY=/app/private \
    PORT=8000
WORKDIR /app
# Ubuntu 24.04 supplies Python 3.12. Assert the version rather than silently
# accepting a different interpreter if the upstream image ever changes.
RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-dev build-essential gfortran \
      ca-certificates curl libcurl4-openssl-dev libssl-dev libxml2-dev \
      libpq-dev tesseract-ocr tesseract-ocr-eng \
    && python3 -c 'import sys; assert sys.version_info[:2] == (3, 12), sys.version' \
    && rm -rf /var/lib/apt/lists/*
COPY statistics/install_packages.R /tmp/install_packages.R
COPY statistics/packages.lock.tsv /tmp/packages.lock.tsv
RUN Rscript --vanilla /tmp/install_packages.R /tmp/packages.lock.tsv
RUN python3 -m venv /opt/venv
COPY requirements.lock ./
RUN pip install --require-hashes -r requirements.lock \
    && pip install build==1.6.1 hatchling==1.32.4
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY protocols/ ./protocols/
COPY statistics/packages.lock.tsv ./statistics/packages.lock.tsv
COPY statistics/meta_analysis.R ./statistics/meta_analysis.R
RUN pip install --no-deps --no-build-isolation . \
    && python -c 'from importlib.resources import files; assert files("livingmeta.statistics").joinpath("meta_analysis.R").is_file()'
COPY --from=frontend /frontend/dist/ ./web/dist/
COPY alembic.ini ./
COPY migrations/ ./migrations/
COPY scripts/check_publication.py scripts/schema_contracts.py ./scripts/
RUN groupadd --gid 10001 livingmeta \
    && useradd --uid 10001 --gid livingmeta --create-home --shell /usr/sbin/nologin livingmeta \
    && mkdir -p /app/private \
    && chown -R livingmeta:livingmeta /app/private

# CI builds this target, verifies R explicitly, and runs synthetic offline tests.
FROM application-base AS test
COPY tests/ ./tests/
USER livingmeta
CMD ["python", "-m", "pytest", "-q"]

# Default production build: no tests, bibliography, model answers, or credentials.
FROM application-base AS runtime
USER livingmeta
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)" || exit 1
CMD ["uvicorn", "livingmeta.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
