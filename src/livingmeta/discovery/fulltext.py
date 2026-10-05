"""Access resolution is separate from scientific eligibility and never bypasses paywalls."""

import hashlib
import json
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

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
    version_metadata: dict[str, dict] = Field(default_factory=dict)


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
            resolution.version_metadata[version] = data
            resolution.doi = canonical_doi(data.get("doi")) or resolution.doi
            if str(data.get("is_retracted", "")).lower() in ("yes", "true", "y"):
                resolution.publication_status = "retracted"
            fields = [("xml_url", "xml"), ("pdf_url", "pdf"), ("text_url", "text")]
            urls = [(data.get(field), kind) for field, kind in fields]
            urls += [(url, "media_or_supplement") for url in data.get("media_urls", [])]
            for url, kind in urls:
                if (not _https(url) or urlsplit(url).hostname != urlsplit(PMC_BUCKET).hostname or
                        not urlsplit(url).path.startswith(f"/{version}/")):
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


async def convert_pmc_ids(ids: list[str], credentials: dict[str, str], idtype: str = "doi") -> dict[str, str]:
    """Keyless identifier conversion, bounded batches; only PMC articles have mappings."""
    if idtype not in ("doi", "pmid", "pmcid"):
        raise ValueError("Unsupported identifier type")
    found = {}
    async with ProviderClient(credentials) as client:
        for offset in range(0, len(ids), 200):
            params = {"ids": ",".join(ids[offset:offset + 200]), "idtype": idtype, "format": "json", "tool": "livingmeta"}
            if credentials.get("contact_email"):
                params["email"] = credentials["contact_email"]
            payload = (await client.get("pubmed", "https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/",
                                        params=params)).json()
            for record in payload.get("records", []):
                if re.fullmatch(r"PMC\d+", str(record.get("pmcid", ""))) and record.get("live", True) is not False:
                    found[str(record.get("requested-id", ""))] = record["pmcid"]
    return found


def inspect_jats(source: Path, artifact_dir: Path | None = None) -> dict:
    """Inventory original JATS elements without assigning artificial PDF page numbers."""
    source = Path(source)
    if source.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("JATS exceeds the size limit")
    content = source.read_bytes()
    if len(content) > 100 * 1024 * 1024 or b"<!ENTITY" in content.upper():
        raise ValueError("JATS exceeds the size limit or declares unsupported entities")
    root = ET.fromstring(content)
    if root.tag.rsplit("}", 1)[-1] != "article":
        raise ValueError("Expected a JATS article")
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    locators = {}
    element_ids = set()

    def walk(node, path):
        if node.attrib.get("id"):
            if node.attrib["id"] in element_ids:
                raise ValueError("JATS element identifiers must be unique")
            element_ids.add(node.attrib["id"])
        locators[id(node)] = f"id:{node.attrib['id']}" if node.attrib.get("id") else path
        counts = {}
        for child in node:
            tag = child.tag.rsplit("}", 1)[-1]
            counts[tag] = counts.get(tag, 0) + 1
            walk(child, f"{path}/{tag}[{counts[tag]}]")
    walk(root, "/article")
    elements = {}
    xlink = "{http://www.w3.org/1999/xlink}href"
    units = []
    nodes = list(root.findall("./front/article-meta/abstract"))
    body = root.find("body")
    if body is not None:
        # Whole body is one source unit so section nesting cannot duplicate evidence.
        nodes.append(body)
    back = root.find("back")
    if back is not None:
        nodes.append(back)
    # Some publishers store original tables and images outside the narrative body.
    nodes.extend(root.findall("floats-group"))
    nodes.extend(root.findall("sub-article"))
    for node in nodes:
        unit_id = locators[id(node)]
        tables, figures, captions, paragraphs = [], [], [], []
        for child in node.iter():
            tag = child.tag.rsplit("}", 1)[-1]
            locator = locators[id(child)]
            literal = " ".join("".join(child.itertext()).split())
            if tag in ("p", "title", "table-wrap", "fig", "caption", "td", "th"):
                elements[locator] = {"text": literal, "source_type": "table" if tag in ("table-wrap", "td", "th") else
                                     "figure" if tag == "fig" else "text", "element_id": child.attrib.get("id")}
            if tag == "p":
                paragraphs.append({"xml_element": locator, "text": literal})
            if tag == "table-wrap":
                rows, cells = [], []
                for tr in child.findall(".//tr"):
                    row = []
                    for cell in tr:
                        if cell.tag.rsplit("}", 1)[-1] not in ("td", "th"):
                            continue
                        value = " ".join("".join(cell.itertext()).split())
                        row.append(value)
                        cells.append({"xml_element": locators[id(cell)], "text": value,
                                      "rowspan": cell.get("rowspan", "1"), "colspan": cell.get("colspan", "1")})
                    rows.append(row)
                caption = " ".join("".join(child.find("caption").itertext()).split()) if child.find("caption") is not None else ""
                tables.append({"id": child.get("id") or locator, "xml_element": locator,
                               "rows": rows, "cells": cells, "caption": caption})
            if tag == "fig":
                refs = [g.get(xlink) or g.get("href") for g in child.iter() if g.tag.rsplit("}", 1)[-1] in ("graphic", "media")]
                refs = [r for r in refs if r and not urlsplit(r).scheme and not r.startswith("/") and
                        ".." not in Path(r).parts and "\\" not in r and not any(c in r for c in "*?[]")]
                assets = []
                for ref in refs:
                    candidate = (source.parent / ref).resolve()
                    if candidate.is_relative_to(source.parent.resolve()) and candidate.is_file():
                        assets.append(str(candidate))
                    elif not Path(ref).suffix:
                        assets.extend(str(p.resolve()) for p in source.parent.glob(ref + ".*")
                                      if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(source.parent.resolve()))
                caption = " ".join("".join(child.find("caption").itertext()).split()) if child.find("caption") is not None else ""
                figures.append({"id": child.get("id") or locator, "xml_element": locator, "caption": caption,
                                "asset_refs": refs, "asset_paths": assets})
            if tag == "caption":
                captions.append({"xml_element": locator, "text": literal})
        units.append({"unit_id": unit_id, "xml_element": unit_id, "page": None, "width": None, "height": None,
                      "text": "\n".join([p["text"] for p in paragraphs] +
                                         [elements[t["xml_element"]]["text"] for t in tables] +
                                         [elements[f["xml_element"]]["text"] for f in figures]),
                      "paragraphs": paragraphs, "tables": tables,
                      "figures": figures, "captions": captions, "notes": [], "text_status": "xml_extracted", "image_path": None})
    doi = next((canonical_doi("".join(e.itertext())) for e in root.findall("./front/article-meta/article-id")
                if e.get("pub-id-type") == "doi"), None)
    identifiers = {e.get("pub-id-type"): "".join(e.itertext()).strip()
                   for e in root.findall("./front/article-meta/article-id")}
    pmid = identifiers.get("pmid")
    pmcid = identifiers.get("pmcid") or identifiers.get("pmc")
    if pmcid and pmcid.isdigit():
        pmcid = "PMC" + pmcid
    title_node = root.find("./front/article-meta/title-group/article-title")
    title = " ".join("".join(title_node.itertext()).split()) if title_node is not None else None
    result = {"document_hash": hashlib.sha256(content).hexdigest(), "page_count": 0, "source_format": "xml",
              "title": title, "doi": doi, "pmid": pmid, "pmcid": pmcid,
              "pages": units, "elements": elements, "warnings": []}
    if artifact_dir is not None:
        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        _atomic_bytes(artifact_dir / "jats-inventory.json", json.dumps(result, ensure_ascii=False).encode())
    return result


