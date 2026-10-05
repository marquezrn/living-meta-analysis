"""Paginated, resumable native-provider searches. This module never invokes AI."""

import asyncio
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from livingmeta.discovery.identity import canonical_doi, preserve_status, reconcile_citations
from livingmeta.domain import Citation, Protocol, PublicationUpdate


class DiscoveryResult(BaseModel):
    citations: list[Citation] = Field(default_factory=list)
    source_runs: list[dict] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    checkpoint: dict = Field(default_factory=dict)
    complete: bool = False


class SourceUnavailable(RuntimeError):
    pass


class ProviderClient:
    """Shared pacing within a worker; Redis pacing spans multiple hosted workers."""

    _locks: dict[str, asyncio.Lock] = {}
    _next: dict[str, float] = {}
    intervals = {"pubmed": 0.35, "arxiv": 3.1, "crossref": 1.1, "openalex": 0.12,
                 "europepmc": 1.0, "scopus": 0.5, "unpaywall": 0.1, "pmc": 0.2}

    def __init__(self, credentials: dict[str, str], transport: httpx.AsyncBaseTransport | None = None):
        self.credentials = credentials
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(30), transport=transport,
                                       headers={"User-Agent": "LivingMetaAnalysis/0.1 (research metadata client)",
                                                "Accept": "application/json"}, follow_redirects=False)
        self.redis = None
        if credentials.get("redis_url"):
            from redis.asyncio import Redis
            self.redis = Redis.from_url(credentials["redis_url"])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.client.aclose()
        if self.redis:
            await self.redis.aclose()

    async def _pace(self, source: str):
        interval = self.intervals[source]
        if self.credentials.get("pace_directory"):
            # Local processes share provider slots without Redis or a database service.
            from pathlib import Path

            def reserve_slot():
                directory = Path(self.credentials["pace_directory"])
                directory.mkdir(parents=True, exist_ok=True)
                with (directory / f"{source}.pace").open("a+", encoding="utf-8") as handle:
                    try:
                        import fcntl
                    except ImportError:
                        import msvcrt
                        if handle.tell() == 0:
                            handle.write("0")
                            handle.flush()
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_EX)
                    handle.seek(0)
                    try:
                        previous = float(handle.read() or "0")
                    except ValueError:
                        previous = 0
                    now = time.time()
                    slot = max(now, previous)
                    handle.seek(0)
                    handle.truncate()
                    handle.write(str(slot + interval))
                    handle.flush()
                    handle.seek(0)
                    if "msvcrt" in locals():
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle, fcntl.LOCK_UN)
                    return slot - now
            delay = await asyncio.to_thread(reserve_slot)
            if delay > 0:
                await asyncio.sleep(delay)
            return
        if self.redis:
            # Acquire a provider-wide slot atomically using server time, avoiding clock skew.
            script = """local t=redis.call('TIME'); local n=t[1]*1000+t[2]/1000;
            local last=tonumber(redis.call('GET',KEYS[1]) or '0'); local slot=math.max(n,last);
            redis.call('SET',KEYS[1],slot+tonumber(ARGV[1]),'PX',math.ceil(slot-n)+60000); return slot-n"""
            delay = await self.redis.eval(script, 1, f"livingmeta:pace:{source}", int(interval * 1000))
            if delay > 0:
                await asyncio.sleep(delay / 1000)
            return
        lock = self._locks.setdefault(source, asyncio.Lock())
        async with lock:
            delay = self._next.get(source, 0) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next[source] = time.monotonic() + interval

    async def get(self, source: str, url: str, *, max_bytes: int | None = None, **kwargs) -> httpx.Response:
        for attempt in range(4):
            await self._pace(source)
            if max_bytes is None:
                response = await self.client.get(url, **kwargs)
            else:
                async with self.client.stream("GET", url, **kwargs) as streamed:
                    if int(streamed.headers.get("content-length", "0")) > max_bytes:
                        raise SourceUnavailable(f"{source}: download size limit exceeded")
                    content = bytearray()
                    async for chunk in streamed.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise SourceUnavailable(f"{source}: download size limit exceeded")
                    response = httpx.Response(streamed.status_code, headers=streamed.headers,
                                              content=bytes(content), request=streamed.request)
            if response.status_code not in (429, 502, 503, 504):
                # Do not include URLs or bodies that can contain credential query parameters.
                if response.status_code >= 400:
                    raise SourceUnavailable(f"{source}: provider returned HTTP {response.status_code}")
                return response
            if attempt == 3:
                raise SourceUnavailable(f"{source}: bounded retries exhausted (HTTP {response.status_code})")
            retry = response.headers.get("Retry-After", "")
            try:
                delay = float(retry)
            except ValueError:
                try:
                    delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                except (ValueError, TypeError):
                    delay = 2 ** attempt
            # Large quota-reset delays should be retried by the next scheduled run.
            if delay > 60:
                raise SourceUnavailable(f"{source}: quota reset exceeds retry window")
            await asyncio.sleep(max(0.1, delay))
        raise AssertionError("unreachable")


