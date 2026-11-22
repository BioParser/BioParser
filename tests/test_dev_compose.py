"""The dev overlay is opt-in. The default Compose file stays the local OKD stack."""

import os
import subprocess
from pathlib import Path
from typing import Any

import yaml

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
    tmp_path: Path, env_text: str | None, *args: str
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
    services = _services("docker-compose.dev.yml")

    frontend = services["frontend"]
    assert frontend["image"] == "bioparser-frontend-dev"
    assert frontend["build"]["dockerfile"] == "docker/frontend.Dockerfile"
    assert "command" not in frontend
    assert "USER node" in frontend_dockerfile
    assert "npm ci --no-audit --no-fund" in frontend_dockerfile
    assert frontend["init"] is True
    assert frontend["networks"] == ["backend", "frontend-publish"]
    publish = yaml.safe_load((_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8"))
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
    assert "frontend-node-modules" not in (_ROOT / "docker-compose.dev.yml").read_text(
        encoding="utf-8"
    )

    assert "command" not in services["bioparser"]
    assert "environment" not in services["bioparser"]
    assert services["bioparser"]["build"]["target"] == "api-dev"
    assert services["bioparser"]["image"] == "bioparser-api-dev"
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
        assert "build-dev" not in dockerfile
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
    assert "install-watchfiles" not in services

    lock = (_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "watchfiles"\nversion = "1.2.0"' in lock

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
