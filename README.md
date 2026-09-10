# BioParser
[![Build](https://github.com/bioparser/bioparser/actions/workflows/ci.yml/badge.svg)](https://github.com/bioparser/bioparser/actions/workflows/ci.yml)

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

Parse a PDF to artifact JSON:

```sh
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf -o artifact.json
```

Overlay parser boxes on the PDF (localhost only):

```sh
uv run pdf-view tests/parser/fixtures/plos-biology-1002000.pdf
```

Opens `http://127.0.0.1:8765/`. You can also start `uv run pdf-view` with no file and upload a PDF in the browser.

Optional CPU MinerU backend (an isolated UV tool, not `uv sync`):

```sh
uv tool install --python 3.13 'mineru[pipeline]==3.4.5' --with six
# If `mineru` is not found afterwards: uv tool update-shell
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
uv run pdf-view tests/parser/fixtures/plos-biology-1002000.pdf --backend mineru
```

Uninstall it with `uv tool uninstall mineru`.

Details: [Local PDF parser](docs/parser.md).

## Development

- [Architecture](docs/architecture.md)
- [Local PDF parser](docs/parser.md)
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