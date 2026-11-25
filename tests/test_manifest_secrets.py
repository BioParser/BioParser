"""The OKD manifests give each pod only its own secrets, as files in /run/secrets."""

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

MANIFESTS = Path(__file__).resolve().parents[1] / "manifests"
# The local copies that manifests/README.md tells you to make. Git ignores them.
LOCAL_COPIES = {"bioparser-configmap.yaml", "bioparser-secret.yaml"}
# The files in /run/secrets of each Deployment, and their keys in the Secret bioparser-secret.
SECRETS_OF = {
    "bioparser-api-dep": {
        "bioparser_redis_password": "REDIS_PASSWORD",
        "bioparser_database_password": "POSTGRES_APP_PASSWORD",
        "bioparser_api_key": "BIOPARSER_API_KEY",
    },
    "redis": {"redis_password": "REDIS_PASSWORD"},
    "vllm-server": {"hf_token": "HF_TOKEN"},
}


def _objects(kind: str) -> list[dict[str, Any]]:
    objects: list[Any] = []
    for path in sorted(MANIFESTS.rglob("*.yaml")):
        if path.name not in LOCAL_COPIES:
            objects += yaml.safe_load_all(path.read_text(encoding="utf-8"))
    return [item for item in objects if item and item["kind"] == kind]


def _pods() -> dict[str, dict[str, Any]]:
    return {d["metadata"]["name"]: d["spec"]["template"]["spec"] for d in _objects("Deployment")}


def _is_secret_name(name: str) -> bool:
    """A name that ends in _FILE or _PATH holds a path, for example HF_TOKEN_PATH."""
    words = ("PASSWORD", "SECRET", "TOKEN", "API_KEY")
    return any(word in name for word in words) and not name.endswith(("_FILE", "_PATH"))


def test_each_pod_mounts_only_its_own_secret_files() -> None:
    pods = _pods()
    assert pods.keys() == SECRETS_OF.keys()
    for name, pod in pods.items():
        # The Kubernetes API token: no pod uses it, and its mount is in /run/secrets.
        assert pod["automountServiceAccountToken"] is False, name
        [secret] = [volume["secret"] for volume in pod["volumes"] if "secret" in volume]
        assert secret["secretName"] == "bioparser-secret", name
        assert secret["defaultMode"] == 0o440, name
        assert {item["path"]: item["key"] for item in secret["items"]} == SECRETS_OF[name], name
        [container] = pod["containers"]
        mounts = [mount for mount in container["volumeMounts"] if mount["name"] == "secrets"]
        assert mounts == [{"mountPath": "/run/secrets", "name": "secrets", "readOnly": True}]


def test_no_secret_is_in_an_environment_variable_a_command_or_the_configmap() -> None:
    for name, pod in _pods().items():
        for container in [*pod.get("initContainers", []), *pod["containers"]]:
            env = container.get("env", [])
            assert [var["name"] for var in env if _is_secret_name(var["name"])] == [], name
            assert [var for var in env if "secretKeyRef" in var.get("valueFrom", {})] == [], name
            sources = container.get("envFrom", [])
            assert [source for source in sources if "secretRef" in source] == [], name
            command = [*container.get("command", []), *container.get("args", [])]
            assert "--requirepass" not in " ".join(command), name
    [config] = _objects("ConfigMap")
    assert [key for key in config["data"] if _is_secret_name(key)] == []
    for key in ("BIOPARSER_REDIS_URL", "BIOPARSER_DATABASE_URL"):
        assert urlsplit(config["data"][key]).password is None, key
    assert _objects("Secret") == []  # the Secret comes from files, not from Git


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="needs a POSIX shell"
)
def test_redis_gets_its_password_only_on_stdin(tmp_path: Path) -> None:
    """The start script of Redis, with a stand-in redis-server that writes its input."""
    [container] = _pods()["redis"]["containers"]
    script = container["args"][0].replace("/run/secrets/", f"{tmp_path}/")
    stand_in = tmp_path / "redis-server"
    stand_in.write_text('#!/bin/sh\necho "args: $*"\ncat\n', encoding="utf-8")
    stand_in.chmod(0o700)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "LC_ALL": "C", "REDIS_MAX_MEMORY": "512mb"}

    def start(password: str) -> subprocess.CompletedProcess[str]:
        (tmp_path / "redis_password").write_text(password, encoding="utf-8")
        command = [*container["command"], script]
        return subprocess.run(command, env=env, capture_output=True, text=True, check=False)

    started = start("0a1b2c3d\n")
    assert started.returncode == 0, started.stderr
    args, config = started.stdout.splitlines()
    # "-" first: redis-server reads its config on stdin.
    assert args.startswith("args: - --appendonly yes ")
    assert "0a1b2c3d" not in args
    assert config == 'requirepass "0a1b2c3d"'
    for bad in ("", "canary value", 'canary"value', "canary\\value", "canary\nvalue", "canaryé"):
        stopped = start(bad)
        # redis-server did not start, and the message does not show the value.
        assert (stopped.returncode, stopped.stdout) == (1, ""), repr(bad)
        assert "canary" not in stopped.stderr
