import importlib
import sys
from datetime import datetime, timezone

import httpx
import pytest

from livingmeta.discovery import service
from livingmeta.discovery.fulltext import resolve_pmc, resolve_unpaywall
from livingmeta.discovery.identity import canonical_doi, preserve_status, reconcile_citations
from livingmeta.domain import Citation, Protocol


@pytest.fixture
def install_transport(monkeypatch):
    original = service.ProviderClient
    monkeypatch.setattr(original, "intervals", dict.fromkeys(original.intervals, 0))
    original._next.clear()
    original._locks.clear()

    def install(handler):
        transport = httpx.MockTransport(handler)
        monkeypatch.setattr(service, "ProviderClient", lambda credentials: original(credentials, transport))
        monkeypatch.setattr("livingmeta.discovery.fulltext.ProviderClient",
                            lambda credentials: original(credentials, transport))
    return install


def test_canonical_doi_and_status():
    assert canonical_doi("https://doi.org/10.1234/ABC") == "10.1234/abc"
    assert canonical_doi("doi: 10.1234/ABC") == "10.1234/abc"
    assert canonical_doi("not a DOI") is None
    assert preserve_status("retracted", "active") == "retracted"


def test_explicit_versions_merge_without_fuzzy_titles():
    preprint = Citation(source="crossref", source_id="p", doi="10.1234/preprint", title="Same title",
                        is_preprint=True, related_dois=["10.1234/published"], status="withdrawn")
    journal = Citation(source="crossref", source_id="j", doi="10.1234/published", title="Same title")
    other = Citation(source="openalex", source_id="other", title="Same title")
    result = reconcile_citations([preprint, journal, other])
    assert len(result) == 2
    assert result[0].doi == "10.1234/published"
    assert result[0].status == "active"  # A separate preprint's withdrawal is not inherited.


@pytest.mark.asyncio
async def test_checkpoint_resume_and_native_query(install_transport):
    requests = []

    def handler(request):
        requests.append(request)
        cursor = request.url.params.get("cursor")
        count = 200 if cursor == "*" else 1
        return httpx.Response(200, json={"meta": {"next_cursor": "next" if cursor == "*" else None},
                                         "results": [{"id": f"W{cursor}-{i}", "display_name": "Experiment"}
                                                     for i in range(count)]})
    install_transport(handler)
    protocol = Protocol(enabled_sources=["openalex"], queries={"openalex": '"native expression"'})
    first = await service.discover(protocol, datetime(2026, 1, 1, tzinfo=timezone.utc), {"max_pages": "1"})
    assert first.complete is False
    assert len(first.citations) == 200
    assert first.source_runs[0]["status"] == "partial"
    second = await service.discover(protocol, datetime(2026, 1, 1, tzinfo=timezone.utc),
                                    {"max_pages": "1"}, first.checkpoint)
    assert second.complete is True
    assert len(second.citations) == 201
    assert requests[0].url.params["search"] == '"native expression"'
    assert "filter" not in requests[0].url.params
    assert "full topic reconciliation" in second.source_runs[0]["date_semantics"]


@pytest.mark.asyncio
async def test_missing_scopus_auth_is_not_success(install_transport):
    install_transport(lambda request: pytest.fail("Unauthenticated Scopus must not be called"))
    result = await service.discover(Protocol(enabled_sources=["scopus"]), None, {})
    assert not result.complete
    assert result.source_runs[0]["status"] == "unavailable"
    assert "institution" in result.errors[0]


@pytest.mark.asyncio
async def test_crossref_update_date_and_retraction_target(install_transport):
    def handler(request):
        assert request.url.params["filter"].startswith("from-index-date:2026-01-01")
        return httpx.Response(200, json={"message": {"items": [
            {"DOI": "10.1234/original", "title": ["Original"], "published": {"date-parts": [[2020]]}},
            {"DOI": "10.1234/notice", "title": ["Retraction"],
             "update-to": [{"DOI": "10.1234/original", "type": "retraction"}]}]}})
    install_transport(handler)
    result = await service.discover(Protocol(enabled_sources=["crossref"]),
                                     datetime(2026, 1, 1, tzinfo=timezone.utc), {})
    assert result.complete
    assert next(c for c in result.citations if c.doi == "10.1234/original").status == "retracted"
    assert result.source_runs[0]["status_updates"] == {"10.1234/original": "retracted"}


@pytest.mark.asyncio
async def test_bounded_rate_retries_do_not_expose_secret(install_transport, monkeypatch):
    calls = []

    async def no_sleep(delay):
        pass
    monkeypatch.setattr(service.asyncio, "sleep", no_sleep)
    install_transport(lambda request: calls.append(request) or httpx.Response(429, headers={"Retry-After": "0"}))
    result = await service.discover(Protocol(enabled_sources=["crossref"]), None,
                                     {"contact_email": "private@example.org"})
    assert len(calls) == 4
    assert not result.complete
    assert "private@example.org" not in str(result.errors)


@pytest.mark.asyncio
async def test_pubmed_xml_retains_retraction(install_transport):
    xml = '''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID><Article>
    <ArticleTitle>A chemical experiment</ArticleTitle><PublicationTypeList>
    <PublicationType>Journal Article</PublicationType></PublicationTypeList></Article>
    <CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><PMID>456</PMID>
    </CommentsCorrections></CommentsCorrectionsList></MedlineCitation><PubmedData><ArticleIdList>
    <ArticleId IdType="doi">10.1234/abc</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>'''

    def handler(request):
        if "esearch" in request.url.path:
            return httpx.Response(200, json={"esearchresult": {"idlist": ["123"], "count": "1"}})
        return httpx.Response(200, text=xml)
    install_transport(handler)
    result = await service.discover(Protocol(enabled_sources=["pubmed"]), None, {})
    assert result.complete and result.citations[0].status == "retracted"


