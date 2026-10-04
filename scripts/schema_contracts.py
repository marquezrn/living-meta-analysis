"""Export versioned OpenAPI contracts into an ignored local directory."""

import json
import sys
from pathlib import Path

from livingmeta.api import create_app
from livingmeta.config import Settings


def main():
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("private/contracts/openapi.json")
    settings = Settings(_env_file=None, database_url="sqlite:///:memory:", private_directory=Path("private/contracts"))
    schema = create_app(settings, dispatch=lambda run: None).openapi()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
