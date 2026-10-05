"""Offline HTML, scientific exports, and private-source integrity safeguards."""

import base64
import copy
import csv
import hashlib
import io
import json
from html.parser import HTMLParser
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from livingmeta.report import export_snapshot, generate_report, normalize_snapshot


def snapshot():
    return {"schema_version": 2, "project": {"name": "Synthetic offline review"},
            "experiments": [{"id": "e1", "sample_label": "Synthetic sample", "study_family": "family-1",
                             "attributes": [{"name": "preparation", "text": "Not reported"}],
                             "measurements": [{"id": "m1", "experiment_id": "e1", "outcome": "diameter",
                                               "raw_value": "<5", "value": None, "unit": "um", "qualifier": "lt",
                                               "status": "uncertain", "origin": "reported", "uncertainty_type": "none",
                                               "n_independent": None, "n_technical": None,
                                               "evidence": [{"document_hash": "0" * 64, "page": 1, "source_type": "text",
                                                             "locator": "Results", "excerpt": "Synthetic value <5 um."}]}]}]}


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts, self.metadata, self.current = [], {}, None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.current = {"attrs": attrs, "text": ""}
            self.scripts.append(self.current)
        elif tag == "meta" and attrs.get("http-equiv"):
            self.metadata[attrs["http-equiv"]] = attrs.get("content")

    def handle_endtag(self, tag):
        if tag == "script":
            self.current = None

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data


def test_report_contains_every_runtime_asset_and_disables_network_services(tmp_path):
    target = generate_report(snapshot(), tmp_path / "offline.html")
    parser = ReportParser()
    parser.feed(target.read_text())
    assert len(parser.scripts) == 2
    assert all("src" not in script["attrs"] for script in parser.scripts)
    assert parser.scripts[0]["attrs"]["type"] == "application/json"
    assert "Plotly" in parser.scripts[1]["text"]
    assert "connect-src 'none'" in parser.metadata["Content-Security-Policy"]
    assert "frame-src blob:" in parser.metadata["Content-Security-Policy"]
    assert "Software license notices" in target.read_text()
    assert "/api/v1" not in parser.scripts[1]["text"]
    assert "process.env.NODE_ENV" not in parser.scripts[1]["text"]
    assert "unsafe-eval" not in parser.metadata["Content-Security-Policy"]
    assert "img-src data: blob:" in parser.metadata["Content-Security-Policy"]
    assert 'type="module"' not in target.read_text()


def test_untrusted_dataset_text_cannot_escape_embedded_json_or_title(tmp_path):
    data = snapshot()
    attack = '</script><img src="https://invalid.example/leak" onerror="alert(1)">'
    data["project"]["name"] = attack
    data["experiments"][0]["measurements"][0]["evidence"][0]["excerpt"] = attack
    target = generate_report(data, tmp_path / "offline.html")
    parser = ReportParser()
    parser.feed(target.read_text())
    assert len(parser.scripts) == 2
    assert "<" not in parser.scripts[0]["text"]
    decoded = json.loads(parser.scripts[0]["text"])
    assert decoded["project"]["name"] == attack
    assert attack not in target.read_text()


def test_normalization_keeps_input_and_scientific_missingness_intact():
    data = snapshot()
    before = copy.deepcopy(data)
    parsed = normalize_snapshot(data)
    assert data == before
    item = parsed["experiments"][0]["measurements"][0]
    assert item["value"] is None
    assert item["n_independent"] is None
    assert item["uncertainty"] is None
    assert item["qualifier"] == "lt"
    assert item["status"] == "uncertain"
    assert parsed["publications"] == []
    assert parsed["synthesis"] == {}


@pytest.mark.parametrize("update", [{"schema_version": 1}, {"provenance": {"value": float("nan")}}, {"documents": ["not a record"]}])
def test_bad_snapshots_do_not_replace_an_existing_report(tmp_path, update):
    data = {**snapshot(), **update}
    target = tmp_path / "report.html"
    target.write_text("existing report")
    with pytest.raises(ValueError):
        generate_report(data, target)
    assert target.read_text() == "existing report"


