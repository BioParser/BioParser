"""The Traefik overlay ships no Basic Auth users: the operator makes the users file.
Each client has a rate limit, and the limit runs before the Basic Auth login."""

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_TRAEFIK = _ROOT / "docker" / "traefik"
# An htpasswd entry: a name, a colon, then a hash. Comment lines start with "#".
_HTPASSWD_ENTRY = re.compile(r"^[^#\s:][^:\s]*:\S+$", re.MULTILINE)
_RATE_LIMIT = "bioparser-ratelimit"


def _http_config() -> dict[str, Any]:
    """The `http` section of the dynamic configuration that the file provider loads."""
    config = yaml.safe_load((_TRAEFIK / "bioparser.yml").read_text(encoding="utf-8"))
    assert isinstance(config, dict)
    http = config["http"]
    assert isinstance(http, dict)
    return http


def _rate_limit() -> dict[str, Any]:
    limit = _http_config()["middlewares"][_RATE_LIMIT]["rateLimit"]
    assert isinstance(limit, dict)
    return limit


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


def test_every_router_starts_with_the_rate_limit() -> None:
    # Basic Auth comes after the limit. The limit also counts the wrong passwords.
    routers = _http_config()["routers"]
    assert routers
    for name, router in routers.items():
        assert router["middlewares"][0] == _RATE_LIMIT, name


def test_the_rate_limit_is_on() -> None:
    # Traefik reads `average: 0` (also a missing `average`) as "no limit".
    limit = _rate_limit()
    assert isinstance(limit["average"], int)
    assert limit["average"] >= 1
    assert isinstance(limit["burst"], int)
    assert limit["burst"] >= 1


def test_the_burst_is_at_most_one_second_of_requests() -> None:
    # Traefik deletes the bucket of a client after 1 to 2 seconds with no requests, and
    # the next request starts a full bucket. With a burst larger than one second of
    # `average`, a client that pauses can send more than `average` requests each second.
    limit = _rate_limit()
    assert limit["period"] == "1s"
    assert limit["burst"] <= limit["average"]


def test_the_rate_limit_uses_the_tcp_address_of_the_client() -> None:
    # Without sourceCriterion, Traefik uses the remote address of the connection. The
    # entry point trusts no proxy, and Traefik deletes X-Forwarded-For before the
    # routers. A limit by that header puts all clients in one bucket. Traefik ignores
    # the case of option names, and the test also ignores it.
    assert "sourcecriterion" not in {key.lower() for key in _rate_limit()}