def _text(node: ET.Element | None) -> str:
    return "" if node is None else "".join(node.itertext()).strip()


def _abstract(inverted: dict | None) -> str | None:
    if not inverted:
        return None
    positions = {i: token for token, indexes in inverted.items() for i in indexes}
    return " ".join(positions[i] for i in sorted(positions))


def _openalex(item: dict) -> Citation:
    location = item.get("best_oa_location") or {}
    return Citation(source="openalex", source_id=item["id"], doi=canonical_doi(item.get("doi")),
                    title=item.get("display_name") or "Untitled record", year=item.get("publication_year"),
                    authors=[a["author"]["display_name"] for a in item.get("authorships", [])],
                    abstract=_abstract(item.get("abstract_inverted_index")),
                    url=item.get("doi") or item.get("id"),
                    full_text_url=location.get("pdf_url") or location.get("landing_page_url"),
                    license=location.get("license"), version=location.get("version"),
                    is_preprint=item.get("type") == "preprint", updated_at=item.get("updated_date"),
                    status="retracted" if item.get("is_retracted") else "active")


def _crossref(item: dict, updates: dict) -> Citation:
    doi = canonical_doi(item.get("DOI"))
    related = []
    for relationship in ("is-preprint-of", "has-preprint"):
        related.extend(canonical_doi(r.get("id")) for r in item.get("relation", {}).get(relationship, [])
                       if canonical_doi(r.get("id")))
    status = "active"
    relationships = []
    for update in item.get("update-to", []):
        kind = {"retraction": "retracted", "correction": "corrected",
                "expression-of-concern": "concern", "withdrawal": "withdrawn"}.get(update.get("type"))
        target = canonical_doi(update.get("DOI"))
        if kind and target:
            relationships.append(PublicationUpdate(relation=update["type"], direction="updates",
                                                    status=kind, target_doi=target))
            updates[target] = preserve_status(updates.get(target, "active"), kind)
            if target == doi:
                status = preserve_status(status, kind)
    dates = item.get("published", item.get("issued", {})).get("date-parts", [[]])
    return Citation(source="crossref", source_id=doi or str(item.get("URL", "")), doi=doi,
                    title=(item.get("title") or ["Untitled record"])[0],
                    authors=[" ".join(filter(None, (a.get("given"), a.get("family"))))
                             for a in item.get("author", [])], year=dates[0][0] if dates[0] else None,
                    url=item.get("URL"), abstract=item.get("abstract"),
                    is_preprint=item.get("type") == "posted-content", related_dois=related,
                    updated_at=item.get("indexed", {}).get("date-time"), status=status,
                    update_relations=relationships)


