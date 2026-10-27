from __future__ import annotations

import argparse
import json
from pathlib import Path

from bioparser.api.app import app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openapi",
        description="Write the API's OpenAPI schema as JSON, for the frontend's generated types.",
    )
    parser.add_argument("output", type=Path, help="Path of the JSON file to write")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.dumps(app.openapi(), indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n": the same bytes on Windows, so CI's up-to-date check does not fail on CRLF.
    args.output.write_text(payload, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
