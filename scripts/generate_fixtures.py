"""Generate reproducible synthetic sources locally; no publisher material or model calls."""

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw


def write_pdf(path: Path):
    lines = ["Synthetic Pickering emulsion study. DOI: 10.5555/synthetic-fixture",
             "Sample A: droplet size 7 um; SD 1 um; 3 independent preparations.",
             "Sample B: droplet size 5 um; SD 0.5 um; 3 independent preparations.",
             "Independent experimental arms were prepared separately.",
             "This fixture is not an actual scientific publication."]
    commands = ["BT /F1 12 Tf 40 740 Td"]
    for line in lines:
        commands.extend([f"({line}) Tj", "0 -24 Td"])
    stream = ("\n".join(commands) + "\nET").encode("ascii")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
               f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"]
    document = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(document)
    document.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(document)


def generate(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    write_pdf(output / "synthetic-source.pdf")
    image = Image.new("RGB", (400, 400), "white")
    draw = ImageDraw.Draw(image)
    draw.line((40, 360, 360, 360), fill="black", width=2)
    draw.line((40, 40, 40, 360), fill="black", width=2)
    for pixel, value in [(40, 0), (200, 5), (360, 10)]:
        draw.text((pixel - 4, 365), str(value), fill="black")
    for pixel, value in [(360, 0), (200, 5), (40, 10)]:
        draw.text((15, pixel - 6), str(value), fill="black")
    draw.ellipse((194, 194, 206, 206), fill="red")
    draw.line((200, 160, 200, 240), fill="red", width=2)
    draw.line((194, 160, 206, 160), fill="red", width=2)
    draw.line((194, 240, 206, 240), fill="red", width=2)
    image.save(output / "synthetic-plot.png")
    manifest = {"kind": "synthetic_fixture", "paid_calls": 0,
                "expected": {"reported_means_um": [7, 5], "independent_n": 3,
                             "marker_xy": [5, 5], "errorbar_y_endpoints": [3.75, 6.25]},
                "files": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in sorted(output.iterdir()) if path.suffix in (".pdf", ".png")}}
    (output / "synthetic-manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("private/synthetic-fixtures"))
    print(json.dumps(generate(parser.parse_args().output), indent=2))
