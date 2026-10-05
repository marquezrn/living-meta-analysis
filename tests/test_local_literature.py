"""Keyless discovery and original-asset acquisition use mocked public providers only."""

import hashlib
from types import SimpleNamespace
from datetime import datetime, timezone

import httpx
import pytest
from pydantic import ValidationError

from livingmeta.discovery import fulltext, service
from livingmeta.discovery.fulltext import AccessLocation, AccessResolution, PMC_BUCKET, download_asset, inspect_jats
from livingmeta.discovery.identity import reconcile_citations
from livingmeta.domain import Citation, Evidence, Protocol
from livingmeta.local import literature
from livingmeta.local.workspace import atomic_json, load_dataset, load_manifest

JATS = b'''<article xmlns:xlink="http://www.w3.org/1999/xlink"><front><article-meta>
<article-id pub-id-type="doi">10.1234/experiment</article-id><title-group><article-title>Pickering experiment</article-title></title-group>
<abstract id="abstract"><p id="summary">A nanocellulose emulsion experiment.</p></abstract></article-meta></front>
<body><sec id="results"><title>Results</title><p id="p1">Mean diameter 7 um; SD 1 um; 3 independent preparations.</p>
<table-wrap id="table1"><caption><p>Measured diameters</p></caption><table><tr><th>Sample</th><th>Diameter (um)</th></tr>
<tr><td>A</td><td id="value1">7</td></tr></table></table-wrap><fig id="figure1"><caption><p>Independent measured droplets.</p></caption>
<graphic xlink:href="figure1.png"/></fig></sec></body></article>'''


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "private-workspace"
    root.mkdir()
    atomic_json(root / "manifest.json", {"schema_version": 2, "id": "synthetic", "status": "ready",
        "created_at": "2026-01-01T00:00:00+00:00", "protocol": Protocol(enabled_sources=["pubmed"]).model_dump(),
        "documents": []})
    atomic_json(root / "dataset.json", {"schema_version": 2, "experiments": [], "publications": [], "limitations": []})
    return root


@pytest.fixture
def transport(monkeypatch):
    original = service.ProviderClient
    monkeypatch.setattr(original, "intervals", dict.fromkeys(original.intervals, 0))
    original._next.clear()
    original._locks.clear()

    def install(handler):
        def factory(credentials):
            return original(credentials, httpx.MockTransport(handler))
        monkeypatch.setattr(service, "ProviderClient", factory)
        monkeypatch.setattr(fulltext, "ProviderClient", factory)
        monkeypatch.setattr(literature, "ProviderClient", factory)
    return install


def test_default_protocol_uses_only_keyless_sources():
    assert Protocol().enabled_sources == ["pubmed", "europepmc", "crossref"]


def test_identifier_reconciliation_keeps_pmc_identifier_without_a_doi():
    pubmed = Citation(source="pubmed", source_id="123", pmid="123", pmcid="PMC123", title="Experiment")
    europe = Citation(source="europepmc", source_id="MED:123", pmid="123", doi="10.1234/experiment", title="Experiment")
    result = reconcile_citations([pubmed, europe])
    assert len(result) == 1
    assert result[0].doi == "10.1234/experiment" and result[0].pmcid == "PMC123"


def test_identifiers_join_through_exact_aliases_without_losing_a_correction():
    citations = [Citation(source="crossref", source_id="10.1234/experiment", doi="10.1234/experiment", title="Experiment"),
        Citation(source="pubmed", source_id="123", pmid="123", pmcid="PMC123", title="Experiment", status="corrected"),
        Citation(source="europepmc", source_id="MED:123", pmid="123", doi="10.1234/experiment", title="Experiment")]
    result = reconcile_citations(citations)
    assert len(result) == 1 and result[0].pmcid == "PMC123" and result[0].status == "corrected"


