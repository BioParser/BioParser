"""Make the secret files that Docker Compose mounts in /run/secrets.

Run it in the directory that holds docker-compose.yml:

    python3 src/bioparser/scripts/generate_secrets.py

The files go to the directory that Compose reads: BIOPARSER_SECRETS_DIR from the shell, else
its last value in .env, else ./secrets. A file that exists keeps its value. To rotate a secret,
delete its file and run the script again (docs/docker-compose.md, section "Secrets"). It uses
only the standard library, so the python3 of the host can run it.
"""

import os
import re
import secrets
from pathlib import Path

# Random values for the passwords and the key inside the stack.
GENERATED = (
    "redis_password",
    "postgres_password",
    "postgres_app_password",
    "bioparser_api_key",
    "grafana_admin_password",
)
# The token of another service. You write it in the file. An empty file: no token.
EXTERNAL = ("hf_token",)

# docker-compose.yml reads each file from ${BIOPARSER_SECRETS_DIR:-./secrets}.
DIRECTORY_VARIABLE = "BIOPARSER_SECRETS_DIR"
DEFAULT_DIRECTORY = "./secrets"
# An assignment in .env, with the rules that bioparser-compose uses for COMPOSE_FILE.
_ASSIGNMENT = re.compile(rf"\s*(?:export\s+)?{DIRECTORY_VARIABLE}\s*=\s*(.*)")


def secrets_directory(env_file: Path = Path(".env")) -> Path:
    """Return the directory that Compose reads the secret files from.

    A value in the shell wins, also an empty value. Else the last value in env_file wins. An
    empty value gives the default, as in Compose. Compose changes a "~" at the start to the
    home directory.
    """
    value = os.environ.get(DIRECTORY_VARIABLE)
    if value is None:
        value = _value_in_env_file(env_file)
    return Path(value or DEFAULT_DIRECTORY).expanduser()


def _value_in_env_file(env_file: Path) -> str:
    """Return the last value of the variable in env_file, without quotes. "": no value.

    In .env, Compose reads $ as an expansion and \\ as an escape. This script does not, so it
    stops at them.
    """
    value = ""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.is_file() else []
    for line in lines:
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            continue
        value = match.group(1)
        quote = value[:1]
        if quote in ("'", '"'):
            if quote not in value[1:]:
                raise SystemExit(f"{DIRECTORY_VARIABLE} in {env_file} has an unterminated quote")
            value = value[1:].split(quote, 1)[0]
        else:
            # A comment after a value without quotes starts with a space.
            value = value.split(" #", 1)[0].rstrip()
    if "$" in value or "\\" in value:
        raise SystemExit(
            f"{DIRECTORY_VARIABLE} in {env_file} contains $ or \\. Write a literal value, or set "
            f"{DIRECTORY_VARIABLE} in the shell."
        )
    return value


def make_secrets(directory: Path) -> None:
    """Make each missing secret file in directory, and print its path. Never a value."""
    # Only the owner can open the directory. Every user can read a file in it: Compose
    # bind-mounts each file with its host mode, and the services run as other users.
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    for name in GENERATED + EXTERNAL:
        path = directory / name
        try:
            # "x" stops at a path that exists, also at a symbolic link.
            with path.open("x", encoding="ascii") as file:
                file.write(secrets.token_hex(32) if name in GENERATED else "")
            print(f"made {path}")
        except FileExistsError:
            pass
        # Also for a file that you wrote: an editor can give it mode 0600. A symbolic link
        # stays as it is, because chmod changes the target of a link.
        if path.is_file() and not path.is_symlink():
            path.chmod(0o644)


if __name__ == "__main__":
    make_secrets(secrets_directory())
