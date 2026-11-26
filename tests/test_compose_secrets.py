"""Compose gives each service only its own secrets, and puts no secret in the config.

`docker inspect` shows the environment, the command, the labels and the health check that
the rendered config gives a container. `docker compose config` needs only the Docker CLI.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(shutil.which("docker") is None, reason="needs the Docker CLI"),
]

ROOT = Path(__file__).resolve().parents[1]
FILES = ["docker-compose.yml", "", "docker-compose.logging.yml", "docker-compose.traefik.yml"]
# The variables of an old .env. Compose must not put their values anywhere.
OLD_NAMES = "REDIS_PASSWORD POSTGRES_PASSWORD POSTGRES_APP_PASSWORD BIOPARSER_API_KEY HF_TOKEN"
OLD_ENV = {
    name: f"canary-{name.lower()}" for name in [*OLD_NAMES.split(), "GRAFANA_ADMIN_PASSWORD"]
}
SECRETS_OF = {
    "bioparser": {"redis_password", "postgres_app_password", "bioparser_api_key"},
    "parser-worker": {"redis_password", "postgres_app_password"},
    "extractor-worker": {"redis_password", "postgres_app_password"},
    "redis": {"redis_password"},
    "postgres": {"postgres_password"},
    "migrate": {"postgres_password", "postgres_app_password"},
    "vllm": {"hf_token"},
    "grafana": {"grafana_admin_password"},
    "traefik": {"traefik_users"},
}


@pytest.fixture(scope="module", params=["cpu", "gpu"])
def config(request: pytest.FixtureRequest) -> dict[str, Any]:
    """The rendered config of the full stack, with an old .env in the environment."""
    command = ["docker", "compose", "--project-directory", str(ROOT)]
    command += ["--env-file", str(ROOT / ".env.example")]
    for name in FILES:
        command += ["-f", str(ROOT / (name or f"docker-compose.{request.param}.yml"))]
    env = os.environ | OLD_ENV | {"TRAEFIK_USERS_FILE": "docker/traefik/users.example"}
    result = subprocess.run(
        [*command, "config", "--format", "json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered: dict[str, Any] = json.loads(result.stdout)
    return rendered


def test_each_service_mounts_only_its_own_secrets(config: dict[str, Any]) -> None:
    mounted = {
        name: {secret["source"] for secret in service.get("secrets", [])}
        for name, service in config["services"].items()
    }
    assert {name: sources for name, sources in mounted.items() if sources} == SECRETS_OF


def test_no_secret_is_in_a_variable_or_a_command(config: dict[str, Any]) -> None:
    assert [name for name, value in OLD_ENV.items() if value in json.dumps(config)] == []
    found = []
    for name, service in config["services"].items():
        for variable, value in (service.get("environment") or {}).items():
            names_a_secret = re.search(r"PASSWORD|TOKEN|API_KEY|SECRET", variable)
            if names_a_secret and not variable.endswith(("_FILE", "_PATH")):
                found.append(f"{name}: {variable}")
            if re.search(r"://[^/@\s]*:[^/@\s]+@", str(value)):  # a URL with a password
                found.append(f"{name}: {variable}")
        arguments = [*(service.get("entrypoint") or []), *(service.get("command") or [])]
        arguments += (service.get("healthcheck") or {}).get("test") or []
        for argument in arguments:
            if re.search(r"--requirepass|\s-a\s|PGPASSWORD=", f" {argument} "):
                found.append(f"{name}: {argument[:40]}")
    assert found == []
