import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bioparser.parser.backend.mineru.schema import DOCVORTEX_VERSION, MINERU_VERSION
from bioparser.services.mineru.client import DEFAULT_TIER, MAX_WAIT_SECONDS

_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "mineru.Dockerfile"
_PIN = re.compile(r"mineru(?:\[(?:torch|full)\])?==([0-9]+\.[0-9]+\.[0-9]+)")
_INSTALL = re.compile(r"uv pip install [^\n]*\"mineru[^\n]*")
_DOCVORTEX_PIN = re.compile(r"\"docvortex==([0-9]+\.[0-9]+\.[0-9]+)\"")
_COMPOSE = [
    _DOCKERFILE.parents[1] / f"docker-compose{suffix}.yml" for suffix in ("", ".cpu", ".gpu")
]
_STAGE = re.compile(r"^FROM runtime AS (\w+)$(.*?)(?=^FROM |\Z)", re.MULTILINE | re.DOTALL)
_SERVED = re.compile(r'^CMD \["--tier", "([a-z]+)"', re.MULTILINE)
_VERIFIED = re.compile(r"models verify --tier ([a-z]+)")
_ENTRYPOINT = re.compile(r"^ENTRYPOINT (\[.*\])$", re.MULTILINE)
# A --tier is a ceiling. Model sets come in basic and standard; Advanced runs on Standard's.
_MODELS_FOR_TIER = {"basic": "basic", "standard": "standard", "advanced": "standard"}


def test_mineru_image_pins_the_schema_version() -> None:
    pins = set(_PIN.findall(_DOCKERFILE.read_text(encoding="utf-8")))
    assert pins == {MINERU_VERSION}


def test_each_mineru_install_pins_the_docvortex_the_fixtures_came_from() -> None:
    # mineru accepts a range of docvortex releases, and docvortex makes the middle.json.
    installs = _INSTALL.findall(_DOCKERFILE.read_text(encoding="utf-8"))
    assert len(installs) == 2  # cpu and gpu
    assert [_DOCVORTEX_PIN.findall(line) for line in installs] == [[DOCVORTEX_VERSION]] * 2


def test_mineru_fixtures_come_from_the_pinned_service() -> None:
    # The image, the mapper and the fixtures move together. A fixture from another
    # version would let the mapper tests pass against a document the service never sends.
    fixtures = sorted(_DOCKERFILE.parents[1].glob("tests/parser/fixtures/mineru-middle*.json"))
    assert fixtures
    for path in fixtures:
        producer = json.loads(path.read_text(encoding="utf-8"))["metadata"]["producer"]
        assert producer == {"name": "mineru", "version": MINERU_VERSION}, path.name


def test_each_mineru_target_serves_the_tiers_its_models_cover() -> None:
    # cpu serves the client's default tier. gpu serves up to Advanced, so Standard, the tier the
    # GPU stack asks for, and Advanced both run. Since MinerU 4.1 a Standard ceiling lacks Advanced.
    stages = dict(_STAGE.findall(_DOCKERFILE.read_text(encoding="utf-8")))
    served = {name: _SERVED.findall(body) for name, body in stages.items()}
    verified = {name: _VERIFIED.findall(body) for name, body in stages.items()}
    assert served == {"cpu": [DEFAULT_TIER], "gpu": ["advanced"]}
    assert verified == {
        name: [_MODELS_FOR_TIER[tier] for tier in tiers] for name, tiers in served.items()
    }


def test_compose_leaves_the_mineru_command_to_the_image() -> None:
    for path in _COMPOSE:
        assert "--tier" not in path.read_text(encoding="utf-8"), path.name


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="needs a POSIX shell"
)
def test_mineru_entrypoint_passes_each_setting_as_one_argument(tmp_path: Path) -> None:
    # The ENTRYPOINT's sh script, run against a mineru-api stub that prints its arguments.
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", "")
    tini, separator, shell, flag, script, argv0 = json.loads(_ENTRYPOINT.findall(dockerfile)[0])
    assert (tini, separator, shell, flag) == ("/usr/bin/tini", "--", "/bin/sh", "-c")
    stub = tmp_path / "mineru-api"
    stub.write_text("#!/bin/sh\nprintf '%s\\0' \"$@\"\n", encoding="utf-8")
    stub.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}",
        "MINERU_API_CONCURRENCY": "2 --allow-http-source",
        "MINERU_API_TASK_TIMEOUT": "3900 --api-key x",
    }
    result = subprocess.run(
        ["sh", "-c", script, argv0, "--tier", "basic"],
        env=env,
        capture_output=True,
        check=True,
    )
    args = result.stdout.decode().split("\0")[:-1]
    fixed = ["--host", "0.0.0.0", "--port", "8000", "--preload-models"]
    assert args == [
        *fixed,
        "--concurrency",
        "2 --allow-http-source",
        "--task-timeout",
        "3900 --api-key x",
        "--tier",
        "basic",
    ]


def test_mineru_stops_a_parse_only_after_the_client_gave_up_on_it() -> None:
    # A parse that reaches --task-timeout makes mineru-api refuse all work until it restarts,
    # so the client's longest wait (queue time included) must end first.
    [timeout] = re.findall(
        r"MINERU_API_TASK_TIMEOUT=(\d+)", _DOCKERFILE.read_text(encoding="utf-8")
    )
    assert int(timeout) > MAX_WAIT_SECONDS
