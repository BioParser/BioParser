"""Each image syncs only its dependency group, then its process can import."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# Module imported after that image's group is installed.
_ENTRYPOINTS = {
    "api": "bioparser.api.app",
    "parser-worker": "bioparser.worker.parse",
    "extractor-worker": "bioparser.worker.extract",
    "migrate": "bioparser.db.migrate",
}

# The API constructs settings while the app module is imported.
_API_ENV = {
    "BIOPARSER_ARTIFACT_STORAGE_PATH": "/tmp/bioparser-artifacts",
    "BIOPARSER_REDIS_URL": "redis://localhost:6379/0",
    "BIOPARSER_DATABASE_URL": "postgresql://bioparser:secret@localhost:5432/bioparser",
}

_FLAGS = ("--locked", "--no-editable", "--no-default-groups")

_REVISION_CHECK = """
from pathlib import Path
import bioparser.db.migrate as migrate
versions = Path(migrate.__file__).resolve().parent / "migrations" / "versions"
revisions = [path for path in versions.glob("*.py") if path.name != "__init__.py"]
if not revisions:
    raise SystemExit("migration revisions are not in the installed package")
"""


def _dockerfile(group: str) -> Path:
    return _REPO / "docker" / f"{group}.Dockerfile"


def _sync_commands(text: str) -> list[list[str]]:
    return [line.split() for line in text.splitlines() if line.strip().startswith("uv sync ")]


def _command_modules(text: str) -> list[str]:
    """Modules the image command names. The process has to be able to import them."""
    line = next(line for line in text.splitlines() if line.startswith("CMD "))
    command = json.loads(line.removeprefix("CMD "))
    if not isinstance(command, list):
        raise TypeError(line)
    modules: list[str] = []
    for token in command:
        if not isinstance(token, str):
            raise TypeError(line)
        name = token.split(":", 1)[0]
        parts = name.split(".")
        if name != "python" and all(part.isidentifier() for part in parts):
            modules.append(name)
    return list(dict.fromkeys(modules))


@pytest.mark.parametrize("group", _ENTRYPOINTS)
def test_image_dockerfile_syncs_its_own_group(group: str) -> None:
    commands = _sync_commands(_dockerfile(group).read_text(encoding="utf-8"))
    assert len(commands) == 2
    dependency_layer, project_install = commands
    assert "--no-install-project" in dependency_layer
    assert "--no-install-project" not in project_install
    for command in commands:
        assert command[:2] == ["uv", "sync"]
        for flag in _FLAGS:
            assert flag in command
        assert "--only-group" not in command
        assert command[command.index("--group") + 1] == group


def test_image_commands_name_their_modules() -> None:
    modules = {
        group: _command_modules(_dockerfile(group).read_text(encoding="utf-8"))
        for group in _ENTRYPOINTS
    }
    assert modules["api"] == ["uvicorn", "bioparser.api.app"]
    assert modules["parser-worker"] == ["bioparser.worker.parse"]
    assert modules["extractor-worker"] == ["bioparser.worker.extract"]
    assert modules["migrate"] == []


def test_api_image_starts_the_app_without_importing_it_at_build() -> None:
    text = _dockerfile("api").read_text(encoding="utf-8")
    assert "bioparser.api.app:app" in text
    assert "import bioparser" not in text


@pytest.mark.image
@pytest.mark.skipif(sys.platform == "win32", reason="image installs are checked on linux")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
@pytest.mark.parametrize("group", _ENTRYPOINTS)
def test_synced_image_group_imports_its_entrypoint(group: str, tmp_path: Path) -> None:
    text = _dockerfile(group).read_text(encoding="utf-8")
    commands = _sync_commands(text)
    project_install = [command for command in commands if "--no-install-project" not in command]
    assert len(project_install) == 1
    env = os.environ.copy()
    env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path)
    subprocess.run(
        [*project_install[0], "--reinstall-package", "bioparser"],
        cwd=_REPO,
        env=env,
        check=True,
    )
    import_env = os.environ.copy()
    if group == "api":
        import_env.update(_API_ENV)
    python = tmp_path / "bin" / "python"
    modules = dict.fromkeys([_ENTRYPOINTS[group], *_command_modules(text)])
    statement = "\n".join(f"import {module}" for module in modules)
    subprocess.run([python, "-c", statement], env=import_env, check=True)
    if group == "migrate":
        subprocess.run([python, "-c", _REVISION_CHECK], check=True)