def _pubmed(xml: str) -> list[Citation]:
    root = ET.fromstring(xml)
    result = []
    for article in root.findall("PubmedArticle"):
        citation = article.find("MedlineCitation")
        if citation is None:
            continue
        pmid = _text(citation.find("PMID"))
        ids = article.findall("PubmedData/ArticleIdList/ArticleId")
        doi = next((canonical_doi(_text(i)) for i in ids if i.get("IdType") == "doi"), None)
        pmcid = next((_text(i) for i in ids if i.get("IdType") == "pmc"), None)
        status = "active"
        relationships = []
        for correction in citation.findall("CommentsCorrectionsList/CommentsCorrections"):
            kind = {"RetractionIn": "retracted", "ExpressionOfConcernIn": "concern",
                    "ErratumIn": "corrected", "RetractedandRepublishedIn": "retracted",
                    "CorrectedandRepublishedIn": "corrected", "UpdateIn": "corrected"}.get(
                        correction.get("RefType"))
            if kind:
                status = preserve_status(status, kind)
            relation = correction.get("RefType", "")
            outgoing = {"RetractionOf": "retracted", "ExpressionOfConcernFor": "concern",
                        "ErratumFor": "corrected", "RetractedandRepublishedFrom": "retracted",
                        "CorrectedandRepublishedFrom": "corrected", "UpdateOf": "corrected"}.get(relation)
            target = _text(correction.find("PMID"))
            if target and (kind or outgoing):
                relationships.append(PublicationUpdate(relation=relation, direction="updated_by" if kind else "updates",
                    status=kind or outgoing, target_pmid=target))
        pubtypes = [_text(t).lower() for t in citation.findall("Article/PublicationTypeList/PublicationType")]
        if "retracted publication" in pubtypes:
            status = "retracted"
        year_text = _text(citation.find("Article/Journal/JournalIssue/PubDate/Year"))
        revised = citation.find("DateRevised")
        revised_parts = [_text(revised.find(k)) for k in ("Year", "Month", "Day")] if revised is not None else []
        result.append(Citation(source="pubmed", source_id=pmid, doi=doi, pmid=pmid, pmcid=pmcid,
                               title=_text(citation.find("Article/ArticleTitle")),
                               authors=[" ".join(filter(None, (_text(a.find("ForeName")),
                                                                _text(a.find("LastName")))))
                                        for a in citation.findall("Article/AuthorList/Author")],
                               abstract="\n".join(_text(t) for t in citation.findall("Article/Abstract/AbstractText")),
                               year=int(year_text) if year_text.isdigit() else None,
                               url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", status=status,
                               is_preprint="preprint" in pubtypes, update_relations=relationships,
                               updated_at="-".join(revised_parts) if all(revised_parts) and revised_parts else None))
    return result


def _europepmc(item: dict) -> Citation:
    oa_urls = item.get("fullTextUrlList", {}).get("fullTextUrl", [])
    oa = next((u for u in oa_urls if u.get("availabilityCode") == "OA"), {})
    return Citation(source="europepmc", source_id=f"{item.get('source')}:{item['id']}",
                    pmid=str(item["id"]) if item.get("source") == "MED" else None,
                    pmcid=item.get("pmcid") or (str(item["id"]) if str(item["id"]).startswith("PMC") else
                                               f"PMC{item['id']}" if item.get("source") == "PMC" and str(item["id"]).isdigit() else None),
                    doi=canonical_doi(item.get("doi")), title=item.get("title") or "Untitled record",
                    authors=[a.get("fullName", "") for a in item.get("authorList", {}).get("author", [])],
                    abstract=item.get("abstractText"), year=int(item["pubYear"]) if item.get("pubYear") else None,
                    url=f"https://europepmc.org/article/{item.get('source', 'MED')}/{item['id']}",
                    full_text_url=oa.get("url"), license=item.get("license"),
                    version=str(item.get("versionNumber", "")) or None,
                    is_preprint=item.get("source") == "PPR",
                    status="retracted" if item.get("isRetracted") == "Y" else "active",
                    updated_at=None)


def _scopus(item: dict) -> Citation:
    return Citation(source="scopus", source_id=item.get("eid") or item.get("dc:identifier", ""),
                    doi=canonical_doi(item.get("prism:doi")), title=item.get("dc:title") or "Untitled record",
                    authors=[item["dc:creator"]] if item.get("dc:creator") else [],
                    year=int(item["prism:coverDate"][:4]) if item.get("prism:coverDate") else None,
                    abstract=item.get("dc:description"), url=item.get("prism:url"))


