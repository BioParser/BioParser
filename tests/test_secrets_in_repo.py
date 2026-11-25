"""No secret goes into Git, into an image or into .env."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# The name of a secret. A name that ends in _FILE, _PATH or _DIR holds a path instead.
SECRET_NAME = re.compile(r"^(?!.*_(FILE|PATH|DIR)$).*(PASSWORD|SECRET|TOKEN|API_KEY)")


def test_git_and_the_image_build_leave_out_the_secrets_directory() -> None:
    assert "/secrets/" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "secrets/" in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()


def test_no_dockerfile_and_no_env_example_line_sets_a_secret() -> None:
    """ARG and ENV values stay in the image history. .env holds settings, not secrets."""
    names: list[str] = []
    for dockerfile in (ROOT / "docker").glob("*.Dockerfile"):
        text = dockerfile.read_text(encoding="utf-8").replace("\\\n", " ")
        for rest in re.findall(r"^\s*(?:ARG|ENV)\s+(.*)$", text, re.MULTILINE):
            names += re.findall(r"([A-Za-z_]\w*)=", rest) or rest.split()[:1]
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    names += [line.split("=")[0] for line in lines if "=" in line and not line.startswith("#")]
    assert len(names) > 20  # the parser found the settings
    assert [name for name in names if SECRET_NAME.match(name.upper())] == []
