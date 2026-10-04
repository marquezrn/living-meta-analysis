"""Access resolution is separate from scientific eligibility and never bypasses paywalls."""

import re
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlsplit

from pydantic import BaseModel, Field

from livingmeta.discovery.identity import canonical_doi
from livingmeta.discovery.service import ProviderClient

PMC_BUCKET = "https://pmc-oa-opendata.s3.amazonaws.com"


class AccessLocation(BaseModel):
    url: str
    kind: str
    provider: str
    license: str | None = None
    version: str | None = None
    manuscript: bool = False
    md5: str | None = None
    redistribution_requires_license_review: bool = True


class AccessResolution(BaseModel):
    doi: str | None = None
    pmcid: str | None = None
    locations: list[AccessLocation] = Field(default_factory=list)
    status: str = "not_available"
    publication_status: str = "active"
    limitations: list[str] = Field(default_factory=list)


def _https(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    return url if parsed.scheme == "https" and parsed.hostname and not parsed.username else None


async def resolve_unpaywall(doi: str, credentials: dict[str, str]) -> AccessResolution:
    doi = canonical_doi(doi)
    if not doi:
        raise ValueError("A valid DOI is required")
    resolution = AccessResolution(doi=doi)
    email = credentials.get("contact_email")
    if not email:
        resolution.limitations.append("Unpaywall requires a contact email")
        return resolution
    async with ProviderClient(credentials) as client:
        data = (await client.get("unpaywall", f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                                 params={"email": email})).json()
    for location in data.get("oa_locations", []):
        url = _https(location.get("url_for_pdf") or location.get("url"))
        if url:
            resolution.locations.append(AccessLocation(url=url,
                                                        kind="pdf" if location.get("url_for_pdf") else "landing_page",
                                                        provider="unpaywall", license=location.get("license"),
                                                        version=location.get("version")))
    resolution.status = "available" if resolution.locations else "not_available"
    return resolution


async def resolve_pmc(pmcid: str, credentials: dict[str, str]) -> AccessResolution:
    """Enumerate every permitted version; a higher number is not necessarily preferred."""
    pmcid = pmcid.upper()
    if not re.fullmatch(r"PMC\d+", pmcid):
        raise ValueError("Expected a PMCID such as PMC10009402")
    resolution = AccessResolution(pmcid=pmcid)
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    async with ProviderClient(credentials) as client:
        continuation = None
        prefixes = []
        for _ in range(20):
            params = {"list-type": "2", "prefix": f"{pmcid}.", "delimiter": "/"}
            if continuation:
                params["continuation-token"] = continuation
            xml = (await client.get("pmc", PMC_BUCKET + "/", params=params)).text
            tree = ET.fromstring(xml)
            prefixes.extend(p.text for p in tree.findall("s:CommonPrefixes/s:Prefix", ns) if p.text)
            continuation = tree.findtext("s:NextContinuationToken", namespaces=ns)
            if not continuation:
                break
        else:
            resolution.limitations.append("PMC version pagination limit reached")
        for prefix in prefixes:
            version = prefix.rstrip("/")
            if not re.fullmatch(rf"{pmcid}\.\d+", version):
                continue
            data = (await client.get("pmc", f"{PMC_BUCKET}/{quote(prefix, safe='/')}{version}.json")).json()
            resolution.doi = canonical_doi(data.get("doi")) or resolution.doi
            if str(data.get("is_retracted", "")).lower() in ("yes", "true", "y"):
                resolution.publication_status = "retracted"
            fields = [("xml_url", "xml"), ("pdf_url", "pdf"), ("text_url", "text")]
            urls = [(data.get(field), kind) for field, kind in fields]
            urls += [(url, "media_or_supplement") for url in data.get("media_urls", [])]
            for url, kind in urls:
                if not _https(url) or urlsplit(url).hostname != urlsplit(PMC_BUCKET).hostname:
                    continue
                from urllib.parse import parse_qs
                resolution.locations.append(AccessLocation(
                    url=url, kind=kind, provider="pmc_aws", license=data.get("license_code"),
                    version=version, manuscript=str(data.get("is_manuscript", "")).lower() in ("yes", "true", "y"),
                    md5=parse_qs(urlsplit(url).query).get("md5", [None])[0]))
    resolution.status = "available" if resolution.locations else "not_available"
    if not resolution.locations:
        resolution.limitations.append("No distributed version was found; absence does not exclude a study")
    return resolution