def test_publication_merge_collapses_identifier_bridges_and_preserves_review_state():
    existing = [{"id": "first", "source": "pubmed", "source_id": "123", "pmid": "123", "title": "Experiment",
        "status": "corrected", "state": "completed"}, {"id": "second", "source": "crossref", "source_id": "10.1234/experiment",
        "doi": "10.1234/experiment", "title": "Experiment", "status": "active", "state": "pending_extraction"}]
    incoming = Citation(source="europepmc", source_id="MED:123", pmid="123", pmcid="PMC123",
                        doi="10.1234/experiment", title="Experiment")
    merged = literature._merge_publications(existing, [incoming])
    assert len(merged) == 1 and merged[0]["id"] == "first" and merged[0]["id_aliases"] == ["second"]
    assert merged[0]["status"] == "corrected" and merged[0]["state"] == "completed"
    assert literature._identifiers(merged[0]) >= {"pmid:123", "pmcid:PMC123", "doi:10.1234/experiment"}


def test_pubmed_update_directions_do_not_retract_the_notice_itself():
    xml = '''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>999</PMID><Article>
    <ArticleTitle>Retraction notice</ArticleTitle></Article><CommentsCorrectionsList>
    <CommentsCorrections RefType="RetractionOf"><PMID>123</PMID></CommentsCorrections></CommentsCorrectionsList>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>'''
    notice = service._pubmed(xml)[0]
    assert notice.status == "active"
    assert notice.update_relations[0].direction == "updates" and notice.update_relations[0].target_pmid == "123"
    records = literature._merge_publications([{"id": "pmid:123", "source": "pubmed", "source_id": "123",
        "pmid": "123", "title": "Original", "status": "active"}], [notice])
    assert next(r for r in records if r.get("pmid") == "123")["status"] == "retracted"
    assert next(r for r in records if r.get("pmid") == "999")["status"] == "active"
    assert next(r for r in records if r.get("pmid") == "999")["state"] == "publication_notice"


def test_jats_inventory_preserves_structure_assets_and_actual_source_locations(tmp_path):
    path = tmp_path / "source.xml"
    path.write_bytes(JATS)
    (tmp_path / "figure1.png").write_bytes(b"synthetic image fixture")
    result = inspect_jats(path, tmp_path / "artifacts")
    assert result["page_count"] == 0 and all(unit["page"] is None for unit in result["pages"])
    assert result["document_hash"] == hashlib.sha256(JATS).hexdigest()
    assert result["elements"]["id:value1"]["text"] == "7"
    body = result["pages"][1]
    assert body["tables"][0]["rows"] == [["Sample", "Diameter (um)"], ["A", "7"]]
    assert body["figures"][0]["asset_refs"] == ["figure1.png"]
    assert len(body["figures"][0]["asset_paths"]) == 1
    assert (tmp_path / "artifacts/jats-inventory.json").exists()


def test_jats_table_only_values_are_available_to_the_source_unit_verifier(tmp_path):
    path = tmp_path / "table-only.xml"
    path.write_bytes(JATS.replace(b'>7</td>', b'>123.456</td>'))
    result = inspect_jats(path)
    assert result["elements"]["id:value1"]["text"] == "123.456"
    assert "123.456" in result["pages"][1]["text"]


def test_jats_inventories_floated_tables_and_original_figures(tmp_path):
    path = tmp_path / "floats.xml"
    path.write_bytes(b'''<article xmlns:xlink="http://www.w3.org/1999/xlink"><body><p>Narrative</p></body>
    <floats-group><table-wrap id="t1"><table><tr><td>42</td></tr></table></table-wrap>
    <fig id="f1"><graphic xlink:href="original.png"/></fig></floats-group></article>''')
    (tmp_path / "original.png").write_bytes(b"original figure")
    result = inspect_jats(path)
    assert result["pages"][1]["tables"][0]["rows"] == [["42"]]
    assert result["pages"][1]["figures"][0]["asset_paths"] == [str(tmp_path / "original.png")]


