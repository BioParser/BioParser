"""The image publish script builds the whole set before it moves any tag."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_NEEDS_POSIX = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="needs a POSIX shell",
)

_ROOT = Path(__file__).resolve().parents[1]
_SHA = "abc123"
_PREFIX = "ghcr.io/bioparser/bioparser"
_PUSHES = [
    f"push {_PREFIX}/parser-worker:{_SHA}",
    f"push {_PREFIX}/extractor-worker:{_SHA}",
    f"push {_PREFIX}/migrate:{_SHA}",
    f"push {_PREFIX}/mineru:{_SHA}-cpu",
    f"push {_PREFIX}/api:{_SHA}",
    f"push {_PREFIX}/parser-worker:latest",
    f"push {_PREFIX}/extractor-worker:latest",
    f"push {_PREFIX}/migrate:latest",
    f"push {_PREFIX}/mineru:cpu",
    f"push {_PREFIX}/api:latest",
]


def _fake_docker(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "docker"
    fake.write_text(
        """#!/usr/bin/env python3
import os
import sys

line = " ".join(sys.argv[1:])
with open(os.environ["DOCKER_LOG"], "a", encoding="utf-8") as handle:
    handle.write(line + "\\n")
fail = os.environ.get("DOCKER_FAIL", "")
if fail and fail in line:
    sys.exit(1)
sys.exit(0)
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    gh = bin_dir / "gh"
    gh.write_text(
        """#!/usr/bin/env python3
import os

print(os.environ["TIP_SHA"])
""",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    return bin_dir


def _publish(
    tmp_path: Path, *, fail: str = "", tip: str = _SHA
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    log = tmp_path / "docker-log"
    bin_dir = _fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["DOCKER_LOG"] = str(log)
    env["DOCKER_FAIL"] = fail
    env["SHA"] = _SHA
    env["TIP_SHA"] = tip
    env["GITHUB_REPOSITORY"] = "BioParser/BioParser"
    result = subprocess.run(
        ["bash", str(_ROOT / ".github/scripts/publish_images.sh")],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return result, lines


def _pushes(lines: list[str]) -> list[str]:
    return [line for line in lines if line.startswith("push ")]


@_NEEDS_POSIX
@pytest.mark.parametrize(
    ("fail", "tip", "outcome"),
    [
        ("", _SHA, "published"),
        ("--target cpu", _SHA, "no-push"),
        (f"push {_PREFIX}/migrate:{_SHA}", _SHA, "no-floating"),
        ("", "newer", "behind"),
    ],
)
def test_publish_order(tmp_path: Path, fail: str, tip: str, outcome: str) -> None:
    result, lines = _publish(tmp_path, fail=fail, tip=tip)
    pushes = _pushes(lines)
    if outcome == "published":
        assert result.returncode == 0, result.stderr
        builds = [line for line in lines if line.startswith("buildx build --load")]
        assert len(builds) == 5
        assert lines.index(builds[-1]) < lines.index(pushes[0])
        assert " --target cpu " in next(line for line in builds if "mineru.Dockerfile" in line)
        assert pushes == _PUSHES
        return
    if outcome == "behind":
        assert result.returncode == 0, result.stderr
        assert pushes == _PUSHES[:5]
        return
    assert result.returncode != 0
    if outcome == "no-push":
        assert any("mineru.Dockerfile" in line for line in lines)
        assert pushes == []
        return
    assert not any(":latest" in line or line.endswith(":cpu") for line in pushes)
