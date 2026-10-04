"""Evaluator-only reference loading. Parse literal arrays without executing JavaScript."""

import ast
import hashlib
import io
import math
import re
import tokenize
from pathlib import Path

from livingmeta.discovery.identity import canonical_doi


def _array_literal(source: str, start: int) -> str:
    quote = None
    escaped = False
    depth = 0
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ("'", '"'):
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
        if depth > 10:
            raise ValueError("Reference nesting exceeds the safe parser limit")
    raise ValueError("Reference array is not terminated")


def import_reference_html(path: str | Path) -> list[dict]:
    path = Path(path)
    if path.stat().st_size > 20_000_000:
        raise ValueError("Reference HTML exceeds the safe parser size limit")
    source = path.read_text(encoding="utf-8")
    match = re.search(r"\b(?:const|let|var)\s+database\s*=\s*(\[)", source)
    if not match:
        raise ValueError("Expected the original literal database array")
    literal = _array_literal(source, match.start(1))
    translated = []
    names = {"null": "None", "true": "True", "false": "False"}
    for token in tokenize.generate_tokens(io.StringIO(literal).readline):
        if token.type == tokenize.NAME:
            if token.string not in names:
                raise ValueError("Nonliteral JavaScript is not accepted in the reference array")
            token = token._replace(string=names[token.string])
        translated.append(token)
    try:
        rows = ast.literal_eval(tokenize.untokenize(translated))
    except (SyntaxError, ValueError) as exc:
        raise ValueError("Reference array must contain literal objects only") from exc
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) for r in rows):
        raise ValueError("Reference must be a nonempty list of records")
    source_hash = hashlib.sha256(literal.encode()).hexdigest()
    result = []
    for index, row in enumerate(rows):
        if any(not isinstance(key, str) or not isinstance(value, (str, int, float, bool, type(None)))
               for key, value in row.items()):
            raise ValueError("Reference fields must be scalar literals")
        if any(isinstance(value, float) and not math.isfinite(value) for value in row.values()):
            raise ValueError("Reference numerical fields must be finite")
        doi = canonical_doi(row.get("DOI"))
        if not doi:
            raise ValueError(f"Reference row {index + 1} has no valid DOI")
        result.append(dict(row, DOI=doi, _reference_row_id=index + 1,
                           _reference_hash=source_hash, _reference_origin="manual_original",
                           _study_family=doi))
    return result


def family_split(study_family: str, holdout_fraction: float = 0.2, seed: str = "livingmeta-v1") -> str:
    """Stable assignment prevents experiments from one study family leaking across splits."""
    if not 0 < holdout_fraction < 1:
        raise ValueError("Holdout fraction must be strictly between zero and one")
    digest = hashlib.sha256(f"{seed}:{study_family}".encode()).digest()
    fraction = int.from_bytes(digest[:8], "big") / 2 ** 64
    return "holdout" if fraction < holdout_fraction else "development"
