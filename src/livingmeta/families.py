"""Keep explicit preprint/journal-version relationships in one dependence family."""

from sqlalchemy import select

from .db import ExperimentRecord, PublicationRecord
from .discovery.identity import canonical_doi


def reconcile_families(db, project_id):
    parents = {}

    def root(doi):
        parents.setdefault(doi, doi)
        if parents[doi] != doi:
            parents[doi] = root(parents[doi])
        return parents[doi]

    for publication in db.scalars(select(PublicationRecord).where(PublicationRecord.project_id == project_id)):
        doi = canonical_doi(publication.doi)
        if not doi:
            continue
        for related in publication.payload.get("related_dois", []):
            related = canonical_doi(related)
            if related:
                a, b = root(doi), root(related)
                parents[max(a, b)] = min(a, b)
    for experiment in db.scalars(select(ExperimentRecord).where(ExperimentRecord.project_id == project_id)):
        doi = canonical_doi(experiment.payload.get("doi"))
        if doi in parents:
            family = root(doi)
            if experiment.payload.get("study_family") != family:
                experiment.payload = {**experiment.payload, "study_family": family,
                                      "family_basis": "explicit preprint/journal-version DOI relationship"}
