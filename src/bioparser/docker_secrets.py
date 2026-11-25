"""Docker secrets: Compose mounts each secret as a file in /run/secrets.

pydantic-settings reads a setting from the file with the setting's environment name, for
example /run/secrets/bioparser_redis_password. A variable of that name has priority.
"""

from pathlib import Path

_RUN_SECRETS = Path("/run/secrets")
# None outside a container: pydantic-settings warns when the directory does not exist.
SECRETS_DIR: Path | None = _RUN_SECRETS if _RUN_SECRETS.is_dir() else None
