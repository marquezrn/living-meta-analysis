"""Bounded access to permitted open PDFs; no payment or paywall bypass."""

import hashlib
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from sqlalchemy import select

from .db import Document

TRUSTED_PDF_HOSTS = frozenset({"pmc-oa-opendata.s3.amazonaws.com", "pmc.ncbi.nlm.nih.gov",
    "europepmc.org", "arxiv.org", "export.arxiv.org", "pubs.acs.org", "pubs.rsc.org",
    "onlinelibrary.wiley.com", "www.sciencedirect.com", "link.springer.com", "www.nature.com",
    "www.mdpi.com", "www.frontiersin.org", "api.elsevier.com"})


def validate_download_url(url):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in TRUSTED_PDF_HOSTS or parsed.username
            or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("This PDF host is not approved for server downloads; upload an authorized copy instead")


async def download_pdf(url, max_bytes, *, transport=None):
    async with httpx.AsyncClient(timeout=90, follow_redirects=False, trust_env=False, transport=transport) as client:
        for _ in range(6):
            validate_download_url(url)
            async with client.stream("GET", url, headers={"User-Agent": "LivingMeta/0.1 (research PDF acquisition)"}) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                if int(response.headers.get("content-length", "0")) > max_bytes:
                    raise ValueError("Open PDF exceeds the configured upload size")
                content = bytearray()
                async for part in response.aiter_bytes():
                    content.extend(part)
                    if len(content) > max_bytes:
                        raise ValueError("Open PDF exceeds the configured upload size")
                if not content.startswith(b"%PDF-"):
                    raise ValueError("The source returned a landing page or access restriction instead of a PDF")
                return bytes(content), url
        raise ValueError("Too many redirects while acquiring a PDF")


async def resolve_publication_access(publication, settings):
    from .discovery.fulltext import convert_pmc_ids, resolve_pmc, resolve_unpaywall
    from .discovery.service import SourceUnavailable
    if publication.payload.get("status", "active") != "active":
        raise ValueError("Publication notices require review; this source cannot be acquired as active evidence")
    credentials = {"contact_email": settings.contact_email}
    pmcid = publication.payload.get("pmcid")
    source_id = publication.payload.get("source_id", "")
    if not pmcid and source_id.startswith("PMC"):
        pmcid = source_id.split(":")[-1]
        if pmcid.isdigit():
            pmcid = f"PMC{pmcid}"
    converter_unavailable = False
    if not pmcid and publication.doi:
        try:
            pmcid = (await convert_pmc_ids([publication.doi], credentials)).get(publication.doi)
        except (SourceUnavailable, httpx.HTTPError):
            converter_unavailable = True
    if pmcid:
        resolution = await resolve_pmc(pmcid, credentials)
    elif publication.doi:
        resolution = await resolve_unpaywall(publication.doi, credentials)
    else:
        raise ValueError("No DOI or PMCID is available for access resolution")
    if resolution.publication_status != "active":
        raise ValueError("Access metadata flags this publication; scientific reassessment is required")
    if converter_unavailable:
        resolution.limitations.append("PMC identifier conversion was unavailable; DOI access fallback was used")
    return resolution


async def acquire_publication(publication, db, store, settings, *, version=None):
    resolution = await resolve_publication_access(publication, settings)
    locations = [location for location in resolution.locations if location.kind == "pdf"]
    if version:
        locations = [location for location in locations if location.version == version]
    elif any(location.version == "publishedVersion" for location in locations):
        locations = [location for location in locations if location.version == "publishedVersion"]
    elif len({location.version for location in locations}) > 1:
        raise ValueError("Multiple PDF versions are available; inspect the access locations and select a version explicitly")
    failures = []
    for location in locations:
        if location.kind != "pdf":
            continue
        try:
            content, final_url = await download_pdf(location.url, settings.max_upload_bytes)
        except (ValueError, httpx.HTTPError) as error:
            failures.append(f"{location.provider}: {type(error).__name__}")
            continue
        digest = hashlib.sha256(content).hexdigest()
        document = db.scalar(select(Document).where(Document.project_id == publication.project_id, Document.sha256 == digest))
        if document is None:
            key = f"projects/{publication.project_id}/documents/{digest}.pdf"
            store.put(key, content, "application/pdf")
            name = Path(urlsplit(final_url).path).name
            document = Document(project_id=publication.project_id, filename=(name if name.lower().endswith(".pdf") else "open-source.pdf")[:255],
                sha256=digest, storage_key=key, doi=publication.doi, inventory={"access": location.model_dump(mode="json"),
                    "final_url": final_url, "redistribution": "Private evidence only; license review required for redistribution"})
            db.add(document)
            db.flush()
        publication.state = "pending_extraction"
        return document
    raise ValueError("No permitted PDF was acquired. Upload an authorized copy. " + "; ".join(failures))