def test_mismatched_and_duplicate_measurement_identity_is_rejected():
    data = snapshot()
    data["experiments"][0]["measurements"][0]["experiment_id"] = "wrong"
    with pytest.raises(ValueError, match="parent experiment"):
        normalize_snapshot(data)
    data = snapshot()
    data["experiments"][0]["measurements"].append(copy.deepcopy(data["experiments"][0]["measurements"][0]))
    with pytest.raises(ValueError, match="unique"):
        normalize_snapshot(data)


def test_xml_and_media_source_locations_remain_distinct_from_pdf_pages(tmp_path):
    data = snapshot()
    source = data["experiments"][0]["measurements"][0]["evidence"][0]
    source.update(source_format="xml", page=None, xml_element="/article/body/sec[1]/p[2]")
    output = export_snapshot(data, tmp_path / "xml-dataset.json", "json")
    parsed = json.loads(output.read_text())["experiments"][0]["measurements"][0]["evidence"][0]
    assert parsed["page"] is None
    assert parsed["xml_element"] == "/article/body/sec[1]/p[2]"
    source.update(source_format="media", asset_path="media/figure-1.png", xml_element=None)
    assert normalize_snapshot(data)["experiments"][0]["measurements"][0]["evidence"][0]["asset_path"] == "media/figure-1.png"
    source["page"] = 1
    with pytest.raises(ValueError, match="must not invent"):
        normalize_snapshot(data)


def test_csv_is_spreadsheet_safe_and_preserves_absent_sampling_information(tmp_path):
    data = snapshot()
    data["experiments"][0]["sample_label"] = "=EXECUTE_UNTRUSTED"
    output = export_snapshot(data, tmp_path / "evidence.csv", "csv")
    row = next(csv.DictReader(io.StringIO(output.read_text())))
    assert row["sample_label"] == "'=EXECUTE_UNTRUSTED"
    assert row["raw_value"] == "<5"
    assert row["value"] == ""
    assert row["n_independent"] == ""
    assert row["uncertainty"] == ""
    assert row["status"] == "uncertain"
    assert json.loads(row["evidence"])[0]["page"] == 1


def test_json_and_parquet_exports_preserve_evidence_and_nullable_values(tmp_path):
    data = snapshot()
    encoded = export_snapshot(data, tmp_path / "dataset.json", "json")
    decoded = json.loads(encoded.read_text())
    assert decoded["schema_version"] == 2
    assert decoded["experiments"][0]["attributes"][0]["status"] == "candidate"
    parquet = export_snapshot(data, tmp_path / "dataset.parquet", "parquet")
    row = pq.read_table(parquet).to_pylist()[0]
    assert row["value"] is None
    assert row["n_independent"] is None
    assert json.loads(row["evidence"])[0]["locator"] == "Results"


def test_only_explicit_hash_validated_private_bitmap_images_can_be_embedded(tmp_path):
    data = snapshot()
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aFZkAAAAASUVORK5CYII=")
    artifact = {"sha256": hashlib.sha256(image).hexdigest(), "mime_type": "image/png", "base64": base64.b64encode(image).decode()}
    data["embedded_artifacts"] = [artifact]
    output = generate_report(data, tmp_path / "private-images.html")
    assert artifact["base64"] in output.read_text()
    artifact["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="integrity"):
        normalize_snapshot(data)
    artifact["mime_type"] = "application/pdf"
    with pytest.raises(ValueError, match="Only explicit"):
        normalize_snapshot(data)


def test_packaged_assets_are_checked_in_and_include_dependency_license_notices():
    assets = Path(__file__).parents[1] / "src/livingmeta/report_assets"
    assert (assets / "reader.js").stat().st_size > 1_000_000
    notices = (assets / "NOTICES.txt").read_text()
    assert "plotly.js-dist-min 4.1.1" in notices
    assert "react 19.3.0" in notices
    assert "MIT License" in notices