def test_jats_duplicate_ids_and_media_symlinks_do_not_create_false_locations(tmp_path):
    path = tmp_path / "source.xml"
    path.write_bytes(JATS.replace(b'id="value1"', b'id="p1"'))
    with pytest.raises(ValueError, match="unique"):
        inspect_jats(path)
    outside = tmp_path.parent / "outside-figure.png"
    outside.write_bytes(b"outside")
    (tmp_path / "figure1.png").symlink_to(outside)
    path.write_bytes(JATS.replace(b"figure1.png", b"figure1"))
    assert inspect_jats(path)["pages"][1]["figures"][0]["asset_paths"] == []


def test_xml_and_media_evidence_cannot_invent_pdf_pages():
    valid = Evidence(document_hash="a" * 64, source_format="xml", xml_element="id:p1",
                     source_type="text", locator="Results paragraph", excerpt="7 um")
    assert valid.page is None
    for fields in [{"source_format": "xml", "xml_element": "id:p1", "page": 1},
                   {"source_format": "xml"}, {"source_format": "media", "asset_path": "../private.png"}, {}]:
        with pytest.raises(ValidationError):
            Evidence(document_hash="a" * 64, source_type="text", locator="Source", excerpt="7", **fields)


def test_jats_rejects_entity_declarations_and_unsafe_figure_paths(tmp_path):
    path = tmp_path / "source.xml"
    path.write_bytes(b'<!DOCTYPE article [<!ENTITY x "private">]><article/>')
    with pytest.raises(ValueError, match="entities"):
        inspect_jats(path)
    path.write_bytes(JATS.replace(b"figure1.png", b"../outside.png"))
    result = inspect_jats(path)
    assert not result["pages"][1]["figures"][0]["asset_refs"]


@pytest.mark.asyncio
async def test_asset_download_is_bounded_verified_and_resumable(tmp_path, transport):
    content = b"%PDF-synthetic fixture"
    md5 = hashlib.md5(content, usedforsecurity=False).hexdigest()
    calls = []
    transport(lambda request: calls.append(request) or httpx.Response(200, content=content))
    location = AccessLocation(url=f"{PMC_BUCKET}/PMC123.1/source.pdf?md5={md5}", kind="pdf", provider="pmc_aws",
                              version="PMC123.1", md5=md5, license="CC-BY")
    first = await download_asset(location, tmp_path, {})
    second = await download_asset(location, tmp_path, {})
    assert first["sha256"] == second["sha256"] == hashlib.sha256(content).hexdigest()
    assert len(calls) == 1
    with pytest.raises(ValueError, match="checksum"):
        await download_asset(location.model_copy(update={"md5": "0" * 32}), tmp_path / "bad", {})
    with pytest.raises(service.SourceUnavailable, match="size"):
        await download_asset(location, tmp_path / "too-small", {}, max_bytes=5)
    with pytest.raises(ValueError, match="filename"):
        await download_asset(location.model_copy(update={"url": f"{PMC_BUCKET}/PMC123.1/%2E%2E%2Fprivate.pdf"}), tmp_path, {})


@pytest.mark.asyncio
async def test_metadata_outage_does_not_advance_freshness_or_lose_extracted_data(workspace, transport):
    manifest = load_manifest(workspace)
    manifest["last_metadata_check"] = "2026-01-01T00:00:00+00:00"
    manifest["literature"] = {"last_metadata_check": manifest["last_metadata_check"]}
    atomic_json(workspace / "manifest.json", manifest)
    dataset = load_dataset(workspace)
    dataset["experiments"] = [{"id": "retained-experiment", "measurements": []}]
    atomic_json(workspace / "dataset.json", dataset)
    transport(lambda request: httpx.Response(503, headers={"Retry-After": "1000"}))
    report = await literature.discover_workspace(workspace, None)
    assert report["status"] == "partial" and report["openai_calls"] == 0
    assert load_manifest(workspace)["last_metadata_check"] == "2026-01-01T00:00:00+00:00"
    assert load_dataset(workspace)["experiments"][0]["id"] == "retained-experiment"


