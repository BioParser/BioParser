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
    script = (_ROOT / "bioparser").read_text(encoding="utf-8")
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

    api = services["bioparser"]["command"]
    assert api == [
        "uvicorn",
        "bioparser.api.app:app",
        "--reload",
        "--reload-dir",
        "/src",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
    ]
    assert services["bioparser"]["environment"]["PYTHONPATH"] == "/src"
    assert "./src:/src:ro" in services["bioparser"]["volumes"]

    for name, module in (
        ("parser-worker", "python -m bioparser.worker.parse"),
        ("extractor-worker", "python -m bioparser.worker.extract"),
    ):
        command = services[name]["command"]
        assert command[1:3] == ["-m", "watchfiles"]
        assert module in command
        assert command[-1] == "/src"
        assert services[name]["environment"]["PYTHONPATH"] == "/src"
        assert services[name]["volumes"] == ["./src:/src:ro"]
        assert services[name]["build"]["target"] == f"{name}-dev"
        dockerfile = (_ROOT / "docker" / f"{name}.Dockerfile").read_text(encoding="utf-8")
        stages = _stage_names(dockerfile)
        assert stages[-1] == "runtime"
        assert stages.index(f"{name}-dev") < stages.index("runtime")
        prod, dev = dockerfile.split("FROM base AS build-dev\n", 1)
        assert "--group dev-reload" not in prod
        assert "--group dev-reload" in dev

    assert "command" not in services["migrate"]
    assert services["migrate"]["environment"]["PYTHONPATH"] == "/src"
    assert "./src:/src:ro" in services["migrate"]["volumes"]
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
