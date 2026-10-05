# Literature discovery and access

The local edition works on a private folder without hosted services or provider API
keys. Preparing evidence, extracting with an existing local coding agent, validating,
synthesizing, and opening the HTML report are separate from literature discovery.
The discovery modules do not import the OpenAI SDK or Agents SDK. Scheduled
literature checks cannot trigger extraction or call a model.
Native query expressions are stored in each protocol, versioned, and sent to their
specific providers unchanged. Crossref and OpenAlex use their own free-text search
parameters; a Scopus Boolean expression is never silently translated for them.

| Provider | Role | Pagination | Update coverage | Local availability |
| --- | --- | --- | --- | --- |
| Crossref | DOI metadata, versions, corrections, Retraction Watch signals | Cursor, 200 records per page | Indexed date, including third-party metadata changes; separate known-DOI notice checks | Enabled by default; keyless, with contact email |
| PubMed | Biomedical citations and correction links | ESearch offsets and batched EFetch | Full topic reconciliation and unfiltered refresh of known PMID records | Enabled by default; keyless, with contact email and tool name |
| Europe PMC | Life-science articles and preprints | CursorMark, 200 records per page | Full topic reconciliation; first-index date is retained but never mislabelled as an update date | Enabled by default; keyless |
| arXiv | Optional preprint discovery | Offset, 100 records per page | Full topic reconciliation with updated timestamps retained | Optional; keyless |
| OpenAlex | Broad scholarly discovery | Cursor, 200 records per page | Full reconciliation; premium updated-date filter only with explicit permission | Retained hosted connector; excluded from local keyless commands |
| Scopus | Chemistry and chemical engineering abstracts | Provider cursor | Full reconciliation; publication date is never treated as update date | Retained hosted connector; requires Elsevier authorization and credentials |

The default protocol enables `pubmed`, `europepmc`, and `crossref`. Add `arxiv` after
configuring its native query. Local commands reject other providers before making
requests. The contact email identifies the research client; it is not a secret or
an authentication credential.
The default run is bounded at 25 pages per source. A bounded or failed run returns a
partial checkpoint and does not qualify as a completed metadata check.

```sh
livingmeta discover --workspace /private/review --contact-email researcher@example.org
livingmeta discover --workspace /private/review --contact-email researcher@example.org --download
livingmeta monitor-due --workspace /private/review --contact-email researcher@example.org
livingmeta schedule --workspace /private/review --contact-email researcher@example.org
```

The first command records pending publications. The second additionally downloads
supported, permitted PMC sources; it leaves them pending until an explicit extraction
run. `schedule` writes local scheduler templates for review and installation; it does
not install them. `monitor-due` checks Mondays at 08:00 Europe/Madrid, handles daylight
saving time, and performs one catch-up check after the computer wakes. A failed check
has a bounded retry schedule. No hosted account, recurring OpenAI call, or automatic
paper extraction is part of this monitoring path.

Each completed page persists its next cursor and citations. Resumption requires the
same query hash, source, and lower-bound timestamp. Crossref's August 2026 cursor
change requires all original parameters on subsequent pages; this connector retains
them and always reads the returned next cursor. Large queries can change during
pagination: repeated scheduled runs and identifier upserts are required rather than
claiming a transactional snapshot. PubMed's 10,000-record ESearch limit is reported
explicitly; protocols above that size must be subdivided before completeness can be
claimed.

Provider pacing is shared within a worker and across local processes using private
workspace pacing files. Hosted workers can additionally use Redis. Requests time out after 30 seconds, with
at most four attempts for rate limiting or temporary upstream failures. A long quota
reset becomes an actionable source limitation rather than an unbounded retry. API
keys, institution tokens, and contact emails are not written into error messages.