@pytest.mark.asyncio
async def test_keyless_monitor_retries_429_and_records_pending_without_extraction(workspace, transport, monkeypatch):
    calls = []

    async def immediate_sleep(delay):
        pass
    monkeypatch.setattr(service.asyncio, "sleep", immediate_sleep)

    def handler(request):
        calls.append(request)
        assert "api_key" not in request.url.params
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if "esearch" in request.url.path:
            return httpx.Response(200, json={"esearchresult": {"idlist": ["123"], "count": "1"}})
        assert request.url.params["email"] == "researcher@example.org"
        return httpx.Response(200, text='''<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>123</PMID>
        <Article><ArticleTitle>Pickering experiment</ArticleTitle></Article></MedlineCitation><PubmedData><ArticleIdList>
        <ArticleId IdType="pmc">PMC123</ArticleId></ArticleIdList></PubmedData></PubmedArticle></PubmedArticleSet>''')
    transport(handler)
    report = await literature.discover_workspace(workspace, "researcher@example.org")
    assert report["complete"] and report["pending_extraction"] == 1 and report["openai_calls"] == 0
    assert not load_manifest(workspace)["documents"]
    assert load_dataset(workspace)["publications"][0]["pmcid"] == "PMC123"
    assert load_manifest(workspace)["synthesis_stale"]


@pytest.mark.asyncio
async def test_pmc_bundle_retains_jats_original_media_and_pdf_as_one_primary_document(workspace, transport):
    assets = {"source.xml": JATS, "source.pdf": b"%PDF-synthetic", "figure1.png": b"original image bytes"}
    metadata = {"doi": "10.1234/experiment", "license_code": "CC-BY", "is_manuscript": "no",
        "xml_url": f"{PMC_BUCKET}/PMC123.1/source.xml?md5={hashlib.md5(JATS, usedforsecurity=False).hexdigest()}",
        "pdf_url": f"{PMC_BUCKET}/PMC123.1/source.pdf?md5={hashlib.md5(assets['source.pdf'], usedforsecurity=False).hexdigest()}",
        "media_urls": [f"{PMC_BUCKET}/PMC123.1/figure1.png?md5={hashlib.md5(assets['figure1.png'], usedforsecurity=False).hexdigest()}"]}

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><CommonPrefixes><Prefix>PMC123.1/</Prefix></CommonPrefixes></ListBucketResult>')
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json=metadata)
        return httpx.Response(200, content=assets[request.url.path.rsplit("/", 1)[-1]])
    transport(handler)
    citation = Citation(source="pubmed", source_id="123", pmid="123", pmcid="PMC123", doi="10.1234/experiment", title="Experiment")
    bundle = await literature._acquire(workspace, citation, {}, None)
    assert bundle["status"] == "downloaded" and bundle["document"]["source_format"] == "xml"
    assert {asset["kind"] for asset in bundle["assets"]} == {"xml", "pdf", "media_or_supplement"}
    xml = workspace / bundle["document"]["source_path"]
    assert xml.read_bytes() == JATS and (xml.parent / "figure1.png").read_bytes() == assets["figure1.png"]
    assert (xml.parent / "jats-inventory.json").is_file()


