"""Make the secret files that Docker Compose mounts in /run/secrets.

Run it in the directory that holds docker-compose.yml:

    python3 src/bioparser/scripts/generate_secrets.py

A file that exists keeps its value. To rotate a secret, delete its file and run the script
again (docs/docker-compose.md, section "Secrets"). It uses only the standard library, so
the python3 of the host can run it.
"""

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


def make_secrets(directory: Path) -> None:
    """Make each missing secret file in directory, and print its path. Never a value."""
    # Only the owner can open the directory. Every user can read a file in it: Compose
    # bind-mounts each file with its host mode, and the services run as other users.
    directory.mkdir(mode=0o700, exist_ok=True)
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
    make_secrets(Path("secrets"))
