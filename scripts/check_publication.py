"""Allowlist publication files and reject credentials, private data and binary sources."""

import json
import re
from pathlib import Path

ROOT_FILES = {"README.md", "LICENSE", "CITATION.cff", "THIRD_PARTY_NOTICES.md", "pyproject.toml",
    "requirements.lock", "Dockerfile", "compose.yaml", "render.yaml", "alembic.ini", ".gitignore",
    ".dockerignore", ".env.example"}
ROOT_DIRS = {"src", "tests", "docs", "protocols", "statistics", "migrations", "scripts", "evaluation", ".github"}
WEB_FILES = {"web/package.json", "web/pnpm-lock.yaml", "web/index.html", "web/tsconfig.json", "web/vite.config.ts"}
EXCLUDED = {"private", "data", ".venv", "node_modules", "dist", "build", ".git", "__pycache__",
            ".pytest_cache", ".ruff_cache"}
SOURCE_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg", ".tif", ".parquet", ".sqlite", ".db", ".whl"}
SECRET_PATTERNS = [r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}", r"gh[pousr]_[A-Za-z0-9]{25,}",
                   r"AKIA[0-9A-Z]{16}", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"]


def publication_files(root: Path):
    output = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED or part.endswith(".egg-info") for part in relative.parts):
            continue
        name = relative.as_posix()
        selected = name in ROOT_FILES or relative.parts[0] in ROOT_DIRS or name in WEB_FILES or name.startswith("web/src/")
        if not selected:
            continue
        if path.is_symlink() or path.suffix.lower() in SOURCE_EXTENSIONS or (path.name.startswith(".env") and name != ".env.example"):
            raise ValueError(f"Private or binary file selected for publication: {name}")
        content = path.read_bytes()
        if b"\0" in content:
            raise ValueError(f"Binary file selected for publication: {name}")
        text = content.decode("utf-8")
        if any(re.search(pattern, text) for pattern in SECRET_PATTERNS):
            raise ValueError(f"Possible credential selected for publication: {name}")
        if any(token in path.name.lower() for token in ("manual_answers", "gold_answers", "pasted text")):
            raise ValueError(f"Reference answers selected for publication: {name}")
        output.append({"path": name, "mode": "100644", "type": "blob", "content": text})
    return output


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    files = publication_files(root)
    print(json.dumps({"checked_files": len(files), "private_sources_included": False,
                      "credentials_included": False, "paths": [f["path"] for f in files]}, indent=2))
