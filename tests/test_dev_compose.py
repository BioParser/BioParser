"""The dev overlay is opt-in. The default Compose file stays the local OKD stack."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

_NEEDS_POSIX = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None, reason="needs a POSIX shell"
)

_ROOT = Path(__file__).resolve().parents[1]


def _services(name: str) -> dict[str, Any]:
    loaded = yaml.safe_load((_ROOT / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    services = loaded["services"]
    assert isinstance(services, dict)
    return services


def _image_after(prefix: str, line: str) -> str:
    start = line.index(prefix)
    return line[start:].split(" AS ", 1)[0].strip()


def test_example_env_does_not_select_the_dev_overlay() -> None:
    for line in (_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("COMPOSE_FILE="):
            assert "docker-compose.dev.yml" not in line


def test_base_compose_has_no_dev_services() -> None:
    services = _services("docker-compose.yml")
    assert "frontend" not in services


def _run_wrapper(
    tmp_path: Path,
    env_text: str | None,
    *args: str,
    shell_compose: str | None = None,
) -> subprocess.CompletedProcess[str]:
    script = tmp_path / "bioparser-compose"
    script.write_bytes((_ROOT / "bioparser-compose").read_bytes())
    script.chmod(0o755)
    if env_text is not None:
        (tmp_path / ".env").write_text(env_text, encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    out = tmp_path / "docker-out"
    fake = bin_dir / "docker"
    fake.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$COMPOSE_FILE" > "$BIOPARSER_FAKE_DOCKER_OUT"\n'
        'printf "%s\\n" "$*" >> "$BIOPARSER_FAKE_DOCKER_OUT"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ.copy()
    env.pop("COMPOSE_FILE", None)
    env.pop("COMPOSE_PATH_SEPARATOR", None)
    env.pop("STACK", None)
    if shell_compose is not None:
        env["COMPOSE_FILE"] = shell_compose
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["BIOPARSER_FAKE_DOCKER_OUT"] = str(out)
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@_NEEDS_POSIX
def test_compose_wrapper_appends_the_overlay_and_does_not_edit_env(tmp_path: Path) -> None:
    env_text = "COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml\n"
    result = _run_wrapper(tmp_path, env_text, "up", "--build")
    assert result.returncode == 0, result.stderr
    recorded = (tmp_path / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded == [
        "docker-compose.yml:docker-compose.gpu.yml:docker-compose.dev.yml",
        "compose up --build",
    ]
    assert (tmp_path / ".env").read_text(encoding="utf-8") == env_text


@_NEEDS_POSIX
def test_compose_wrapper_strips_quotes_and_does_not_repeat_the_overlay(tmp_path: Path) -> None:
    quoted = [
        '"docker-compose.yml:docker-compose.cpu.yml"\n',
        "'docker-compose.yml:docker-compose.cpu.yml'\n",
    ]
    for index, env_value in enumerate(quoted):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        result = _run_wrapper(case, f"COMPOSE_FILE={env_value}", "config")
        assert result.returncode == 0, result.stderr
        recorded = (case / "docker-out").read_text(encoding="utf-8").splitlines()
        assert recorded[0] == "docker-compose.yml:docker-compose.cpu.yml:docker-compose.dev.yml"
        assert (case / ".env").read_text(encoding="utf-8") == f"COMPOSE_FILE={env_value}"

    already = tmp_path / "already"
    already.mkdir()
    env_text = "COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml\n"
    result = _run_wrapper(already, env_text, "down")
    assert result.returncode == 0, result.stderr
    recorded = (already / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "docker-compose.yml:docker-compose.dev.yml"
    assert recorded[1] == "compose down"
    assert (already / ".env").read_text(encoding="utf-8") == env_text


@_NEEDS_POSIX
def test_compose_wrapper_follows_compose_env_rules(tmp_path: Path) -> None:
    last_wins = tmp_path / "last"
    last_wins.mkdir()
    env_text = "COMPOSE_FILE=docker-compose.yml\nexport COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml # note\n"
    result = _run_wrapper(last_wins, env_text, "config")
    assert result.returncode == 0, result.stderr
    recorded = (last_wins / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "docker-compose.yml:docker-compose.cpu.yml:docker-compose.dev.yml"
    assert (last_wins / ".env").read_text(encoding="utf-8") == env_text

    shell = tmp_path / "shell"
    shell.mkdir()
    result = _run_wrapper(
        shell,
        "COMPOSE_FILE=docker-compose.yml\n",
        "config",
        shell_compose="docker-compose.yml:docker-compose.gpu.yml",
    )
    assert result.returncode == 0, result.stderr
    recorded = (shell / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "docker-compose.yml:docker-compose.gpu.yml:docker-compose.dev.yml"


@_NEEDS_POSIX
def test_compose_wrapper_rejects_expansion_in_the_env_file(tmp_path: Path) -> None:
    cases = [
        "STACK=b.yml\nCOMPOSE_FILE=$STACK\n",
        "COMPOSE_FILE=${NOT_SET_ZZ:-b.yml}\n",
        "COMPOSE_FILE='$STACK'\n",
        'COMPOSE_FILE="a\\nb.yml"\n',
        "COMPOSE_PATH_SEPARATOR=$SEP\nCOMPOSE_FILE=a.yml\n",
    ]
    for index, env_text in enumerate(cases):
        case = tmp_path / f"case-{index}"
        case.mkdir()
        result = _run_wrapper(case, env_text, "config")
        assert result.returncode == 1, result.stderr
        assert "contains $ or \\" in result.stderr
        assert not (case / "docker-out").exists()

    shell = tmp_path / "shell"
    shell.mkdir()
    result = _run_wrapper(
        shell,
        "COMPOSE_FILE=$STACK\n",
        "config",
        shell_compose="docker-compose.yml",
    )
    assert result.returncode == 0, result.stderr
    recorded = (shell / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "docker-compose.yml:docker-compose.dev.yml"


@_NEEDS_POSIX
def test_compose_wrapper_accepts_compose_whitespace_and_separator(tmp_path: Path) -> None:
    spaced = tmp_path / "spaced"
    spaced.mkdir()
    result = _run_wrapper(spaced, "COMPOSE_FILE = b.yml\n", "config")
    assert result.returncode == 0, result.stderr
    recorded = (spaced / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "b.yml:docker-compose.dev.yml"

    exported = tmp_path / "exported"
    exported.mkdir()
    result = _run_wrapper(exported, "export\tCOMPOSE_FILE=b.yml\n", "config")
    assert result.returncode == 0, result.stderr
    recorded = (exported / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "b.yml:docker-compose.dev.yml"

    separated = tmp_path / "separated"
    separated.mkdir()
    result = _run_wrapper(
        separated,
        "COMPOSE_PATH_SEPARATOR=;\nCOMPOSE_FILE=a.yml;b.yml\n",
        "config",
    )
    assert result.returncode == 0, result.stderr
    recorded = (separated / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "a.yml;b.yml;docker-compose.dev.yml"

    tab_hash = tmp_path / "tab-hash"
    tab_hash.mkdir()
    result = _run_wrapper(tab_hash, "COMPOSE_FILE=b.yml\t# comment\n", "config")
    assert result.returncode == 0, result.stderr
    recorded = (tab_hash / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "b.yml\t# comment:docker-compose.dev.yml"


@_NEEDS_POSIX
def test_compose_wrapper_reads_env_file_and_adds_file_flag(tmp_path: Path) -> None:
    other = tmp_path / "other.env"
    other.write_text("COMPOSE_FILE=b.yml\n", encoding="utf-8")
    result = _run_wrapper(tmp_path, None, "--env-file", "other.env", "config")
    assert result.returncode == 0, result.stderr
    recorded = (tmp_path / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded == [
        "b.yml:docker-compose.dev.yml",
        "compose --env-file other.env config",
    ]

    both = tmp_path / "both"
    both.mkdir()
    (both / "other.env").write_text("COMPOSE_FILE=b.yml\n", encoding="utf-8")
    result = _run_wrapper(both, "COMPOSE_FILE=a.yml\n", "--env-file", "other.env", "config")
    assert result.returncode == 0, result.stderr
    recorded = (both / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[0] == "b.yml:docker-compose.dev.yml"

    flagged = tmp_path / "flagged"
    flagged.mkdir()
    result = _run_wrapper(flagged, "COMPOSE_FILE=a.yml\n", "-f", "a.yml", "config")
    assert result.returncode == 0, result.stderr
    recorded = (flagged / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[1] == "compose -f a.yml -f docker-compose.dev.yml config"

    present = tmp_path / "present"
    present.mkdir()
    result = _run_wrapper(present, "COMPOSE_FILE=a.yml\n", "-f", "docker-compose.dev.yml", "config")
    assert result.returncode == 0, result.stderr
    recorded = (present / "docker-out").read_text(encoding="utf-8").splitlines()
    assert recorded[1] == "compose -f docker-compose.dev.yml config"


@_NEEDS_POSIX
def test_compose_wrapper_says_when_compose_file_is_empty(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, None, "config", shell_compose="")
    assert result.returncode == 1
    assert "COMPOSE_FILE is empty" in result.stderr
    assert not (tmp_path / "docker-out").exists()


@_NEEDS_POSIX
def test_compose_wrapper_refuses_a_missing_env(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, None, "up")
    assert result.returncode == 1
    assert ".env is missing" in result.stderr
    assert not (tmp_path / "docker-out").exists()


def _stage_names(text: str) -> list[str]:
    return [
        line.rsplit(" AS ", 1)[1]
        for line in text.splitlines()
        if line.startswith("FROM ") and " AS " in line
    ]


def test_dev_overlay_reloads_edited_code_only() -> None:
    api_dockerfile = (_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    frontend_dockerfile = (_ROOT / "docker" / "frontend.Dockerfile").read_text(encoding="utf-8")
    api_node = next(
        _image_after("node:", line) for line in api_dockerfile.splitlines() if " AS ui" in line
    )
    frontend_node = next(
        line.removeprefix("FROM ")
        for line in frontend_dockerfile.splitlines()
        if line.startswith("FROM node:")
    )
    assert frontend_node == api_node
    nvmrc = (_ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    assert api_node.startswith(f"node:{nvmrc}-slim@")
    services = _services("docker-compose.dev.yml")

    frontend = services["frontend"]
    assert frontend["image"] == "bioparser-frontend-dev"
    assert frontend["build"]["dockerfile"] == "docker/frontend.Dockerfile"
    assert "command" not in frontend
    assert "USER node" in frontend_dockerfile
    assert "npm ci --no-audit --no-fund" in frontend_dockerfile
    assert frontend["init"] is True
    assert frontend["networks"] == ["dev-api", "frontend-publish"]
    assert frontend["environment"]["VITE_GRAFANA_URL"] == "http://localhost:${GRAFANA_PORT:-3000}"
    publish = yaml.safe_load((_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
    assert publish["networks"]["dev-api"]["internal"] is True
    assert (
        publish["networks"]["frontend-publish"]["driver_opts"][
            "com.docker.network.bridge.enable_ip_masquerade"
        ]
        == "false"
    )
    assert frontend["ports"] == ["127.0.0.1:5173:5173"]
    assert frontend["environment"]["BIOPARSER_DEV_API_ORIGIN"] == "http://bioparser:8080"
    assert frontend["volumes"] == [
        "./frontend/src:/frontend/src:ro",
        "./frontend/index.html:/frontend/index.html:ro",
        "./frontend/vite.config.ts:/frontend/vite.config.ts:ro",
    ]

    assert "command" not in services["bioparser"]
    assert "environment" not in services["bioparser"]
    assert services["bioparser"]["build"]["target"] == "api-dev"
    assert services["bioparser"]["image"] == "bioparser-api-dev"
    assert services["bioparser"]["networks"] == ["dev-api"]
    assert services["bioparser"]["volumes"] == ["./src:/src:ro"]
    api_stages = _stage_names(api_dockerfile)
    assert api_stages[-1] == "runtime"
    assert api_stages.index("api-dev") < api_stages.index("runtime")
    api_dev = api_dockerfile.split("FROM base AS api-dev\n", 1)[1].split(
        "FROM base AS runtime\n", 1
    )[0]
    assert "PYTHONPATH=/src" in api_dev
    assert '--reload-dir", "/src"' in api_dev

    for name, module in (
        ("parser-worker", "python -m bioparser.worker.parse"),
        ("extractor-worker", "python -m bioparser.worker.extract"),
    ):
        assert "command" not in services[name]
        assert "environment" not in services[name]
        assert services[name]["volumes"] == ["./src:/src:ro"]
        assert services[name]["build"]["target"] == f"{name}-dev"
        assert services[name]["image"] == f"bioparser-{name}-dev"
        dockerfile = (_ROOT / "docker" / f"{name}.Dockerfile").read_text(encoding="utf-8")
        stages = _stage_names(dockerfile)
        assert stages[-1] == "runtime"
        assert stages.index(f"{name}-dev") < stages.index("runtime")
        prod, dev = dockerfile.split(f"FROM base AS {name}-dev\n", 1)
        assert "--group dev-reload" not in prod
        assert "--group dev-reload" in dev
        assert "PYTHONPATH=/src" in dev
        assert "watchfiles" in dev
        assert module in dev
        runtime = dev.split("FROM base AS runtime\n", 1)[1]
        assert "PYTHONPATH" not in runtime
        assert "watchfiles" not in runtime

    assert "command" not in services["migrate"]
    assert "environment" not in services["migrate"]
    assert services["migrate"]["build"]["target"] == "migrate-dev"
    assert services["migrate"]["image"] == "bioparser-migrate-dev"
    assert services["migrate"]["volumes"] == ["./src:/src:ro"]
    migrate = (_ROOT / "docker" / "migrate.Dockerfile").read_text(encoding="utf-8")
    migrate_stages = _stage_names(migrate)
    assert migrate_stages[-1] == "runtime"
    assert migrate_stages.index("migrate-dev") < migrate_stages.index("runtime")
    migrate_dev = migrate.split("FROM base AS migrate-dev\n", 1)[1].split(
        "FROM base AS runtime\n", 1
    )[0]
    assert "PYTHONPATH=/src" in migrate_dev
    assert "watchfiles" not in migrate_dev

    for left_alone in (
        "mineru",
        "vllm",
        "postgres",
        "redis",
        "loki",
        "alloy",
        "grafana",
        "traefik",
    ):
        assert left_alone not in services
