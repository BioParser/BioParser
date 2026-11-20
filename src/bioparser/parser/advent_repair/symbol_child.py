"""Repair one PDF in a process the worker can kill.

The PDF path and the output path are the arguments. Stdout is one line,
`success 3` or `skipped 0`. A failure exits 1 and leaves no output file the
parent should trust. Log lines go to stderr.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from pathlib import Path

from bioparser.parser.advent_repair.symbol_fonts import SymbolRepair, repair_pdf
from bioparser.parser.child import configure_child_logging, run_child
from bioparser.parser.models import RepairStatus

LOGGER = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    """Repair the PDF named on the command line and write it to the output path."""
    configure_child_logging()
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        raise SystemExit(1)
    source = Path(args[0])
    target = Path(args[1])
    repaired = repair_pdf(source.read_bytes())
    if repaired.status is RepairStatus.FAILED:
        raise SystemExit(1)
    target.write_bytes(repaired.data)
    line = f"{repaired.status.value} {repaired.count}\n"
    sys.stdout.buffer.write(line.encode())


async def repair_pdf_in_child(path: Path, *, original: bytes | None = None) -> SymbolRepair:
    """Run the repair in a child process. Cancellation kills that process.

    `original` is the PDF bytes when the caller already read them. The output
    file is read off the event loop.
    """
    if original is None:
        original = await asyncio.to_thread(path.read_bytes)
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        output = Path(handle.name)
    try:
        returncode, stdout = await run_child(
            "bioparser.parser.advent_repair.symbol_child", str(path), str(output)
        )
        parsed = _child_result(stdout)
        if returncode != 0 or parsed is None or parsed[0] is RepairStatus.FAILED:
            LOGGER.warning("symbol font repair left the PDF unchanged")
            return SymbolRepair(original, 0, RepairStatus.FAILED)
        status, count = parsed
        try:
            repaired = await asyncio.to_thread(output.read_bytes)
        except OSError:
            LOGGER.warning("symbol font repair left the PDF unchanged", exc_info=True)
            return SymbolRepair(original, 0, RepairStatus.FAILED)
        return SymbolRepair(repaired, count, status)
    finally:
        output.unlink(missing_ok=True)


def _child_result(stdout: bytes) -> tuple[RepairStatus, int] | None:
    parts = stdout.decode("utf-8", "replace").split()
    if len(parts) != 2 or not parts[1].isdigit():
        return None
    try:
        status = RepairStatus(parts[0])
    except ValueError:
        return None
    return status, int(parts[1])


if __name__ == "__main__":
    main()