@pytest.mark.asyncio
async def test_changed_original_image_is_linked_to_new_immutable_bundle_even_when_xml_is_unchanged(workspace, transport):
    assets = {"source.xml": JATS, "figure1.png": b"original figure"}
    download_calls = []

    def metadata():
        return {"license_code": "CC-BY", "is_manuscript": "no",
            "xml_url": f"{PMC_BUCKET}/PMC123.1/source.xml?md5={hashlib.md5(JATS, usedforsecurity=False).hexdigest()}",
            "media_urls": [f"{PMC_BUCKET}/PMC123.1/figure1.png?md5={hashlib.md5(assets['figure1.png'], usedforsecurity=False).hexdigest()}"]}

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><CommonPrefixes><Prefix>PMC123.1/</Prefix></CommonPrefixes></ListBucketResult>')
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json=metadata())
        filename = request.url.path.rsplit("/", 1)[-1]
        download_calls.append(filename)
        return httpx.Response(200, content=assets[filename])
    transport(handler)
    citation = Citation(source="pubmed", source_id="123", pmid="123", pmcid="PMC123", title="Experiment")
    first = await literature._acquire(workspace, citation, {}, None)
    assets["figure1.png"] = b"updated corrected figure"
    second = await literature._acquire(workspace, citation, {}, first)
    assert first["document"]["sha256"] == second["document"]["sha256"]
    assert first["document"]["bundle_hash"] != second["document"]["bundle_hash"]
    assert first["document"]["source_path"] != second["document"]["source_path"]
    current_xml = workspace / second["document"]["source_path"]
    figure = inspect_jats(current_xml)["pages"][1]["figures"][0]["asset_paths"][0]
    assert (current_xml.parent / "figure1.png").read_bytes() == b"updated corrected figure"
    assert figure == str(current_xml.parent / "figure1.png")
    assert (workspace / first["document"]["source_path"]).parent.joinpath("figure1.png").read_bytes() == b"original figure"
    assert download_calls.count("source.xml") == 1


@pytest.mark.asyncio
async def test_removed_selected_version_requires_reassessment_and_never_silently_substitutes_another(workspace, monkeypatch):
    async def resolve(*args):
        return AccessResolution(pmcid="PMC123", version_metadata={"PMC123.2": {"is_manuscript": "no", "license_code": "CC-BY"}})
    monkeypatch.setattr(literature, "resolve_pmc", resolve)
    citation = Citation(source="pubmed", source_id="123", pmcid="PMC123", title="Experiment")
    result = await literature._acquire(workspace, citation, {}, {"version": "PMC123.1"})
    assert result["status"] == "source_unavailable" and result["versions"] == ["PMC123.2"]
    assert "document" not in result


@pytest.mark.asyncio
async def test_partial_bundle_download_reuses_verified_assets_on_retry(workspace, transport):
    calls = []
    broken = True
    image = b"original figure"
    metadata = {"license_code": "CC-BY", "is_manuscript": "no",
        "xml_url": f"{PMC_BUCKET}/PMC123.1/source.xml?md5={hashlib.md5(JATS, usedforsecurity=False).hexdigest()}",
        "media_urls": [f"{PMC_BUCKET}/PMC123.1/figure1.png?md5={hashlib.md5(image, usedforsecurity=False).hexdigest()}"]}

    def handler(request):
        if request.url.path == "/":
            return httpx.Response(200, text='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><CommonPrefixes><Prefix>PMC123.1/</Prefix></CommonPrefixes></ListBucketResult>')
        if request.url.path.endswith(".json"):
            return httpx.Response(200, json=metadata)
        filename = request.url.path.rsplit("/", 1)[-1]
        calls.append(filename)
        if filename.endswith(".png") and broken:
            return httpx.Response(503, headers={"Retry-After": "1000"})
        return httpx.Response(200, content=JATS if filename.endswith(".xml") else image)
    transport(handler)
    citation = Citation(source="pubmed", source_id="123", pmcid="PMC123", title="Experiment")
    with pytest.raises(service.SourceUnavailable):
        await literature._acquire(workspace, citation, {}, None)
    broken = False
    assert (await literature._acquire(workspace, citation, {}, None))["status"] == "downloaded"
    assert calls.count("source.xml") == 1 and calls.count("figure1.png") == 2


