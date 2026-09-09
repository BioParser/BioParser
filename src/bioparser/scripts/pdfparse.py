from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bioparser.parser import (
    DEFAULT_PARSER_NAME,
    PARSER_BACKENDS,
    ParserBackendUnavailableError,
    UnsupportedDocumentError,
    get_parser,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-parse",
        description="Convert a PDF into a validated parser artifact JSON.",
    )
    parser.add_argument("input", type=Path, help="Path to a PDF file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write JSON to this path (default: stdout)",
    )
    parser.add_argument(
        "--backend",
        choices=PARSER_BACKENDS,
        default=DEFAULT_PARSER_NAME,
        help=f"Parser backend (default: {DEFAULT_PARSER_NAME})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        artifact = get_parser(args.backend).parse(args.input)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except UnsupportedDocumentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except ParserBackendUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    payload = artifact.model_dump_json(indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(payload)
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
