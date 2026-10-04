# Literature discovery and access

The discovery service performs metadata requests only. Its modules do not import the
OpenAI SDK or Agents SDK, and scheduled literature checks cannot trigger extraction.
Native query expressions are stored in each protocol, versioned, and sent to their
specific providers unchanged. Crossref and OpenAlex use their own free-text search
parameters; a Scopus Boolean expression is never silently translated for them.

| Provider | Role | Pagination | Update coverage | Credentials |
| --- | --- | --- | --- | --- |
| OpenAlex | Broad scholarly discovery | Cursor, 200 records per page | Full topic reconciliation by default; metadata updated-date filtering only when premium permission is explicitly configured | Optional API key; premium permission for updated-date filter |
| Crossref | DOI metadata, versions, corrections, Retraction Watch signals | Cursor, 200 records per page | Indexed date, including third-party metadata changes | Optional contact email |
| PubMed | Biomedical citations and correction links | ESearch offsets and batched EFetch | Full topic reconciliation captures both newly indexed older papers and corrections | Optional NCBI key and contact email |
| Europe PMC | Life-science articles and preprints | CursorMark, 200 records per page | Full topic reconciliation; first-index date is retained but never mislabelled as an update date | None |
| Scopus | Chemistry and chemical engineering abstracts | Provider cursor | Full topic reconciliation; publication date is never treated as update date | Elsevier key and verified institution entitlement; institution token when issued |
| arXiv | Optional preprints | Offset, 100 records per page | Full topic reconciliation with each version's updated timestamp retained | None |

The default protocol enables the four open metadata sources. Add `scopus` or `arxiv`
to `enabled_sources` after configuring their native protocol queries. Optional
credential dictionary keys are `openalex_api_key`, `openalex_updated_filter` (the
literal string `true`, only with paid permission), `scopus_api_key`,
`scopus_insttoken`, `pubmed_api_key`, `contact_email`, `redis_url`, and `max_pages`.
The default run is bounded at 25 pages per source. A bounded or failed run returns a
partial checkpoint and does not qualify as a completed metadata check.

Each completed page persists its next cursor and citations. Resumption requires the
same query hash, source, and lower-bound timestamp. Crossref's August 2026 cursor
change requires all original parameters on subsequent pages; this connector retains
them and always reads the returned next cursor. Large queries can change during
pagination: repeated scheduled runs and identifier upserts are required rather than
claiming a transactional snapshot. PubMed's 10,000-record ESearch limit is reported
explicitly; protocols above that size must be subdivided before completeness can be
claimed.

Provider pacing is shared by clients in one worker. Configure Redis in deployed
workers to share pacing across processes. Requests time out after 30 seconds, with
at most four attempts for rate limiting or temporary upstream failures. A long quota
reset becomes an actionable source limitation rather than an unbounded retry. API
keys, institution tokens, and contact emails are not written into error messages.

## Version and status reconciliation

DOIs are normalized to lowercase after removing DOI URL or `doi:` prefixes. Exact
identifiers and explicit preprint-to-publication relations are merged. Similar titles
alone never cause a merge. A published version is preferred; the preprint identifier
remains related metadata. A preprint withdrawal is not automatically transferred to
a distinct journal version.

Provider records and correction notices are retained. The persistence layer must
apply `preserve_status(previous, incoming)` and the `status_updates` maps returned by
source runs, including target DOI records that are absent from the current topic
search. Missing status signals never restore retracted evidence. A verified restoration
requires an explicit adjudication rather than an automatic downgrade.

## Lawful full-text and media access

Discovery eligibility does not depend on open access. Missing text creates an access
limitation, not a scientific exclusion. Institutional Scopus metadata access does not
imply permission to download publisher full text.

The Unpaywall resolver uses the supported `/v2/:doi` endpoint with a contact email,
retains OA location/version/license metadata, and never invokes its search endpoint,
which was retired on September 18, 2026. Landing pages and PDFs are distinguished.
Downloading and redistribution still require the corresponding permissions.

The PMC resolver uses the current `pmc-oa-opendata` AWS bucket. Legacy OA FTP packages
were removed in August 2026. It lists every available PMCID version, fetches its JSON
metadata, and resolves XML, text, PDF, figures, and supplements from documented
`*_url` and `media_urls` fields. Per-version license, manuscript state, checksums,
and retraction metadata are preserved. A higher version number is not assumed to be
the preferred published version. A missing version 1 is not fabricated. TDM licenses
do not confer unrestricted redistribution rights. No source PDF or media is copied
into the public code repository.

## Primary documentation

- [OpenAlex authentication](https://help.openalex.org/api/authentication/)
- [OpenAlex updated-date filter permissions](https://help.openalex.org/api/filtering/)
- [Crossref incremental metadata filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)
- [Crossref August 2026 cursor changes](https://community.crossref.org/t/changes-to-cursors-filtering-and-sorting-in-the-rest-api/16246)
- [Crossref Retraction Watch metadata](https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/)
- [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/)
- [PubMed correction relationship types](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/230101/el-CommentsCorrections.html)
- [Europe PMC REST API](https://europepmc.org/RestfulWebService)
- [Scopus Search API](https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl)
- [arXiv API manual](https://info.arxiv.org/help/api/user-manual.html)
- [Unpaywall API](https://data.unpaywall.org/products/api)
- [PMC AWS distribution](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)
- [PMC current bucket schema and version rules](https://pmc-oa-opendata.s3.amazonaws.com/README.txt)

Documentation checked on October 3, 2026. Service availability and institutional
permissions are separate from connector implementation.
