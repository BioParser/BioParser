"""The script that makes the secret files for Docker Compose."""

import ast
import os
import re
import runpy
import stat
import sys
from pathlib import Path

import pytest

from bioparser.scripts import generate_secrets
from bioparser.scripts.generate_secrets import (
    DIRECTORY_VARIABLE,
    EXTERNAL,
    GENERATED,
    make_secrets,
    secrets_directory,
)


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


def test_the_directory_is_the_one_that_compose_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """${BIOPARSER_SECRETS_DIR:-./secrets}: the shell wins, then the last value in .env."""
    monkeypatch.delenv(DIRECTORY_VARIABLE, raising=False)
    env_file = tmp_path / ".env"
    assert secrets_directory(env_file) == Path("secrets")

    env_file.write_text(
        "# BIOPARSER_SECRETS_DIR=./commented\n"
        "BIOPARSER_SECRETS_DIR=./first\n"
        "export\tBIOPARSER_SECRETS_DIR = '../shared secrets' # note\n",
        encoding="utf-8",
    )
    assert secrets_directory(env_file) == Path("../shared secrets")
    env_file.write_text("BIOPARSER_SECRETS_DIR=~/bioparser-secrets # note\n", encoding="utf-8")
    assert secrets_directory(env_file) == Path.home() / "bioparser-secrets"
    env_file.write_text("BIOPARSER_SECRETS_DIR=\n", encoding="utf-8")
    assert secrets_directory(env_file) == Path("secrets")

    env_file.write_text("BIOPARSER_SECRETS_DIR=./from-env-file\n", encoding="utf-8")
    monkeypatch.setenv(DIRECTORY_VARIABLE, "/srv/bioparser-secrets")
    assert secrets_directory(env_file) == Path("/srv/bioparser-secrets")
    monkeypatch.setenv(DIRECTORY_VARIABLE, "")  # set and empty: the default, not .env
    assert secrets_directory(env_file) == Path("secrets")


def test_an_expansion_or_an_open_quote_in_env_stops_the_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Compose expands ${HOME} in .env. The script does not, so it does not guess a path."""
    monkeypatch.delenv(DIRECTORY_VARIABLE, raising=False)
    env_file = tmp_path / ".env"
    for value in ("${HOME}/secrets", "'$HOME'", '"secrets\\new"'):
        env_file.write_text(f"BIOPARSER_SECRETS_DIR={value}\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="Write a literal value"):
            secrets_directory(env_file)
    env_file.write_text("BIOPARSER_SECRETS_DIR='secrets\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="unterminated quote"):
        secrets_directory(env_file)

    monkeypatch.setenv(DIRECTORY_VARIABLE, "/srv/bioparser-secrets")  # .env is not read
    assert secrets_directory(env_file) == Path("/srv/bioparser-secrets")


def test_the_script_writes_to_a_shared_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """For example one directory for many Git worktrees. The script makes the missing parents."""
    shared = tmp_path / "shared" / "bioparser-secrets"
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    monkeypatch.setenv(DIRECTORY_VARIABLE, str(shared))

    runpy.run_path(generate_secrets.__file__, run_name="__main__")

    assert sorted(path.name for path in shared.iterdir()) == sorted(GENERATED + EXTERNAL)
    assert not (checkout / "secrets").exists()


def test_the_script_imports_only_the_standard_library() -> None:
    """The python3 of the host runs it without the packages of the project."""
    nodes = list(ast.walk(ast.parse(Path(generate_secrets.__file__).read_text(encoding="utf-8"))))
    modules = {a.name for node in nodes if isinstance(node, ast.Import) for a in node.names}
    modules |= {node.module for node in nodes if isinstance(node, ast.ImportFrom) and node.module}
    assert {module.split(".")[0] for module in modules} <= sys.stdlib_module_names
