"""The patches are written against the releases the backend expects."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "docker" / "patches" / "mineru"))

import versions

from bioparser.parser.backend.mineru.schema import DOCVORTEX_VERSION, MINERU_VERSION


def test_patch_versions_match_the_backend_pins() -> None:
    assert versions.MINERU_VERSION == MINERU_VERSION
    assert versions.DOCVORTEX_VERSION == DOCVORTEX_VERSION