@pytest.mark.asyncio
async def test_current_pmc_media_versions(install_transport):
    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='''<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
            <CommonPrefixes><Prefix>PMC123.2/</Prefix></CommonPrefixes></ListBucketResult>''')
        assert request.url.path == "/PMC123.2/PMC123.2.json"
        return httpx.Response(200, json={"doi": "10.1234/test", "license_code": "CC-BY",
                                         "is_retracted": "yes", "is_manuscript": "no",
                                         "pdf_url": "https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/a.pdf?md5=x",
                                         "media_urls": ["https://pmc-oa-opendata.s3.amazonaws.com/PMC123.2/fig1.jpg"]})
    install_transport(handler)
    result = await resolve_pmc("PMC123", {})
    assert result.status == "available"
    assert result.publication_status == "retracted"
    assert {location.kind for location in result.locations} == {"pdf", "media_or_supplement"}
    assert all(location.version == "PMC123.2" for location in result.locations)


@pytest.mark.asyncio
async def test_unpaywall_requires_email_and_resolves_doi_only(install_transport):
    install_transport(lambda request: httpx.Response(200, json={"oa_locations": []}))
    result = await resolve_unpaywall("10.1234/test", {})
    assert result.status == "not_available" and result.limitations
    result = await resolve_unpaywall("10.1234/test", {"contact_email": "researcher@example.org"})
    assert not result.locations


def test_discovery_has_no_ai_dependency():
    for module in ("livingmeta.discovery.service", "livingmeta.discovery.fulltext"):
        source = open(importlib.import_module(module).__file__, encoding="utf-8").read()
        assert "from openai" not in source and "import agents" not in source
    assert "livingmeta.discovery.service" in sys.modules


@pytest.mark.asyncio
async def test_europepmc_native_expression_and_open_access_separation(install_transport):
    expression = 'Pickering AND TITLE_ABS:"cellulose nanocrystals"'

    def handler(request):
        assert request.url.params["query"] == expression
        assert request.url.params["cursorMark"] == "*"
        return httpx.Response(200, json={"resultList": {"result": [
            {"source": "MED", "id": "123", "doi": "10.1234/epmc", "title": "Closed full text",
             "isOpenAccess": "N", "pubYear": "2024", "firstIndexDate": "2026-09-30"}]},
                                         "nextCursorMark": "next"})
    install_transport(handler)
    result = await service.discover(Protocol(enabled_sources=["europepmc"],
                                             queries={"europepmc": expression}), None, {})
    assert result.complete
    assert len(result.citations) == 1
    assert result.citations[0].full_text_url is None
    assert result.citations[0].updated_at is None
    assert result.source_runs[0]["record_dates"]["MED:123"]["first_indexed_at"] == "2026-09-30"
    assert "full topic reconciliation" in result.source_runs[0]["date_semantics"]


@pytest.mark.asyncio
async def test_scopus_native_auth_headers_not_logged(install_transport):
    expression = 'TITLE-ABS-KEY(Pickering AND "cellulose nanocrystals")'

    def handler(request):
        assert request.headers["X-ELS-APIKey"] == "secret-key"
        assert request.headers["X-ELS-Insttoken"] == "secret-institution"
        assert request.url.params["query"] == expression
        assert "apiKey" not in request.url.params
        return httpx.Response(200, json={"search-results": {"entry": [
            {"eid": "2-s2.0-test", "dc:title": "Chemistry experiment", "prism:doi": "10.1234/Scopus",
             "prism:coverDate": "2024-01-01"}], "cursor": {"@next": "last"}}})
    install_transport(handler)
    result = await service.discover(Protocol(enabled_sources=["scopus"], queries={"scopus": expression}),
                                    None, {"scopus_api_key": "secret-key", "scopus_insttoken": "secret-institution"})
    assert result.complete and result.citations[0].doi == "10.1234/scopus"
    assert "secret-key" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_arxiv_preprint_and_published_doi_retained(install_transport):
    def handler(request):
        assert request.url.params["sortBy"] == "lastUpdatedDate"
        return httpx.Response(200, text='''<feed xmlns="http://www.w3.org/2005/Atom"
        xmlns:arxiv="http://arxiv.org/schemas/atom"
        xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
        <opensearch:totalResults>1</opensearch:totalResults><entry>
        <id>https://arxiv.org/abs/2601.12345v2</id><title>Pickering experiment</title>
        <published>2026-01-01T00:00:00Z</published><updated>2026-09-01T00:00:00Z</updated>
        <summary>Experiment summary</summary><author><name>A. Researcher</name></author>
        <arxiv:doi>10.1234/journal</arxiv:doi></entry></feed>''')
    install_transport(handler)
    result = await service.discover(Protocol(enabled_sources=["arxiv"]), None, {})
    assert result.complete
    assert result.citations[0].is_preprint
    assert result.citations[0].doi == "10.1234/journal"
    assert result.citations[0].updated_at == "2026-09-01T00:00:00Z"


@pytest.mark.asyncio
async def test_known_doi_updates_are_searched_without_topic_expression(install_transport):
    def handler(request):
        assert request.url.params["filter"] == "updates:10.1234/known"
        assert "query" not in request.url.params
        return httpx.Response(200, json={"message": {"items": [{"DOI": "10.1234/notice", "title": ["Correction"],
                                                                 "update-to": [{"DOI": "10.1234/known", "type": "correction"}]}]}})
    install_transport(handler)
    result = await service.check_publication_updates(["https://doi.org/10.1234/KNOWN"], {})
    assert result.complete
    assert result.source_runs[0]["status_updates"] == {"10.1234/known": "corrected"}
