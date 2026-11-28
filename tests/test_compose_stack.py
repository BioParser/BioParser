"""The Compose stack in real containers: a secret shows only in /run/secrets.

The test builds the API, migrate and worker images. It starts redis, postgres, migrate, the
API and the two workers with new secrets. The workers start without mineru and vllm: a
worker calls those services only before it claims a job. The test needs the Docker daemon
and some minutes. It uses its own project, image names, port and secrets, and removes them
at the end. Opt in:

    BIOPARSER_TEST_COMPOSE_STACK=1 uv run pytest tests/test_compose_stack.py
"""

import gzip
import os
import socket
import subprocess
import tarfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx2
import pytest

from bioparser.scripts.generate_secrets import make_secrets

pytestmark = [
    pytest.mark.compose,
    pytest.mark.skipif(
        os.environ.get("BIOPARSER_TEST_COMPOSE_STACK") != "1",
        reason="set BIOPARSER_TEST_COMPOSE_STACK=1 to start containers",
    ),
    pytest.mark.timeout(1800),  # the image build takes minutes
]

ROOT = Path(__file__).resolve().parents[1]
PDF = ("paper.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")
# A paper of 3 pages. The default parser reads it, and then the job goes to the extractor.
PAPER = ROOT / "tests" / "parser" / "fixtures" / "plos-biology-3000248.pdf"
# The warning of src/bioparser/worker/extract/before_claim.py, after the load of the job.
VLLM_DOWN = "vLLM is not answering"
WORKERS = ("parser-worker", "extractor-worker")
# The files in /run/secrets of each service: each target is the name of a setting.
MOUNTS = {
    "redis": {"redis_password"},
    "postgres": {"postgres_password"},
    "bioparser": {"bioparser_redis_password", "bioparser_database_password", "bioparser_api_key"},
    "parser-worker": {"bioparser_redis_password", "bioparser_database_password"},
    "extractor-worker": {"bioparser_redis_password", "bioparser_database_password"},
}
# psql reads the new password from the secret file, not from a command line.
SET_SUPERUSER_PASSWORD = (
    "\\set password `cat /run/secrets/postgres_password`\n"
    "ALTER ROLE bioparser PASSWORD :'password';\n"
)


