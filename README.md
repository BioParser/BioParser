# BioParser

## Install

### Docker Compose

```sh
cp .env.example .env
```

`.env` is gitignored.

### Host (`uv`)

Needs **Python 3.13** and [uv](https://docs.astral.sh/uv/). For the local API, checks, and dev scripts:

```sh
uv sync
```

## Running

### Docker Compose (default)

```sh
docker compose up --build
```

- API: `http://localhost:8080`: `curl localhost:8080/health`
- vLLM: `http://localhost:8000`

First start can take several minutes (image pull, model download, healthcheck). Stop with `Ctrl+C` or `docker compose down`.

Probe the model from the host (after vLLM is up):

```sh
uv run test_prompt "Say hello in one sentence"
```

Ports, env vars, Hugging Face tokens, and more options: [docs/docker-compose.md](docs/docker-compose.md).

### Local (`uv`)

Starts the FastAPI app only

```sh
uv run bioparser
```

Listens on `127.0.0.1:8080`.

## Development

- [Architecture](docs/architecture.md)
- [Definition of Done](docs/dod.md)
- [Collaboration practices](docs/collaboration.md)
- [Docker Compose](docs/docker-compose.md)

Install hooks so they run on commit:

```sh
uv run pre-commit install
```

All hooks: `uv run pre-commit run --all-files`.

```bash
uv run ruff check
uv run ruff format
uv run mypy
uv run pytest
```

Lint auto-fixes: `uv run ruff check --fix`.