"""One pdfplumber parse. The PDF path is the only argument, and the artifact JSON is written to stdout."""

from __future__ import annotations

import sys
from pathlib import Path

# Exit status for a rejected document, and for a missing PDF.
# Any other non-zero status is a crash.
EXIT_UNSUPPORTED = 3
EXIT_NOT_FOUND = 4


def main(argv: list[str] | None = None) -> None:
    """Parse the PDF named on the command line. The artifact JSON goes to stdout."""
    from bioparser.parser.backend.default.parser import DefaultParser
    from bioparser.parser.errors import UnsupportedDocumentError

    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit(EXIT_NOT_FOUND)
    try:
        artifact = DefaultParser().parse_in_this_process(Path(args[0]))
    except FileNotFoundError as exc:
        sys.stdout.buffer.write(str(exc).encode())
        raise SystemExit(EXIT_NOT_FOUND) from exc
    except UnsupportedDocumentError as exc:
        sys.stdout.buffer.write(str(exc).encode())
        raise SystemExit(EXIT_UNSUPPORTED) from exc
    sys.stdout.buffer.write(artifact.model_dump_json().encode())


if __name__ == "__main__":
    main()
