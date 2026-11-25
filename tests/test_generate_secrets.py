"""The script that makes the secret files for Docker Compose."""

import ast
import os
import re
import stat
import sys
from pathlib import Path

import pytest

from bioparser.scripts import generate_secrets
from bioparser.scripts.generate_secrets import EXTERNAL, GENERATED, make_secrets


def test_it_makes_the_missing_files_and_keeps_the_others(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / "secrets"
    directory.mkdir(mode=0o755)
    (directory / "redis_password").write_text("kept", encoding="ascii")

    make_secrets(directory)

    values = {path.name: path.read_text(encoding="ascii") for path in directory.iterdir()}
    assert sorted(values) == sorted(GENERATED + EXTERNAL)
    assert values.pop("redis_password") == "kept"
    assert values.pop("hf_token") == ""
    assert all(re.fullmatch(r"[0-9a-f]{64}", value) for value in values.values())
    assert len(set(values.values())) == len(values)
    output = capsys.readouterr().out
    assert "redis_password" not in output
    assert not any(value in output for value in values.values())
    if os.name != "nt":  # Windows has no POSIX file modes
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert stat.S_IMODE((directory / "postgres_password").stat().st_mode) == 0o644


@pytest.mark.skipif(os.name == "nt", reason="Windows has no POSIX file modes")
def test_a_file_that_you_wrote_gets_mode_0644_and_a_link_stays(tmp_path: Path) -> None:
    """The upgrade from .env: you write the old values, then run the script."""
    directory = tmp_path / "secrets"
    directory.mkdir()
    written = directory / "postgres_password"
    written.write_text("old-value", encoding="ascii")
    written.chmod(0o600)
    target = tmp_path / "elsewhere"
    target.write_text("linked-value", encoding="ascii")
    target.chmod(0o600)
    (directory / "redis_password").symlink_to(target)

    make_secrets(directory)

    assert written.read_text(encoding="ascii") == "old-value"
    assert stat.S_IMODE(written.stat().st_mode) == 0o644
    assert target.read_text(encoding="ascii") == "linked-value"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_the_script_imports_only_the_standard_library() -> None:
    """The python3 of the host runs it without the packages of the project."""
    nodes = list(ast.walk(ast.parse(Path(generate_secrets.__file__).read_text(encoding="utf-8"))))
    modules = {a.name for node in nodes if isinstance(node, ast.Import) for a in node.names}
    modules |= {node.module for node in nodes if isinstance(node, ast.ImportFrom) and node.module}
    assert {module.split(".")[0] for module in modules} <= sys.stdlib_module_names
