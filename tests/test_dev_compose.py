"""The dev overlay is opt-in. The default Compose file stays the local OKD stack."""

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
    assert "ui" not in services


def test_compose_wrapper_appends_the_overlay_and_does_not_edit_env() -> None:
    script = (_ROOT / "bioparser-compose").read_text(encoding="utf-8")
    assert "exec docker compose up --build" in script
    assert "exec docker compose" in script
    assert "docker-compose.dev.yml" in script
    assert "5173" in script
    assert ">>" not in script
    assert script.startswith("#!/bin/sh\n")


def _stage_names(text: str) -> list[str]:
    return [
        line.rsplit(" AS ", 1)[1]
        for line in text.splitlines()
        if line.startswith("FROM ") and " AS " in line
    ]


def test_dev_overlay_reloads_edited_code_only() -> None:
    api_dockerfile = (_ROOT / "docker" / "api.Dockerfile").read_text(encoding="utf-8")
    node_image = next(
        _image_after("node:", line) for line in api_dockerfile.splitlines() if " AS ui" in line
    )
    services = _services("docker-compose.dev.yml")

    ui = services["ui"]
    assert ui["image"] == node_image
    assert ui["ports"] == ["127.0.0.1:5173:5173"]
    assert ui["environment"]["BIOPARSER_DEV_API_ORIGIN"] == "http://bioparser:8080"
    assert "npm run dev -- --host 0.0.0.0 --port 5173" in ui["command"][-1]
    assert "./frontend:/frontend" in ui["volumes"]
    assert "ui-node-modules:/frontend/node_modules" in ui["volumes"]

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
        prod, dev = dockerfile.split("FROM base AS build-dev\n", 1)
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
