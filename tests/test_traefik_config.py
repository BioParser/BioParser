"""The Traefik overlay ships no Basic Auth users: the operator makes the users file."""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_TRAEFIK = _ROOT / "docker" / "traefik"
# An htpasswd entry: a name, a colon, then a hash. Comment lines start with "#".
_HTPASSWD_ENTRY = re.compile(r"^[^#\s:][^:\s]*:\S+$", re.MULTILINE)


def test_the_users_example_holds_no_user() -> None:
    example = (_TRAEFIK / "users.example").read_text(encoding="utf-8")
    assert not _HTPASSWD_ENTRY.search(example)


def test_git_ignores_the_real_users_file() -> None:
    ignored = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "docker/traefik/users" in ignored


def test_traefik_reads_the_users_from_the_compose_secret() -> None:
    config = (_TRAEFIK / "bioparser.yml").read_text(encoding="utf-8")
    assert 'usersFile: "/run/secrets/traefik_users"' in config


def test_compose_refuses_traefik_without_a_users_file() -> None:
    compose = (_ROOT / "docker-compose.traefik.yml").read_text(encoding="utf-8")
    assert "${TRAEFIK_USERS_FILE:?" in compose