@pytest.mark.asyncio
async def test_removed_pmc_source_invalidates_measurements_but_is_not_reported_as_retracted(workspace, monkeypatch):
    dataset = load_dataset(workspace)
    dataset["publications"] = [{"id": "pmid:123", "source": "pubmed", "source_id": "123", "pmid": "123", "pmcid": "PMC123",
        "doi": "10.1234/experiment", "title": "Experiment", "status": "active", "state": "completed"}]
    dataset["experiments"] = [{"doi": "10.1234/experiment", "measurements": [{"status": "accepted", "value": 7, "evidence": []}]}]
    atomic_json(workspace / "dataset.json", dataset)
    manifest = load_manifest(workspace)
    manifest["documents"] = [{"publication_id": "pmid:123", "doi": "10.1234/experiment", "sha256": "a" * 64, "status": "completed"}]
    manifest["literature"] = {"bundles": {"pmid:123": {"pmcid": "PMC123", "version": "PMC123.1", "metadata_hash": "before"}}}
    atomic_json(workspace / "manifest.json", manifest)

    async def empty(*args):
        return service.DiscoveryResult(complete=True)

    async def unavailable(*args):
        return AccessResolution(pmcid="PMC123")
    monkeypatch.setattr(literature, "discover", empty)
    monkeypatch.setattr(literature, "_known_pubmed", empty)
    monkeypatch.setattr(literature, "resolve_pmc", unavailable)
    report = await literature.discover_workspace(workspace, None)
    record = load_dataset(workspace)["publications"][0]
    assert report["complete"] and record["status"] == "active" and record["access_status"] == "source_unavailable"
    assert record["state"] == "needs_review" and load_manifest(workspace)["documents"][0]["status"] == "stale"
    assert load_dataset(workspace)["experiments"][0]["measurements"][0]["status"] == "stale"


@pytest.mark.asyncio
async def test_commit_preserves_extraction_written_while_metadata_request_is_running(workspace, monkeypatch):
    async def discover(*args):
        dataset = load_dataset(workspace)
        dataset["experiments"].append({"id": "concurrent-result", "measurements": [{"value": 7}]})
        atomic_json(workspace / "dataset.json", dataset)
        return service.DiscoveryResult(complete=True, citations=[Citation(source="pubmed", source_id="123", pmid="123", title="Experiment")])

    monkeypatch.setattr(literature, "discover", discover)
    await literature.discover_workspace(workspace, None)
    assert load_dataset(workspace)["experiments"][0]["id"] == "concurrent-result"


@pytest.mark.asyncio
async def test_pubmed_missing_known_record_is_partial_and_not_a_retraction(transport):
    transport(lambda request: httpx.Response(200, text="<PubmedArticleSet/>"))
    result = await literature._known_pubmed(["123"], {})
    assert not result.complete and result.source_runs[0]["status"] == "partial"


@pytest.mark.asyncio
async def test_hosted_access_routes_explicit_pmcid_and_preserves_doi_fallback_during_converter_outage(monkeypatch):
    from livingmeta.acquisition import resolve_publication_access
    calls = []

    async def pmc(identifier, credentials):
        calls.append(identifier)
        return AccessResolution(pmcid=identifier)

    async def unavailable(*args):
        raise service.SourceUnavailable("unavailable")

    async def unpaywall(doi, credentials):
        calls.append(doi)
        return AccessResolution(doi=doi)
    monkeypatch.setattr(fulltext, "resolve_pmc", pmc)
    monkeypatch.setattr(fulltext, "convert_pmc_ids", unavailable)
    monkeypatch.setattr(fulltext, "resolve_unpaywall", unpaywall)
    settings = SimpleNamespace(contact_email="researcher@example.org")
    explicit = SimpleNamespace(payload={"pmcid": "PMC123"}, doi="10.1234/experiment")
    assert (await resolve_publication_access(explicit, settings)).pmcid == "PMC123"
    fallback = await resolve_publication_access(SimpleNamespace(payload={}, doi="10.1234/experiment"), settings)
    assert fallback.doi == "10.1234/experiment" and fallback.limitations
    assert calls == ["PMC123", "10.1234/experiment"]


