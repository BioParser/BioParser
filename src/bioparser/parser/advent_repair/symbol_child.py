"""Repair one PDF in a process the worker can kill.

The PDF path and the output path are the arguments. The unmapped count is one
line on stdout. Log lines go to stderr.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from pathlib import Path

from bioparser.parser.child import run_child

LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    """Repair the PDF named on the command line and write it to the output path."""
    from bioparser.parser.advent_repair.symbol_fonts import repair_pdf
    from bioparser.parser.child import configure_child_logging

    configure_child_logging()
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit(1)
    source = Path(args[0])
    target = Path(args[1])
    try:
        repaired, count = repair_pdf(source.read_bytes())
        target.write_bytes(repaired)
    except Exception:
        LOGGER.warning("symbol font repair left the PDF unchanged", exc_info=True)
        raise SystemExit(1) from None
    sys.stdout.buffer.write(f"{count}\n".encode())


async def repair_pdf_in_child(path: Path) -> tuple[bytes, int]:
    """Run the repair in a child process. Cancellation kills that process."""
    original = path.read_bytes()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        output = Path(handle.name)
    try:
        returncode, stdout = await run_child(
            "bioparser.parser.advent_repair.symbol_child", str(path), str(output)
        )
        if returncode != 0:
            LOGGER.warning("symbol font repair left the PDF unchanged")
            return original, 0
        text = stdout.decode("utf-8", "replace").strip()
        if not text.isdigit():
            LOGGER.warning("symbol font repair left the PDF unchanged")
            return original, 0
        try:
            repaired = output.read_bytes()
        except OSError:
            LOGGER.warning("symbol font repair left the PDF unchanged", exc_info=True)
            return original, 0
        return repaired, int(text)
    finally:
        output.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
