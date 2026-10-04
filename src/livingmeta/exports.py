"""Portable exports containing source-supported values and explicit missingness."""

import csv
import html
import io
import json


def measurement_rows(experiments):
    rows = []
    for experiment in experiments:
        attrs = {a["name"]: a.get("text") for a in experiment.get("attributes", [])}
        for m in experiment.get("measurements", []):
            rows.append({"experiment_id": experiment["id"], "doi": experiment.get("doi"),
                "sample_label": experiment["sample_label"], "study_family": experiment["study_family"],
                **attrs, **{k: v for k, v in m.items() if k != "evidence"},
                "evidence": json.dumps(m.get("evidence", []), ensure_ascii=False),
                "validation_notes": "; ".join(m.get("validation_notes", []))})
    return rows


def render_export(format_name, experiments, project):
    rows = measurement_rows(experiments)
    if format_name == "json":
        return json.dumps({"project": project, "experiments": experiments,
                           "missingness_policy": "No silent imputation"}, indent=2,
                          ensure_ascii=False, allow_nan=False).encode(), "application/json"
    if format_name == "csv":
        stream = io.StringIO()
        columns = sorted({key for row in rows for key in row}) or ["experiment_id", "doi", "outcome"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formula execution from untrusted source text.
            safe = {k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v)
                    for k, v in row.items()}
            writer.writerow(safe)
        return stream.getvalue().encode(), "text/csv; charset=utf-8"
    if format_name == "parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq
        table = pa.Table.from_pylist([{k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                                       for k, v in row.items()} for row in rows])
        sink = io.BytesIO()
        pq.write_table(table, sink)
        return sink.getvalue(), "application/vnd.apache.parquet"
    if format_name == "html":
        columns = ["doi", "sample_label", "outcome", "raw_value", "unit", "qualifier", "status"]
        header = "".join(f"<th>{html.escape(c)}</th>" for c in columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(c) or ''))}</td>" for c in columns)
                       + "</tr>" for r in rows)
        title = html.escape(project["name"])
        metadata = html.escape(json.dumps({k: project.get(k) for k in ["last_metadata_check", "last_extraction",
                         "last_synthesis_update", "synthesis_stale", "pending_count"]}, indent=2))
        doc = f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{title}</title><style>body{{font:15px system-ui;margin:40px;background:#f5f8fb;color:#15293f}}table{{border-collapse:collapse;width:100%}}td,th{{padding:10px;border-bottom:1px solid #ccd6df;text-align:left}}pre{{white-space:pre-wrap}}h1{{color:#155e75}}</style><h1>{title}</h1><p>Evidence-preserving living review. Missing values and unresolved evidence are retained explicitly.</p><pre>{metadata}</pre><table><thead><tr>{header}</tr></thead><tbody>{body}</tbody></table></html>'
        return doc.encode(), "text/html; charset=utf-8"
    raise ValueError("Supported formats: csv, json, parquet, html")