@pytest.mark.asyncio
async def test_notice_invalidates_existing_measurements_without_deleting_them(workspace, monkeypatch):
    dataset = load_dataset(workspace)
    dataset["publications"] = [{"id": "pmid:123", "source": "pubmed", "source_id": "123", "pmid": "123",
        "doi": "10.1234/experiment", "title": "Original experiment", "status": "active", "state": "active"}]
    dataset["experiments"] = [{"id": "experiment", "doi": "10.1234/experiment", "measurements": [
        {"id": "measurement", "status": "accepted", "value": 7, "evidence": []}]}]
    atomic_json(workspace / "dataset.json", dataset)

    async def empty_discovery(*args, **kwargs):
        return service.DiscoveryResult(complete=True)

    async def retraction(*args, **kwargs):
        return service.DiscoveryResult(complete=True, citations=[Citation(source="pubmed", source_id="123", pmid="123",
            doi="10.1234/experiment", title="Original experiment", status="retracted")])
    monkeypatch.setattr(literature, "discover", empty_discovery)
    monkeypatch.setattr(literature, "_known_pubmed", retraction)
    report = await literature.discover_workspace(workspace, None)
    observation = load_dataset(workspace)["experiments"][0]["measurements"][0]
    assert report["complete"] and observation["value"] == 7 and observation["status"] == "stale"
    assert load_manifest(workspace)["synthesis_stale"]


def test_weekly_schedule_handles_dst_and_never_uses_a_fixed_utc_offset():
    assert literature.next_weekly_check(datetime(2026, 3, 27, tzinfo=timezone.utc)).isoformat() == "2026-03-30T06:00:00+00:00"
    assert literature.next_weekly_check(datetime(2026, 10, 23, tzinfo=timezone.utc)).isoformat() == "2026-10-26T07:00:00+00:00"


@pytest.mark.asyncio
async def test_sleeping_computer_runs_one_overdue_check_only(workspace, monkeypatch):
    manifest = load_manifest(workspace)
    manifest["literature"] = {"next_metadata_check": "2026-09-28T06:00:00+00:00"}
    atomic_json(workspace / "manifest.json", manifest)
    calls = []

    async def checked(*args, **kwargs):
        calls.append((args, kwargs))
        return {"status": "completed", "openai_calls": 0}
    monkeypatch.setattr(literature, "discover_workspace", checked)
    assert (await literature.monitor_due(workspace, None, datetime(2026, 9, 20, tzinfo=timezone.utc)))["status"] == "not_due"
    assert (await literature.monitor_due(workspace, None, datetime(2026, 10, 4, tzinfo=timezone.utc)))["status"] == "completed"
    assert len(calls) == 1 and calls[0][1]["download"] is False


@pytest.mark.asyncio
async def test_partial_check_retry_does_not_wait_until_next_monday(workspace, monkeypatch):
    manifest = load_manifest(workspace)
    manifest["literature"] = {"next_metadata_check": "2026-10-05T06:00:00+00:00", "next_retry": "2026-10-04T09:00:00+00:00"}
    atomic_json(workspace / "manifest.json", manifest)

    async def checked(*args, **kwargs):
        return {"status": "completed"}
    monkeypatch.setattr(literature, "discover_workspace", checked)
    report = await literature.monitor_due(workspace, None, datetime(2026, 10, 4, 10, tzinfo=timezone.utc))
    assert report["status"] == "completed"


@pytest.mark.asyncio
async def test_local_discovery_rejects_authenticated_sources_before_any_network_request(workspace, monkeypatch):
    monkeypatch.setattr(literature, "discover", lambda *args: pytest.fail("No network should be attempted"))
    with pytest.raises(ValueError, match="keyless"):
        await literature.discover_workspace(workspace, None, ["scopus"])


def test_jats_preserves_local_pmid_and_pmcid(tmp_path):
    path = tmp_path / 'source.xml'
    path.write_text('<article><front><article-meta><article-id pub-id-type="pmid">123</article-id>'
                    '<article-id pub-id-type="pmc">456</article-id></article-meta></front>'
                    '<body><sec id="results"><p id="p1">7 um</p></sec></body></article>')
    inventory = inspect_jats(path)
    assert inventory['pmid'] == '123' and inventory['pmcid'] == 'PMC456'
