"""Canonical identifiers and conservative publication-version reconciliation."""

import re
from urllib.parse import unquote

from livingmeta.domain import Citation

STATUS_PRIORITY = {"active": 0, "corrected": 1, "concern": 2, "withdrawn": 3, "retracted": 4}


def canonical_doi(value: str | None) -> str | None:
    if not value:
        return None
    value = unquote(value.strip())
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value, flags=re.I)
    value = value.strip().lower()
    return value if re.fullmatch(r"10\.\d{4,9}/\S+", value) else None


def preserve_status(previous: str, incoming: str) -> str:
    """Missing status signals never undo a known correction or retraction."""
    return max((previous, incoming), key=lambda s: STATUS_PRIORITY.get(s, 0))


def reconcile_citations(citations: list[Citation]) -> list[Citation]:
    """Merge exact identifiers and explicit preprint/version relations, never fuzzy titles."""
    parent: dict[str, str] = {}

    def find(key: str) -> str:
        parent.setdefault(key, key)
        if parent[key] != key:
            parent[key] = find(parent[key])
        return parent[key]

    def key(citation: Citation) -> str:
        return citation.doi or (f"pmid:{citation.pmid}" if citation.pmid else
                                f"pmcid:{citation.pmcid}" if citation.pmcid else f"{citation.source}:{citation.source_id}")

    for citation in citations:
        citation.doi = canonical_doi(citation.doi)
        root = find(key(citation))
        for identifier in (f"pmid:{citation.pmid}" if citation.pmid else None,
                           f"pmcid:{citation.pmcid}" if citation.pmcid else None):
            if identifier:
                parent[find(identifier)] = root
        for related in citation.related_dois:
            if (doi := canonical_doi(related)):
                parent[find(doi)] = root
    groups: dict[str, list[Citation]] = {}
    for citation in citations:
        groups.setdefault(find(key(citation)), []).append(citation)
    result = []
    for group in groups.values():
        ordered = sorted(group, key=lambda c: (c.is_preprint, not bool(c.doi), not bool(c.abstract)))
        chosen = ordered[0].model_copy(deep=True)
        # A retracted preprint does not automatically retract a distinct journal version.
        same_version = [c for c in group if c.doi == chosen.doi]
        # Identifier-only records may join through another provider's exact alias.
        while True:
            pmids = {c.pmid for c in same_version if c.pmid}
            pmcids = {c.pmcid for c in same_version if c.pmcid}
            additions = [c for c in group if c not in same_version and not c.doi and
                         ((c.pmid and c.pmid in pmids) or (c.pmcid and c.pmcid in pmcids))]
            if not additions:
                break
            same_version.extend(additions)
        for other in same_version:
            chosen.status = preserve_status(chosen.status, other.status)
        chosen.pmid = chosen.pmid or next((c.pmid for c in same_version if c.pmid), None)
        chosen.pmcid = chosen.pmcid or next((c.pmcid for c in same_version if c.pmcid), None)
        chosen.license = chosen.license or next((c.license for c in same_version if c.license), None)
        relations = {str(r.model_dump()): r for c in same_version for r in c.update_relations}
        chosen.update_relations = list(relations.values())
        chosen.abstract = chosen.abstract or next((c.abstract for c in group if c.abstract), None)
        chosen.full_text_url = chosen.full_text_url or next(
            (c.full_text_url for c in same_version if c.full_text_url), None
        )
        chosen.related_dois = sorted({c.doi for c in group if c.doi and c.doi != chosen.doi}
                                     | {d for c in group for d in c.related_dois if d != chosen.doi})
        result.append(chosen)
    return result
