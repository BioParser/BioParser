"""Each image syncs only its dependency group, then its process can import."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

# Modules imported after that image's group is installed. The API image runs
# uvicorn, and the app module does not import it.
_ENTRYPOINTS = {
    "api": ("bioparser.api.app", "uvicorn"),
    "parser-worker": ("bioparser.worker.parse",),
    "extractor-worker": ("bioparser.worker.extract",),
    "migrate": ("bioparser.db.migrate",),
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


def test_api_image_starts_the_app_without_importing_it_at_build() -> None:
    text = _dockerfile("api").read_text(encoding="utf-8")
    assert "bioparser.api.app:app" in text
    assert "import bioparser" not in text


@pytest.mark.image
@pytest.mark.skipif(sys.platform == "win32", reason="image installs are checked on linux")
@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
@pytest.mark.parametrize("group", _ENTRYPOINTS)
def test_synced_image_group_imports_its_entrypoint(group: str, tmp_path: Path) -> None:
    commands = _sync_commands(_dockerfile(group).read_text(encoding="utf-8"))
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
    statement = "\n".join(f"import {module}" for module in _ENTRYPOINTS[group])
    subprocess.run([python, "-c", statement], env=import_env, check=True)
    if group == "migrate":
        subprocess.run([python, "-c", _REVISION_CHECK], check=True)
