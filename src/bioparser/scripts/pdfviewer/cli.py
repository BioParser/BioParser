from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from bioparser.parser import DEFAULT_PARSER_NAME, PARSER_BACKENDS, ParserBackendUnavailableError
from bioparser.scripts.pdfviewer.server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-view",
        description="Local viewer that overlays parser block boxes on a PDF.",
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        help="PDF to load on startup (optional; you can also upload in the browser)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port to listen on (default: 8765)",
    )
    parser.add_argument(
        "--backend",
        choices=PARSER_BACKENDS,
        default=DEFAULT_PARSER_NAME,
        help="Parser backend for the initial parse and as the UI default",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    initial = args.input
    if initial is not None:
        pdf_path = initial.expanduser().resolve()
        if not pdf_path.is_file():
            print(f"PDF not found: {pdf_path}", file=sys.stderr)
            return 1
        initial = pdf_path

    try:
        app = create_app(initial, backend=args.backend)
    except ParserBackendUnavailableError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    host = "127.0.0.1"
    print(f"PDF viewer: http://{host}:{args.port}/", file=sys.stderr)
    uvicorn.run(app, host=host, port=args.port, timeout_keep_alive=3600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
