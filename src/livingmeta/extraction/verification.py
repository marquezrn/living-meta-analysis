"""Deterministic source checks, separate from same-model review."""

import math
import re

from pydantic import BaseModel, Field

from livingmeta.domain import Attribute, ExtractionBatch, Measurement
from .normalization import normalize_measurement


class VerificationReview(BaseModel):
    rejected_measurement_ids: list[str] = Field(default_factory=list)
    uncertain_measurement_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)


def canonical_text(text: str) -> str:
    return " ".join(text.replace("−", "-").replace("µ", "μ").split()).casefold()


def numeric_values(text: str) -> list[float]:
    numbers = re.findall(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?(?![\w.])", text.replace("−", "-"))
    result = []
    for number in numbers:
        try:
            result.append(float(number.replace(",", ".")))
        except ValueError:
            pass
    return result


def supported_number(value: float, excerpt: str) -> bool:
    return math.isfinite(value) and any(math.isclose(value, v, rel_tol=1e-8, abs_tol=1e-12) for v in numeric_values(excerpt))


def source_text(page: dict) -> str:
    tables = "\n".join(" | ".join(str(cell or "") for cell in row) for table in page["tables"] for row in table["rows"])
    return page["text"] + "\n" + tables


def verify_measurement(measurement: Measurement, pages: dict[int, dict], document_hash: str) -> Measurement:
    output = measurement.model_copy(deep=True)
    failures, ambiguities = [], []
    if not output.evidence:
        failures.append("No source evidence")
    excerpts = []
    for evidence in output.evidence:
        page = pages.get(evidence.page)
        if evidence.document_hash != document_hash:
            failures.append("Evidence document hash does not match the uploaded source")
        if page is None:
            failures.append("Evidence page is outside the document")
            continue
        if page.get("text_status", "").startswith("ocr"):
            ambiguities.append("OCR evidence requires visual verification against the rendered source")
        if evidence.source_type in {"text", "table"}:
            if not evidence.excerpt.strip() or canonical_text(evidence.excerpt) not in canonical_text(source_text(page)):
                failures.append("Exact source excerpt was not found on the stated page")
            else:
                excerpts.append(evidence.excerpt)
            if evidence.source_type == "table" and not any(t["id"] in evidence.locator for t in page["tables"]):
                ambiguities.append("Table locator was not found in structural inventory")
        elif output.origin == "reported":
            failures.append("Reported numerical figure values require calibrated geometry, not visual estimation")
        if evidence.bounding_box is not None:
            box = evidence.bounding_box
            if len(box) != 4 or any(not math.isfinite(v) for v in box) or not (
                    0 <= box[0] < box[2] <= page["width"] and 0 <= box[1] < box[3] <= page["height"]):
                failures.append("Evidence bounding box is outside PDF page bounds")
    text = "\n".join(excerpts)
    if output.origin == "reported":
        for name in ("value", "lower_bound", "upper_bound", "uncertainty", "ci_lower", "ci_upper", "time_value"):
            value = getattr(output, name)
            if value is not None and not supported_number(value, text):
                failures.append(f"Reported {name} lacks exact numerical source support")
        if output.raw_value and canonical_text(output.raw_value) not in canonical_text(text):
            # Formatting may differ, but every numeric token in the raw expression
            # must still occur in the exact excerpt. A different error bar is invalid.
            raw_numbers = numeric_values(output.raw_value)
            if not raw_numbers or any(not supported_number(value, text) for value in raw_numbers):
                failures.append("Raw value expression lacks source support")
        signs = {"lt": ("<", "less than"), "le": ("≤", "<=", "at most"),
                 "gt": (">", "greater than"), "ge": ("≥", ">=", "at least")}
        if output.qualifier in signs and not any(sign in text.casefold() for sign in signs[output.qualifier]):
            failures.append("Inequality qualifier lacks explicit source support")
        if output.unit and canonical_text(output.unit) not in canonical_text(text):
            ambiguities.append("Unit is not literal in excerpt; verify table header or dimensional context")
        if output.time_unit and canonical_text(output.time_unit) not in canonical_text(text):
            ambiguities.append("Time unit is not literal in excerpt; time-point interpretation requires verification")
        if output.measurement_method and canonical_text(output.measurement_method) not in canonical_text(text):
            ambiguities.append("Measurement method is not literal in excerpt; method interpretation requires verification")
        if output.statistic:
            meaning = {"mean": r"\b(?:mean|average)\b", "arithmetic_mean": r"\b(?:mean|average)\b",
                       "median": r"\bmedian\b"}.get(output.statistic, re.escape(output.statistic))
            if not re.search(meaning, text, re.I):
                ambiguities.append("Statistic definition lacks explicit source support")
        if output.concentration_basis:
            basis = output.concentration_basis.casefold()
            terms = {"mass": r"wt\.?\s*%|w/w|mass|weight", "volume": r"vol\.?\s*%|v/v|volume"}
            kind = "mass" if basis in {"mass", "mass/mass", "w/w", "wt", "weight"} else "volume" if basis in {"volume", "volume/volume", "v/v", "vol"} else None
            if kind is None or not re.search(terms[kind], text, re.I):
                ambiguities.append("Concentration basis lacks explicit source support; conversion withheld")
                output.concentration_basis = None
        if output.uncertainty_type not in {"none", "unknown"}:
            terms = {"SD": r"\b(?:sd|standard deviation)\b", "SE": r"\b(?:se|sem|standard error)\b",
                     "CI": r"\b(?:ci|confidence interval)\b"}
            if not re.search(terms[output.uncertainty_type], text, re.I):
                output.uncertainty_type = "unknown"
                ambiguities.append("Uncertainty definition not explicitly supported; kept unknown")
        for field in ("n_independent", "n_technical"):
            count = getattr(output, field)
            if count is not None and not supported_number(count, text):
                failures.append(f"{field} lacks numerical source support")
        if output.n_independent is not None:
            count = output.n_independent
            defined = re.search(r"independent|separately prepared|separate preparations|biological replicates", text, re.I)
            tied = re.search(rf"\b{count}\s+(?:[a-z]+\s+){{0,3}}(?:independent|separate|biological)\b|(?:independent|separate|biological).{{0,55}}\bn\s*[=:]\s*{count}\b", text, re.I)
            if not defined or not tied:
                output.n_independent = None
                ambiguities.append("Independent preparation count is not explicitly tied to the reported n; independent n withheld")
        if output.n_technical is not None and not re.search(r"technical|particles|droplets|repeat|replicat|measurements", text, re.I):
            output.n_technical = None
            ambiguities.append("Technical count definition not explicit; technical n withheld")
    if output.origin == "curve_sample":
        output.n_independent = None
        output.n_technical = None
        ambiguities.append("Interpolated curve sample cannot establish experimental replication")
    if output.qualifier == "missing":
        output.status = "uncertain"
        ambiguities.append("Missing observation retained without imputation")
    elif failures:
        output.status = "rejected"
    elif ambiguities or output.origin in {"digitized", "derived", "curve_sample"}:
        output.status = "uncertain"
    else:
        output.status = "accepted"
    output.validation_notes.extend(failures + ambiguities)
    return normalize_measurement(output)


def verify_batch(batch: ExtractionBatch, pages: dict[int, dict], document_hash: str) -> ExtractionBatch:
    result = batch.model_copy(deep=True)
    for experiment in result.experiments:
        experiment.measurements = [verify_measurement(m, pages, document_hash) for m in experiment.measurements]
        experiment.attributes = [verify_attribute(a, pages, document_hash) for a in experiment.attributes]
    return result


def verify_attribute(attribute: Attribute, pages: dict[int, dict], document_hash: str) -> Attribute:
    output = attribute.model_copy(deep=True)
    excerpts, failures, ambiguities = [], [], []
    for evidence in output.evidence:
        page = pages.get(evidence.page)
        if (not page or evidence.document_hash != document_hash or evidence.source_type not in {"text", "table"}
                or not evidence.excerpt.strip() or canonical_text(evidence.excerpt) not in canonical_text(source_text(page))):
            failures.append("Condition attribute evidence lacks exact primary-source support")
        else:
            excerpts.append(evidence.excerpt)
            if page.get("text_status", "").startswith("ocr"):
                ambiguities.append("OCR condition evidence requires visual source verification")
            if evidence.source_type == "table" and not any(t["id"] in evidence.locator for t in page["tables"]):
                ambiguities.append("Condition table locator requires verification")
            if evidence.bounding_box is not None:
                box = evidence.bounding_box
                if len(box) != 4 or not (0 <= box[0] < box[2] <= page["width"] and 0 <= box[1] < box[3] <= page["height"]):
                    failures.append("Condition evidence bounding box lacks exact page support")
    text = "\n".join(excerpts)
    if not excerpts:
        output.status = "uncertain"
        failures.append("Condition attribute requires source evidence")
    elif output.name == "arms_independent" and output.text == "true":
        output.status = "accepted" if re.search(r"independent(?:ly)?[^.]{0,60}(?:arms|groups)|(?:arms|groups)[^.]{0,60}independent", text, re.I) else "uncertain"
        if output.status != "accepted":
            failures.append("Independent arms are not explicitly established by the source")
    elif canonical_text(output.text) not in canonical_text(text):
        output.status = "uncertain"
        failures.append("Condition text assignment requires interpretation")
    else:
        output.status = "accepted"
    if output.value is not None and not supported_number(output.value, text):
        failures.append("Condition value lacks numerical support")
        output.status = "rejected"
    if any("lacks exact" in failure for failure in failures):
        output.status = "rejected"
    elif ambiguities and output.status == "accepted":
        output.status = "uncertain"
    output.validation_notes.extend(failures + ambiguities)
    return output


def apply_review(batch: ExtractionBatch, review: VerificationReview) -> ExtractionBatch:
    output = batch.model_copy(deep=True)
    rejected, uncertain = set(review.rejected_measurement_ids), set(review.uncertain_measurement_ids)
    for experiment in output.experiments:
        for measurement in experiment.measurements:
            if measurement.id in rejected:
                measurement.status = "rejected"
                measurement.validation_notes.extend(["Specialist source review rejected candidate", *review.reasons])
            elif measurement.id in uncertain and measurement.status != "rejected":
                measurement.status = "uncertain"
                measurement.validation_notes.extend(["Specialist source review requires adjudication", *review.reasons])
    return output
