"""Invariants for Dockerfiles and Compose configurations for Windows & Linux compatibility."""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOCKER_DIR = _ROOT / "docker"
_COMPOSE_FILE = _ROOT / "docker-compose.yml"


def test_compose_vllm_volume_supports_windows_and_linux_user_profiles() -> None:
    compose = _COMPOSE_FILE.read_text(encoding="utf-8")
    assert (
        "${HF_CACHE_DIR:-${USERPROFILE:-${HOME}}/.cache/huggingface}:/root/.cache/huggingface"
        in compose
    )


def test_dockerfiles_uv_cache_mounts_use_sharing_locked() -> None:
    dockerfiles = list(_DOCKER_DIR.glob("*.Dockerfile"))
    assert dockerfiles, "Expected to find Dockerfiles in docker/"

    for dockerfile in dockerfiles:
        lines = dockerfile.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines, 1):
            if "--mount=type=cache" in line and "/root/.cache/uv" in line:
                assert "sharing=locked" in line, (
                    f"{dockerfile.name}:{idx} UV cache mount missing sharing=locked: {line}"
                )


def test_dockerfiles_npm_cache_mounts_use_sharing_locked() -> None:
    api_dockerfile = _DOCKER_DIR / "api.Dockerfile"
    lines = api_dockerfile.read_text(encoding="utf-8").splitlines()
    npm_cache_lines = [
        (idx, line)
        for idx, line in enumerate(lines, 1)
        if "--mount=type=cache" in line and "/root/.npm" in line
    ]
    assert npm_cache_lines, "Expected npm cache mount in api.Dockerfile"
    for idx, line in npm_cache_lines:
        assert "sharing=locked" in line, (
            f"api.Dockerfile:{idx} npm cache mount missing sharing=locked: {line}"
        )


def test_mineru_dockerfile_uses_separate_uv_cache_id() -> None:
    mineru_dockerfile = _DOCKER_DIR / "mineru.Dockerfile"
    lines = mineru_dockerfile.read_text(encoding="utf-8").splitlines()
    uv_cache_lines = [
        (idx, line)
        for idx, line in enumerate(lines, 1)
        if "--mount=type=cache" in line and "/root/.cache/uv" in line
    ]
    assert uv_cache_lines, "Expected uv cache mount in mineru.Dockerfile"
    for idx, line in uv_cache_lines:
        assert "id=uv-mineru" in line, (
            f"mineru.Dockerfile:{idx} should use its own uv cache id: {line}"
        )