NCBI documents a keyless ceiling of three requests per second and asks distributed
clients to send `email` and `tool`; the connector spaces PubMed requests by at least
0.35 seconds. NCBI recommends off-peak hours for more than 100 requests. The Europe
PMC interval of one second is this application's conservative pacing choice, not a
claimed official quota. Provider throttling and `Retry-After` are respected.
[NCBI usage policies](https://www.ncbi.nlm.nih.gov/home/about/policies/)

Metadata freshness advances only when every configured provider and applicable
known-publication/access check completes. Checkpoints retain completed pages after
outages. A changed native query resets its discovery checkpoint and date filter.
The manifest records the last metadata check separately from extraction and synthesis;
pending or stale evidence prevents a synthesis from being presented as current.

## Version and status reconciliation

DOIs are normalized to lowercase after removing DOI URL or `doi:` prefixes. PMID and
PMCID remain explicit identifiers, including articles without a DOI. Exact identifiers
and explicit preprint-to-publication relations are merged. Similar titles
alone never cause a merge. A published version is preferred; the preprint identifier
remains related metadata. A preprint withdrawal is not automatically transferred to
a distinct journal version.

Provider records and correction notices are retained. The persistence layer must
apply `preserve_status(previous, incoming)` and the `status_updates` maps returned by
source runs, including target DOI records that are absent from the current topic
search. Missing status signals never restore retracted evidence. A verified restoration
requires an explicit adjudication rather than an automatic downgrade.

Notice relationships preserve their direction: `RetractionOf` or `ErratumFor` updates
the original article; it does not label the notice itself retracted. Notices remain
metadata records and are not new pending experimental studies. Changed or flagged
primary sources retain their measurements as stale evidence for reassessment; they
are not deleted, silently restored, or pooled as current evidence.

## Lawful full-text and media access

Discovery eligibility does not depend on open access. Missing text creates an access
limitation, not a scientific exclusion. Institutional Scopus metadata access does not
imply permission to download publisher full text.

The retained hosted Unpaywall resolver uses the supported `/v2/:doi` endpoint with a contact email,
retains OA location/version/license metadata, and never invokes its search endpoint,
which was retired on September 18, 2026. Landing pages and PDFs are distinguished.
Downloading and redistribution still require the corresponding permissions.

The local PMC resolver uses the current anonymous `pmc-oa-opendata` AWS distribution,
with no AWS account or key. It reads per-version JSON URLs for XML, text, PDF, original
media, and supplements. License and manuscript metadata are preserved; version
numbers alone do not determine preference. [PMC cloud documentation](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)

Downloads have bounded file/bundle sizes, permitted host and path checks, advertised
MD5 verification, local SHA-256 hashes, and resumable verified assets. XML is preferred
as one primary source when both XML and PDF exist; the PDF and original media remain
linked assets. JATS extraction preserves paragraph, table, caption, and figure element
locators without inventing PDF pages. Linked images are available to figure jobs.
Changed metadata or image hashes produce a new private bundle and invalidate the
previous extraction, even when the XML bytes are unchanged.

A missing selected prefix is flagged `source_unavailable`; another version is never
silently substituted. This is not a retraction or exclusion. The current connector
does not perform global PMC inventory reconciliation or claim confirmed removal
detection. PMC documents inventory reconciliation as its supported removal-detection
method. [PMC version and inventory rules](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)

Unrecognized licenses require review. TDM sources contribute XML/text only; their code
does not grant unrestricted redistribution. Article licenses may exclude individual
figures. Systematic article scraping from the main PMC website is prohibited, so this
workflow uses its supported cloud distribution. Private reports can contain source
excerpts and images: keep them private unless their reuse terms permit publication.
[PMC copyright notice](https://pmc.ncbi.nlm.nih.gov/about/copyright/),
[third-party notices](../THIRD_PARTY_NOTICES.md)

The local edition has no direct Europe PMC PDF/supplement ZIP or arXiv full-text
downloader. It resolves available PMC content from explicit PMCID or DOI conversion;
other sources require an authorized local upload. Access limitations remain separate
from scientific eligibility. Connector tests use mocked responses; no live provider
coverage or primary-paper extraction performance has been measured for this release.

## Primary documentation

- [OpenAlex authentication](https://help.openalex.org/api/authentication/)
- [OpenAlex updated-date filter permissions](https://help.openalex.org/api/filtering/)
- [Crossref incremental metadata filters](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)
- [Crossref August 2026 cursor changes](https://community.crossref.org/t/changes-to-cursors-filtering-and-sorting-in-the-rest-api/16246)
- [Crossref Retraction Watch metadata](https://www.crossref.org/documentation/retrieve-metadata/retraction-watch/)
- [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25499/)
- [PubMed correction relationship types](https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/el-CommentsCorrections.html)
- [Europe PMC REST API](https://europepmc.org/RestfulWebService)
- [Scopus Search API](https://dev.elsevier.com/documentation/ScopusSearchAPI.wadl)
- [arXiv API manual](https://info.arxiv.org/help/api/user-manual.html)
- [Unpaywall API](https://data.unpaywall.org/products/api)
- [PMC AWS distribution](https://pmc.ncbi.nlm.nih.gov/tools/pmcaws/)
- [PMC current bucket schema and version rules](https://pmc-oa-opendata.s3.amazonaws.com/README.txt)
- [PMC keyless identifier converter](https://pmc.ncbi.nlm.nih.gov/tools/id-converter-api/)
- [Crossref metadata reuse and abstract rights](https://www.crossref.org/documentation/retrieve-metadata/)

Documentation checked on October 4, 2026. Service availability and institutional
permissions are separate from connector implementation.