async def _page(source: str, query: str, state: dict, client: ProviderClient,
                since: datetime | None, updates: dict) -> tuple[list[Citation], Any, bool, str]:
    token = state.get("cursor")
    credentials = client.credentials
    date = since.date().isoformat() if since else None
    if source == "openalex":
        params = {"search": query, "per-page": 200, "cursor": token or "*"}
        headers = {}
        if credentials.get("openalex_api_key"):
            headers["Authorization"] = f"Bearer {credentials['openalex_api_key']}"
        semantics = "full topic reconciliation (no premium updated-date permission configured)"
        if date and credentials.get("openalex_updated_filter") == "true":
            params["filter"] = f"from_updated_date:{date}"
            semantics = "provider metadata updated date"
        data = (await client.get(source, "https://api.openalex.org/works", params=params, headers=headers)).json()
        cursor = data.get("meta", {}).get("next_cursor")
        items = data.get("results", [])
        return [_openalex(i) for i in items], cursor, len(items) < 200 or not bool(cursor), semantics
    if source == "crossref":
        params = {"query": query, "rows": 200, "cursor": token or "*"}
        if date:
            params["filter"] = f"from-index-date:{date}"
        if credentials.get("contact_email"):
            params["mailto"] = credentials["contact_email"]
        data = (await client.get(source, "https://api.crossref.org/works", params=params)).json()["message"]
        items = data.get("items", [])
        notices = set(state.get("notice_dois", []))
        for item in items:
            item_doi = canonical_doi(item.get("DOI"))
            if item_doi and any(canonical_doi(update.get("DOI")) != item_doi
                                for update in item.get("update-to", []) if canonical_doi(update.get("DOI"))):
                notices.add(item_doi)
        state["notice_dois"] = sorted(notices)
        cursor = data.get("next-cursor")
        done = len(items) < 200 or not cursor or cursor == token
        return [_crossref(i, updates) for i in items], cursor, done, "provider indexed date (all metadata changes)"
    if source == "pubmed":
        offset = int(token or 0)
        params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": 200, "retstart": offset,
                  "tool": "livingmeta"}
        if credentials.get("pubmed_api_key"):
            params["api_key"] = credentials["pubmed_api_key"]
        if credentials.get("contact_email"):
            params["email"] = credentials["contact_email"]
        # Entry date finds late-indexed older articles; full topic reconciliation also finds corrections.
        data = (await client.get(source, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                                 params=params)).json()["esearchresult"]
        if "errorlist" in data or "ERROR" in data:
            raise SourceUnavailable("pubmed: query rejected by provider")
        ids = data.get("idlist", [])
        total = int(data.get("count", 0))
        if not ids:
            return [], None, True, "full topic reconciliation (entries and corrections)"
        fetch = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml", "tool": "livingmeta"}
        if credentials.get("contact_email"):
            fetch["email"] = credentials["contact_email"]
        if credentials.get("pubmed_api_key"):
            fetch["api_key"] = credentials["pubmed_api_key"]
        xml = (await client.get(source, "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                                params=fetch)).text
        if offset + len(ids) >= 10000 and total > 10000:
            raise SourceUnavailable("pubmed: query exceeds 10,000-record ESearch limit; subdivide protocol query")
        return _pubmed(xml), offset + len(ids), offset + len(ids) >= total, "full topic reconciliation (entries and corrections)"
    if source == "europepmc":
        # FIRST_IDATE would miss corrections; full reconciliation is intentional and recorded.
        params = {"query": query, "format": "json", "resultType": "core", "pageSize": 200,
                  "cursorMark": token or "*"}
        data = (await client.get(source, "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                 params=params)).json()
        items = data.get("resultList", {}).get("result", [])
        for item in items:
            state.setdefault("record_dates", {})[f"{item.get('source')}:{item['id']}"] = {
                "first_indexed_at": item.get("firstIndexDate"),
                "first_published_at": item.get("firstPublicationDate"),
                "metadata_updated_at": None,
            }
        cursor = data.get("nextCursorMark")
        done = not items or not cursor or cursor == token or len(items) < 200
        return [_europepmc(i) for i in items], cursor, done, "full topic reconciliation (entries and corrections)"
    if source == "scopus":
        if not credentials.get("scopus_api_key"):
            raise SourceUnavailable("scopus: API key and institution authorization must be configured")
        headers = {"X-ELS-APIKey": credentials["scopus_api_key"]}
        if credentials.get("scopus_insttoken"):
            headers["X-ELS-Insttoken"] = credentials["scopus_insttoken"]
        params = {"query": query, "count": 200, "cursor": token or "*", "view": "STANDARD"}
        data = (await client.get(source, "https://api.elsevier.com/content/search/scopus",
                                 params=params, headers=headers)).json().get("search-results", {})
        items = data.get("entry", [])
        if items and items[0].get("error"):
            if int(data.get("opensearch:totalResults", 0)) == 0:
                return [], None, True, "full topic reconciliation (Scopus publication date is not update date)"
            raise SourceUnavailable("scopus: provider returned an error entry")
        cursor = data.get("cursor", {}).get("@next")
        return [_scopus(i) for i in items], cursor, not cursor or cursor == token or len(items) < 200, \
            "full topic reconciliation (Scopus publication date is not update date)"
    if source == "arxiv":
        offset = int(token or 0)
        params = {"search_query": query, "start": offset, "max_results": 100,
                  "sortBy": "lastUpdatedDate", "sortOrder": "descending"}
        xml = (await client.get(source, "https://export.arxiv.org/api/query", params=params)).text
        root = ET.fromstring(xml)
        ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom",
              "o": "http://a9.com/-/spec/opensearch/1.1/"}
        entries = root.findall("a:entry", ns)
        found = []
        for e in entries:
            updated = _text(e.find("a:updated", ns))
            doi = canonical_doi(_text(e.find("x:doi", ns)))
            found.append(Citation(source="arxiv", source_id=_text(e.find("a:id", ns)), doi=doi,
                                  title=_text(e.find("a:title", ns)), abstract=_text(e.find("a:summary", ns)),
                                  authors=[_text(a.find("a:name", ns)) for a in e.findall("a:author", ns)],
                                  url=_text(e.find("a:id", ns)), is_preprint=True, updated_at=updated,
                                  year=int(_text(e.find("a:published", ns))[:4]) if e.find("a:published", ns) is not None else None))
        total = int(_text(root.find("o:totalResults", ns)) or 0)
        return found, offset + len(entries), offset + len(entries) >= total or not entries, "full topic reconciliation; updated timestamp retained"
    raise SourceUnavailable(f"Unknown source: {source}")


async def discover(protocol: Protocol, since: datetime | None, credentials: dict[str, str],
                   checkpoint: dict | None = None) -> DiscoveryResult:
    """Resume complete pages only; bounded partial runs must not advance metadata freshness."""
    saved = checkpoint or {}
    result = DiscoveryResult(checkpoint={"sources": {}})
    max_pages = min(1000, max(1, int(credentials.get("max_pages", "25"))))
    async with ProviderClient(credentials) as client:
        for source in protocol.enabled_sources:
            query = protocol.queries.get(source)
            if not query:
                result.errors.append(f"{source}: no native protocol query configured")
                result.source_runs.append({"source": source, "status": "unavailable", "complete": False})
                continue
            signature = hashlib.sha256(json.dumps({"source": source, "query": query,
                                                    "since": since.isoformat() if since else None},
                                                   sort_keys=True).encode()).hexdigest()
            old = saved.get("sources", {}).get(source, {})
            state = dict(old) if old.get("query_hash") == signature else {"cursor": None, "citations": []}
            state["query_hash"] = signature
            citations = [Citation.model_validate(c) for c in state.get("citations", [])]
            updates = dict(state.get("status_updates", {}))
            run = {"source": source, "query": query, "query_hash": signature, "query_version": protocol.version,
                   "started_at": datetime.now(timezone.utc).isoformat(), "pages": 0, "complete": False,
                   "status": "partial", "since": since.isoformat() if since else None}
            try:
                if state.get("complete"):
                    run.update(status="completed", complete=True)
                else:
                    for _ in range(max_pages):
                        items, cursor, done, semantics = await _page(source, query, state, client, since, updates)
                        citations.extend(items)
                        state.update(cursor=cursor, complete=done)
                        run.update(pages=run["pages"] + 1, date_semantics=semantics)
                        if done:
                            run.update(status="completed", complete=True)
                            break
                    if not state.get("complete"):
                        result.errors.append(f"{source}: page budget reached; resume the checkpoint")
            except (httpx.HTTPError, SourceUnavailable, ValueError, KeyError, ET.ParseError) as exc:
                # No credentials, provider payload or URLs in persistent error reports.
                message = str(exc) if isinstance(exc, SourceUnavailable) else f"{source}: {type(exc).__name__}"
                result.errors.append(message)
                run.update(status="partial" if citations else "unavailable", error=message)
            state["citations"] = [c.model_dump(mode="json") for c in citations]
            state["status_updates"] = updates
            result.checkpoint["sources"][source] = state
            run.update(record_count=len(citations), status_updates=updates, notice_dois=state.get("notice_dois", []),
                       record_dates=state.get("record_dates", {}),
                       finished_at=datetime.now(timezone.utc).isoformat())
            result.source_runs.append(run)
            result.citations.extend(citations)
    result.citations = reconcile_citations(result.citations)
    for run in result.source_runs:
        for citation in result.citations:
            if citation.doi in run.get("status_updates", {}):
                citation.status = preserve_status(citation.status, run["status_updates"][citation.doi])
    result.complete = bool(result.source_runs) and all(r["complete"] for r in result.source_runs)
    return result


async def check_publication_updates(dois: list[str], credentials: dict[str, str],
                                    checkpoint: dict | None = None) -> DiscoveryResult:
    """Check known DOI correction targets independently of the current topic query."""
    known = sorted({doi for value in dois if (doi := canonical_doi(value))})
    signature = hashlib.sha256(json.dumps(known).encode()).hexdigest()
    old = checkpoint or {}
    state = dict(old) if old.get("query_hash") == signature else {
        "query_hash": signature, "batch": 0, "cursor": None, "citations": [], "status_updates": {}}
    result = DiscoveryResult(checkpoint=state)
    if not known:
        result.complete = True
        return result
    citations = [Citation.model_validate(c) for c in state.get("citations", [])]
    updates = dict(state.get("status_updates", {}))
    batches = [known[i:i + 50] for i in range(0, len(known), 50)]
    max_pages = min(1000, max(1, int(credentials.get("max_pages", "25"))))
    run = {"source": "crossref_known_updates", "query_hash": signature,
           "query_version": "known-doi-updates-v1", "date_semantics": "all correction notices targeting known DOIs",
           "complete": False, "status": "partial", "pages": 0, "known_dois": known}
    try:
        async with ProviderClient(credentials) as client:
            if state.get("complete"):
                run.update(status="completed", complete=True)
            else:
                for _ in range(max_pages):
                    batch = int(state["batch"])
                    if batch >= len(batches):
                        state["complete"] = True
                        run.update(status="completed", complete=True)
                        break
                    # Repeated values of the same native Crossref filter have OR semantics.
                    params = {"filter": ",".join(f"updates:{doi}" for doi in batches[batch]),
                              "rows": 200, "cursor": state.get("cursor") or "*"}
                    if credentials.get("contact_email"):
                        params["mailto"] = credentials["contact_email"]
                    data = (await client.get("crossref", "https://api.crossref.org/works", params=params)).json()["message"]
                    items = data.get("items", [])
                    citations.extend(_crossref(item, updates) for item in items)
                    next_cursor = data.get("next-cursor")
                    run["pages"] += 1
                    done = len(items) < 200 or not next_cursor
                    if done:
                        state.update(batch=batch + 1, cursor=None)
                    else:
                        state["cursor"] = next_cursor
                    if int(state["batch"]) >= len(batches):
                        state["complete"] = True
                        run.update(status="completed", complete=True)
                        break
                if not state.get("complete"):
                    result.errors.append("Known-DOI update-check page budget reached; resume checkpoint")
    except (httpx.HTTPError, SourceUnavailable, ValueError, KeyError) as exc:
        message = str(exc) if isinstance(exc, SourceUnavailable) else f"Known-DOI update check: {type(exc).__name__}"
        result.errors.append(message)
        run["error"] = message
        run["status"] = "partial" if citations else "unavailable"
    state.update(citations=[c.model_dump(mode="json") for c in citations], status_updates=updates)
    run.update(status_updates=updates, record_count=len(citations))
    result.citations = reconcile_citations(citations)
    result.source_runs = [run]
    result.complete = bool(state.get("complete"))
    return result
