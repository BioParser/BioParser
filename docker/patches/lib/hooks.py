"""Apply source hooks to installed Python packages.

A patch is a ``Feature``: a runtime module plus the hooks that call it. A hook swaps one
exact piece of installed source for a few lines. The runtime module holds the logic, so
it is plain Python that tests import. This file knows no package and no feature.

Applying a feature twice does nothing. A hook whose source has moved stops the script, so
a new release never gets half a patch.
"""

from __future__ import annotations

import ast
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple


class Hook(NamedTuple):
    """Replace ``old`` with ``new`` once in ``path``, a file under site-packages."""

    path: str
    old: str
    new: str


class Feature(NamedTuple):
    """One patch: a runtime module copied into site-packages, and the hooks that call it."""

    name: str
    # The runtime module's name. Its source is ``source / f"{module}.py"``.
    module: str
    source: Path
    hooks: tuple[Hook, ...]
    # A reason to a list of files that must exist, so a wrong version fails clearly.
    required: dict[str, tuple[str, ...]]


def apply_hook(site_packages: Path, hook: Hook) -> None:
    path = site_packages / hook.path
    if not path.is_file():
        raise SystemExit(f"patch does not match {path}. The file is missing.")
    text = path.read_text(encoding="utf-8")
    if hook.new in text:
        return
    if hook.old not in text:
        raise SystemExit(
            f"patch does not match {path}. The installed file is not the pinned release."
        )
    path.write_text(text.replace(hook.old, hook.new, 1), encoding="utf-8")


def ensure_import(path: Path, module: str) -> None:
    """Add ``import module`` above the first top-level import that is not ``__future__``.

    Nothing is added to a file that never calls the module.
    """
    line = f"import {module}\n"
    text = path.read_text(encoding="utf-8")
    if line in text or f"{module}." not in text:
        return
    for node in ast.parse(text).body:
        is_future = isinstance(node, ast.ImportFrom) and node.module == "__future__"
        if isinstance(node, (ast.Import, ast.ImportFrom)) and not is_future:
            lines = text.splitlines(keepends=True)
            lines.insert(node.lineno - 1, line)
            path.write_text("".join(lines), encoding="utf-8")
            return
    raise SystemExit(f"patch does not match {path}. It has no import to anchor on.")


def apply_feature(site_packages: Path, feature: Feature) -> None:
    for reason, files in feature.required.items():
        for relative in files:
            if not (site_packages / relative).is_file():
                raise SystemExit(
                    f"patch {feature.name} does not match {site_packages / relative}. {reason}."
                )
    shutil.copyfile(feature.source / f"{feature.module}.py", site_packages / f"{feature.module}.py")
    for hook in feature.hooks:
        apply_hook(site_packages, hook)
    for relative in dict.fromkeys(hook.path for hook in feature.hooks):
        ensure_import(site_packages / relative, feature.module)


def apply_features(site_packages: Path, features: Iterable[Feature]) -> None:
    for feature in features:
        apply_feature(site_packages, feature)
