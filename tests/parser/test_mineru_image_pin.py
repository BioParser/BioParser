import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from bioparser.parser.backend.mineru.schema import MINERU_VERSION
from bioparser.services.mineru.client import DEFAULT_TIER

_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "mineru.Dockerfile"
_PIN = re.compile(r"mineru(?:\[(?:torch|full)\])?==([0-9]+\.[0-9]+\.[0-9]+)")
_COMPOSE = [
    _DOCKERFILE.parents[1] / f"docker-compose{suffix}.yml" for suffix in ("", ".cpu", ".gpu")
]
_STAGE = re.compile(r"^FROM runtime AS (\w+)$(.*?)(?=^FROM |\Z)", re.MULTILINE | re.DOTALL)
_SERVED = re.compile(r'^CMD \["--tier", "([a-z]+)"', re.MULTILINE)
_VERIFIED = re.compile(r"models verify --tier ([a-z]+)")
_ENTRYPOINT = re.compile(r"^ENTRYPOINT (\[.*\])$", re.MULTILINE)


def test_mineru_image_pins_the_schema_version() -> None:
    pins = set(_PIN.findall(_DOCKERFILE.read_text(encoding="utf-8")))
    assert pins == {MINERU_VERSION}


def test_each_mineru_target_serves_the_tier_it_bakes_in() -> None:
    # cpu serves the client's default tier; gpu serves Standard, the tier the GPU stack asks for.
    stages = dict(_STAGE.findall(_DOCKERFILE.read_text(encoding="utf-8")))
    served = {name: _SERVED.findall(body) for name, body in stages.items()}
    verified = {name: _VERIFIED.findall(body) for name, body in stages.items()}
    assert served == {"cpu": [DEFAULT_TIER], "gpu": ["standard"]}
    assert verified == served


def test_compose_leaves_the_mineru_command_to_the_image() -> None:
    for path in _COMPOSE:
        assert "--tier" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX shell")
def test_mineru_entrypoint_passes_the_concurrency_as_one_argument(tmp_path: Path) -> None:
    # The ENTRYPOINT's sh script, run against a mineru-api stub that prints its arguments.
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", "")
    tini, separator, shell, flag, script, argv0 = json.loads(_ENTRYPOINT.findall(dockerfile)[0])
    assert (tini, separator, shell, flag) == ("/usr/bin/tini", "--", "/bin/sh", "-c")
    stub = tmp_path / "mineru-api"
    stub.write_text("#!/bin/sh\nprintf '%s\\0' \"$@\"\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "MINERU_API_CONCURRENCY": "2 --allow-local-source",
    }
    result = subprocess.run(
        ["sh", "-c", script, argv0, "--tier", "basic"],
        env=env,
        capture_output=True,
        check=True,
    )
    args = result.stdout.decode().split("\0")[:-1]
    fixed = ["--host", "0.0.0.0", "--port", "8000", "--preload-models"]
    assert args == [*fixed, "--concurrency", "2 --allow-local-source", "--tier", "basic"]