class Stack:
    """One Compose project of this test, with its own secrets directory."""

    def __init__(self, directory: Path) -> None:
        self.project = f"bioparser-secrets-test-{uuid.uuid4().hex[:8]}"
        self.secrets = directory / "secrets"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port: int = probe.getsockname()[1]
        # Own image names: the build does not replace the images of your own stack.
        self.built = ("bioparser", "migrate", *WORKERS)
        self.images = [f"{self.project}-{service}" for service in self.built]
        self.override = directory / "override.yml"
        self.override.write_text(
            "services:\n"
            + "".join(
                f"  {service}: {{image: {image}}}\n"
                for service, image in zip(self.built, self.images, strict=True)
            ),
            encoding="utf-8",
        )
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
        self.env |= {"BIOPARSER_SECRETS_DIR": str(self.secrets), "BIOPARSER_PORT": str(self.port)}

    def compose(
        self, *args: str, stdin: str | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        command = ["docker", "compose", "--project-name", self.project]
        command += ["--project-directory", str(ROOT), "--env-file", str(ROOT / ".env.example")]
        for file in (ROOT / "docker-compose.yml", ROOT / "docker-compose.cpu.yml", self.override):
            command += ["--file", str(file)]
        result = subprocess.run(
            [*command, *args],
            env=self.env,
            input=stdin,
            capture_output=True,
            text=True,
            check=False,
            timeout=1200,
        )
        if check and result.returncode != 0:
            # The end holds the error. The start is mostly image pull progress.
            raise AssertionError("\n".join(result.stderr.splitlines()[-40:]))
        return result

    def run_in(self, service: str, script: str) -> str:
        result = self.compose("exec", "-T", service, "sh", "-c", script, check=False)
        return result.stdout + result.stderr

    def value(self, name: str) -> str:
        return (self.secrets / name).read_text(encoding="utf-8")

    def leaked(self, text: str | bytes) -> list[str]:
        """The names of the secrets whose value is in text, also of ./secrets. No value."""
        data = text if isinstance(text, bytes) else text.encode()
        found = []
        for directory in (self.secrets, ROOT / "secrets"):
            for path in sorted(directory.glob("*")) if directory.is_dir() else []:
                value = path.read_bytes().strip()
                if value and value in data:
                    found.append(f"{directory}/{path.name}")
        return found

    def api(self, key: str | None = None) -> httpx2.Client:
        headers = {"X-API-Key": key} if key else {}
        return httpx2.Client(base_url=f"http://127.0.0.1:{self.port}", headers=headers, timeout=30)

    def wait_for_api(self) -> None:
        for _ in range(180):
            try:
                with self.api() as client:
                    if client.get("/health").status_code == 200:
                        return
            except httpx2.HTTPError:
                pass
            time.sleep(1)
        raise AssertionError("the API did not start")

    def submit_paper(self, key: str) -> str:
        """Queue the paper through the API, and return the job id."""
        with self.api(key) as client:
            file = (PAPER.name, PAPER.read_bytes(), "application/pdf")
            submitted = client.post("/api/extractions", files={"file": file})
        assert submitted.status_code == 202, submitted.text
        return str(submitted.json()["job_id"])

    def wait_for_the_workers(self, key: str, job_id: str) -> None:
        """Each worker reads the job from Redis and from Postgres with its secret files.

        The parser worker parses the paper: the status becomes parsed. The extractor worker
        loads the job, and it stops before its claim, because vllm does not answer. Its
        warning has the job id.
        """
        status = ""
        for _ in range(180):
            with self.api(key) as client:
                job = client.get(f"/api/jobs/{job_id}").json()
            status = job["status"]
            assert status != "failed", job
            logs = self.compose("logs", "--no-color", "extractor-worker").stdout.splitlines()
            if status == "parsed" and any(job_id in line and VLLM_DOWN in line for line in logs):
                return
            time.sleep(1)
        raise AssertionError(f"the workers did not take job {job_id}: status {status!r}")


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    stack = Stack(tmp_path_factory.mktemp("stack"))
    make_secrets(stack.secrets)
    try:
        stack.compose("build", *stack.built)
        # up waits until redis and postgres are healthy and migrate exited with 0.
        stack.compose("up", "--detach", "bioparser")
        # A worker calls mineru or vllm only before it claims a job. It starts without them.
        stack.compose("up", "--detach", "--no-deps", *WORKERS)
        stack.wait_for_api()
        yield stack
    finally:
        stack.compose("down", "--volumes", "--remove-orphans", check=False)
        subprocess.run(["docker", "image", "rm", *stack.images], capture_output=True, check=False)


def test_the_stack_works_with_the_secret_files(stack: Stack) -> None:
    with stack.api(stack.value("bioparser_api_key")) as client:
        # 202: the API logged in to Postgres and to Redis with the passwords from the files.
        submitted = client.post("/api/extractions", files={"file": PDF})
        assert submitted.status_code == 202, submitted.text
    with stack.api() as client:
        assert client.get("/api/jobs").status_code == 401
    assert "NOAUTH" in stack.run_in("redis", "redis-cli ping")
    with_password = 'REDISCLI_AUTH="$(cat /run/secrets/redis_password)" redis-cli ping'
    assert stack.run_in("redis", with_password).strip() == "PONG"
    for service, names in MOUNTS.items():
        assert set(stack.run_in(service, "ls /run/secrets").split()) == names, service
    migrate = stack.compose("run", "--rm", "--no-deps", "migrate", "ls", "/run/secrets")
    assert set(migrate.stdout.split()) == {"bioparser_database_password", "postgres_app_password"}
    key = stack.value("bioparser_api_key")
    stack.wait_for_the_workers(key, stack.submit_paper(key))


def test_no_secret_shows_outside_run_secrets(stack: Stack) -> None:
    """docker inspect, /proc, the logs, and the history and layers of the built images."""
    containers = stack.compose("ps", "--all", "--quiet").stdout.split()
    inspected = subprocess.run(
        ["docker", "inspect", *containers], capture_output=True, text=True, check=True
    )
    assert stack.leaked(inspected.stdout) == []
    # Every command line, and the environment of PID 1. A health check's redis-cli holds
    # REDISCLI_AUTH for some milliseconds; only its user and root can read that, as the file.
    for service in MOUNTS:
        proc = stack.run_in(service, "cat /proc/[0-9]*/cmdline /proc/1/environ")
        assert stack.leaked(proc) == [], service
    logs = stack.compose("logs", "--no-color")
    assert stack.leaked(logs.stdout + logs.stderr) == []
    for image in stack.images:
        history = subprocess.run(
            ["docker", "history", "--no-trunc", image], capture_output=True, text=True, check=True
        )
        assert stack.leaked(history.stdout) == [], image
        assert _leaked_in_layers(stack, image) == [], image


def test_the_documented_rotation_works(stack: Stack) -> None:
    """docs/docker-compose.md: delete the files, run the script, recreate the services."""
    old_key, old_redis = stack.value("bioparser_api_key"), stack.value("redis_password")
    for name in (
        "redis_password",
        "postgres_password",
        "postgres_app_password",
        "bioparser_api_key",
    ):
        (stack.secrets / name).unlink()
    make_secrets(stack.secrets)

    stack.compose("up", "--detach", "--wait", "--force-recreate", "postgres")
    psql = ["psql", "-U", "bioparser", "-d", "bioparser", "-v", "ON_ERROR_STOP=1"]
    stack.compose("exec", "-T", "postgres", *psql, stdin=SET_SUPERUSER_PASSWORD)
    # up fails when migrate cannot log in with the new superuser password.
    stack.compose("up", "--detach", "--force-recreate", "redis", "migrate", "bioparser")
    stack.wait_for_api()
    # The docs recreate the workers too. --no-deps: this stack has no mineru and no vllm.
    stack.compose("up", "--detach", "--force-recreate", "--no-deps", *WORKERS)

    with stack.api(old_key) as client:
        assert client.get("/api/jobs").status_code == 401
    # The API queues the job (202) with the new Redis password and the bioparser_app password
    # that migrate set. Then the workers take the job with the new files.
    new_key = stack.value("bioparser_api_key")
    stack.wait_for_the_workers(new_key, stack.submit_paper(new_key))
    old_works = "PONG" in stack.run_in("redis", f"REDISCLI_AUTH={old_redis} redis-cli ping")
    assert not old_works, "Redis takes the old password"  # no password in the message


def _leaked_in_layers(stack: Stack, image: str) -> list[str]:
    """Secrets in a file of a layer: `docker image save` writes each layer as a tar."""
    leaked: set[str] = set()
    with subprocess.Popen(["docker", "image", "save", image], stdout=subprocess.PIPE) as saved:
        assert saved.stdout is not None
        with tarfile.open(fileobj=saved.stdout, mode="r|") as archive:
            for member in archive:
                content = archive.extractfile(member)
                data = content.read() if content else b""
                assert data[:4] != b"\x28\xb5\x2f\xfd", f"{member.name}: zstd, not scanned"
                leaked.update(
                    stack.leaked(gzip.decompress(data) if data[:2] == b"\x1f\x8b" else data)
                )
    return sorted(leaked)
