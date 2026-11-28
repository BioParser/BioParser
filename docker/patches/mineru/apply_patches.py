"""Patch the MinerU and DocVortex packages installed in the MinerU image.

    python apply_patches.py [site-packages]

With no argument the script patches the site-packages of the ``mineru`` it can import.
Each feature lives in its own directory next to this file. Add a feature to ``FEATURES``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE), str(_HERE.parent)]

from glyph_edges.hooks import FEATURE as GLYPH_EDGES
from lib.hooks import Feature, apply_features

FEATURES: tuple[Feature, ...] = (GLYPH_EDGES,)


def main() -> None:
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    else:
        import mineru

        root = Path(mineru.__file__).resolve().parent.parent
    apply_features(root, FEATURES)


if __name__ == "__main__":
    main()
