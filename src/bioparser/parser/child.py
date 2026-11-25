"""A parser child process the worker can kill.

The child does not inherit the database and Redis passwords. This is not a
sandbox: code in the child runs as the same user and can still read them from
/proc/<pid>/environ. #286 limits what a stolen password can do.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

LOGGER = logging.getLogger(__name__)

# Names a child needs to start. Windows needs SYSTEMROOT, PATHEXT, and the temp
# directories. The rest are the process path and the locale.
CHILD_ENV = ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "PATHEXT", "TEMP", "TMP", "TMPDIR")


def child_environment() -> dict[str, str]:
    """The environment passed to a parser child."""
    return {name: os.environ[name] for name in CHILD_ENV if name in os.environ}


def configure_child_logging() -> None:
    """Log warnings to stderr. Library noise stays out of the worker log."""
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(name)s %(levelname)s %(message)s",
    )
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    logging.getLogger("pikepdf").setLevel(logging.ERROR)


async def run_child(module: str, *args: str) -> tuple[int | None, bytes]:
    """Run `python -m module`. Cancellation kills the process.

    Stderr is written to this process's log. The return is the exit status and
    stdout.
    """
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        module,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_environment(),
    )
    try:
        stdout, stderr = await child.communicate()
    except asyncio.CancelledError:
        if child.returncode is None:
            child.kill()
        await child.wait()
        raise
    text = stderr.decode("utf-8", "replace").strip()
    if text:
        LOGGER.warning("%s", text)
    return child.returncode, stdout
