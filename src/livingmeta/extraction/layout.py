"""PDF geometry, text, tables, page rendering, and optional local OCR.

No contents are uploaded by this module. Raster and vector inventories are
candidates, not a claim that every scientific figure has been understood.
"""

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _box(item, width: float, height: float) -> list[float]:
    return [max(0.0, float(item["x0"])), max(0.0, float(item["top"])),
            min(width, float(item["x1"])), min(height, float(item["bottom"]))]


def _overlap(a, b) -> bool:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return smaller > 0 and ix * iy / smaller > 0.65


def _vector_regions(page) -> list[list[float]]:
    """Merge nearby drawing geometry; short rules remain possible false positives."""
    regions = []
    objects = list(page.curves) + list(page.rects) + list(page.lines)
    for obj in objects[:10000]:
        box = _box(obj, page.width, page.height)
        if box[2] <= box[0] and box[3] <= box[1]:
            continue
        attached = []
        for i, existing in enumerate(regions):
            if (box[0] <= existing[2] + 10 and box[2] >= existing[0] - 10
                    and box[1] <= existing[3] + 10 and box[3] >= existing[1] - 10):
                attached.append(i)
        for i in reversed(attached):
            old = regions.pop(i)
            box = [min(box[0], old[0]), min(box[1], old[1]), max(box[2], old[2]), max(box[3], old[3])]
        regions.append(box)
    return [box for box in regions if box[2] - box[0] >= 30 and box[3] - box[1] >= 30]


def ocr_image(image_path: Path, timeout: int = 90) -> dict:
    """Run Tesseract locally with an argument vector, or report its absence."""
    executable = shutil.which("tesseract")
    if not executable:
        return {"status": "unavailable", "text": "", "words": []}
    try:
        result = subprocess.run([executable, str(image_path), "stdout", "--psm", "3", "tsv"],
                                capture_output=True, text=True, timeout=timeout, check=True)
    except (subprocess.SubprocessError, OSError) as error:
        return {"status": "failed", "text": "", "words": [], "error": type(error).__name__}
    lines = result.stdout.splitlines()
    words = []
    for line in lines[1:]:
        fields = line.split("\t", 11)
        if len(fields) != 12 or not fields[11].strip():
            continue
        try:
            confidence = float(fields[10])
            if confidence < 50:
                continue
            left, top, width, height = map(int, fields[6:10])
        except ValueError:
            continue
        words.append({"text": fields[11], "box": [left, top, left + width, top + height],
                      "confidence": confidence})
    return {"status": "ocr", "text": " ".join(word["text"] for word in words), "words": words}


def render_page(path: Path, page_index: int, destination: Path, scale: float = 2.0) -> Path:
    if scale <= 0 or scale > 4:
        raise ValueError("Render scale must be in (0, 4]")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pdfium.PdfDocument(str(path)) as document:
        page = document[page_index]
        try:
            bitmap = page.render(scale=scale)
            try:
                bitmap.to_pil().convert("RGB").save(destination)
            finally:
                bitmap.close()
        finally:
            page.close()
    return destination


def render_crop(image_path: Path, bbox: list[float], destination: Path) -> Path:
    """Crop an already-rendered page using bounded pixel coordinates."""
    with Image.open(image_path) as source:
        x0, y0, x1, y1 = bbox
        if x0 < 0 or y0 < 0 or x1 > source.width or y1 > source.height or x1 <= x0 or y1 <= y0:
            raise ValueError("Crop bounding box is outside the page image")
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.crop((x0, y0, x1, y1)).save(destination)
    return destination


def inspect_pdf(path: Path, artifact_dir: Path | None = None, *, scale: float = 2.0,
                use_ocr: bool = True, max_pages: int = 1000) -> dict:
    """Return deterministic structural inventory; optionally write private images.

    Every page has text/table/figure candidates and explicit processing status.
    Text/geometry extraction cannot establish scientific eligibility or coverage.
    """
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("Only primary-source PDF documents are accepted")
    digest = file_hash(path)
    inventory = {"document_hash": digest, "page_count": 0, "pages": [], "warnings": []}
    with pdfplumber.open(path) as document:
        if len(document.pages) > max_pages:
            raise ValueError(f"Document exceeds {max_pages} pages")
        inventory["page_count"] = len(document.pages)
        for index, page in enumerate(document.pages):
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""
            words = [{"text": word["text"], "box": _box(word, page.width, page.height)}
                     for word in page.extract_words()]
            tables = []
            try:
                for number, table in enumerate(page.find_tables()):
                    tables.append({"id": f"page-{index + 1}-table-{number + 1}",
                                   "box": list(table.bbox), "rows": table.extract(),
                                   "status": "detected_pending_extraction"})
            except (ValueError, TypeError) as error:
                inventory["warnings"].append(f"Page {index + 1}: table detection {type(error).__name__}")
            figures = []
            for image in page.images:
                box = _box(image, page.width, page.height)
                if box[2] - box[0] >= 20 and box[3] - box[1] >= 20:
                    figures.append({"box": box, "kind": "raster", "status": "detected_pending_review"})
            for box in _vector_regions(page):
                if not any(_overlap(box, existing["box"]) for existing in tables + figures):
                    figures.append({"box": box, "kind": "vector", "status": "detected_pending_review"})
            captions = [line.strip() for line in text.splitlines()
                        if re.match(r"^\s*(?:Fig(?:ure)?\.?|Scheme)\s*\d", line, re.I)]
            for number, figure in enumerate(figures):
                figure["id"] = f"page-{index + 1}-figure-candidate-{number + 1}"
            # An entire page remains a visual candidate when caption geometry is absent.
            if captions and not figures:
                figures.append({"id": f"page-{index + 1}-figure-page", "kind": "unlocalized_caption",
                                "box": [0, 0, page.width, page.height], "status": "detected_pending_review"})
            record = {"page": index + 1, "width": page.width, "height": page.height,
                      "text": text, "words": words, "text_status": "extracted" if text.strip() else "missing",
                      "tables": tables, "figures": figures, "captions": captions, "notes": []}
            if artifact_dir is not None:
                image_path = render_page(path, index, Path(artifact_dir) / f"page-{index + 1:04d}.png", scale)
                with Image.open(image_path) as image:
                    record["pixel_width"], record["pixel_height"] = image.size
                record["image_path"] = str(image_path)
                # OCR supplements tick labels even on text-bearing pages.
                if use_ocr:
                    ocr = ocr_image(image_path)
                    record["ocr"] = ocr
                    if not text.strip() and ocr["text"]:
                        record["text"] = ocr["text"]
                        record["text_status"] = "ocr_requires_review"
                    elif not text.strip():
                        record["notes"].append(f"OCR {ocr['status']}; text remains unavailable")
            elif not text.strip() and use_ocr:
                record["notes"].append("OCR requires page rendering; no artifact directory supplied")
            inventory["pages"].append(record)
    return inventory