def _atomic_bytes(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)


def safe_asset_filename(url: str) -> str:
    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1])
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("Unsafe source asset filename")
    return name


async def download_asset(location: AccessLocation, destination: Path, credentials: dict[str, str],
                         max_bytes: int = 100 * 1024 * 1024) -> dict:
    """Only current PMC distribution URLs are accepted; bytes are bounded and verified."""
    parsed = urlsplit(location.url)
    if (parsed.scheme != "https" or parsed.hostname != urlsplit(PMC_BUCKET).hostname or parsed.username or
            parsed.password or parsed.port not in (None, 443) or not location.version or
            not re.fullmatch(r"PMC\d+\.\d+", location.version) or not parsed.path.startswith(f"/{location.version}/")):
        raise ValueError("Expected a permitted versioned PMC distribution URL")
    filename = safe_asset_filename(location.url)
    destination = Path(destination)
    if destination.is_symlink():
        raise ValueError("Asset destination cannot be a symlink")
    cached = destination / filename
    if cached.is_symlink():
        raise ValueError("Cached source asset cannot be a symlink")
    if (cached.is_file() and location.md5 and re.fullmatch(r"[a-fA-F0-9]{32}", location.md5) and
            cached.stat().st_size <= max_bytes):
        content = cached.read_bytes()
        if hashlib.md5(content, usedforsecurity=False).hexdigest() == location.md5.lower():
            _validate_asset(content, location.kind)
            return {**location.model_dump(mode="json"), "filename": filename,
                    "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    async with ProviderClient(credentials) as client:
        response = await client.get("pmc", location.url, max_bytes=max_bytes)
        content = response.content
    if len(content) > max_bytes:
        raise ValueError("Source asset exceeds the download size limit")
    if location.md5:
        if not re.fullmatch(r"[a-fA-F0-9]{32}", location.md5) or hashlib.md5(content, usedforsecurity=False).hexdigest() != location.md5.lower():
            raise ValueError("Source checksum mismatch")
    _validate_asset(content, location.kind)
    digest = hashlib.sha256(content).hexdigest()
    _atomic_bytes(destination / filename, content)
    return {**location.model_dump(mode="json"), "filename": filename, "sha256": digest, "bytes": len(content)}


def _validate_asset(content: bytes, kind: str):
    if kind == "pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("The source did not return a PDF")
    if kind == "xml":
        if b"<!ENTITY" in content.upper() or ET.fromstring(content).tag.rsplit("}", 1)[-1] != "article":
            raise ValueError("The source did not return a safe JATS article")
