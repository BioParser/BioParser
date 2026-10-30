from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

# Importing the app reads the API settings, and these have no default. The schema does not
# depend on them, so placeholders do; the app is never started.
_PLACEHOLDER_SETTINGS = {
    "BIOPARSER_ARTIFACT_STORAGE_PATH": "/data",
    "BIOPARSER_REDIS_URL": "redis://localhost:6379/0",
    "BIOPARSER_DATABASE_URL": "postgresql://bioparser@localhost/bioparser",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openapi",
        description="Write the API's OpenAPI schema as JSON, for the frontend's generated types.",
    )
    parser.add_argument("output", type=Path, help="Path of the JSON file to write")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    for name, value in _PLACEHOLDER_SETTINGS.items():
        os.environ.setdefault(name, value)
    # Imported here, after the placeholders: the import reads the settings.
    from bioparser.api.app import app

    payload = json.dumps(app.openapi(), indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the same bytes on Windows, so CI's up-to-date check does not fail on CRLF.
    args.output.write_text(payload, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
