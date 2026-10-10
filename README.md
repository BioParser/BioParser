# BioParser

[![CI](https://github.com/BioParser/BioParser/actions/workflows/ci.yml/badge.svg)](https://github.com/BioParser/BioParser/actions/workflows/ci.yml)

BioParser extracts mammal trait data (for example body length and weight) from born-digital scientific PDFs. Everything runs locally: a PDF parser (pdfplumber or MinerU) splits the article into traceable blocks, and a local LLM served by vLLM extracts observations with evidence.

> **Status: early development.** `POST /api/extractions` accepts a PDF and creates a job, but no worker processes jobs yet, so every job stays `queued`. To run the full parse → extract pipeline today, use the synchronous test route `POST /extract`. Details: [docs/api.md](docs/api.md).

## Quick start (Docker Compose)

Runs the full stack: API, MinerU, vLLM, Redis, and central logs (Loki, Alloy, Grafana).

Prerequisites: Docker with Compose v2. The default stack uses an NVIDIA GPU and needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). A CPU stack is also available.

1. Create your config (`.env` is gitignored):

```sh
   cp .env.example .env
```

   Set `GRAFANA_ADMIN_PASSWORD` in `.env`; Compose does not start without it.

   This selects the GPU stack. For CPU, change the `COMPOSE_FILE` line in `.env` to:

```sh
   COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml:docker-compose.logging.yml
```

2. Build and start:

```sh
   docker compose up --build
```

   The first run is slow: MinerU models download during the image build, and vLLM downloads the model set in `VLLM_MODEL` (default `Qwen/Qwen3-0.6B`). The API starts only after `vllm`, `mineru`, and `redis` pass their health checks.

3. Check the API:

```sh
   curl localhost:8080/health
   # {"status":"ok"}
```

4. Run the pipeline on a sample paper (synchronous; can take minutes on CPU):

```sh
   curl -F file=@tests/parser/fixtures/plos-biology-3000248.pdf localhost:8080/extract
```

Stop with `Ctrl+C` or `docker compose down`.

| Service | Address on the host |
|---|---|
| API | `http://localhost:8080` (Swagger UI: `/docs`) |
| vLLM (OpenAI-compatible) | `http://localhost:8000/v1` |
| Grafana (logs of all services) | `http://localhost:3000` (user `admin`) |
| MinerU, Redis, Loki, Alloy | not published (internal network only) |

Published ports bind to `127.0.0.1` only. Ports, environment variables, log queries, and `test_prompt`: [docs/docker-compose.md](docs/docker-compose.md).

## Local development (uv)

Prerequisites: [uv](https://docs.astral.sh/uv/). The project pins Python 3.13; `uv sync` downloads it if missing.

```sh
uv sync
```

### Run the API

```sh
uv run bioparser
```

Serves on `http://127.0.0.1:8080`. Job submission and polling work on their own; `POST /extract` also needs reachable MinerU and vLLM services.

> The host API reads `.env` if it exists. `.env.example` sets `BIOPARSER_REDIS_URL=redis://redis:6379/0`, which only resolves inside Compose, so `/ready` returns `503`. Empty that line for host-only runs.

### Parse a PDF

```sh
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf -o artifact.json
```

Omit `-o` to print the artifact JSON to stdout.

### View parser boxes on a PDF

```sh
uv run pdf-view tests/parser/fixtures/plos-biology-1002000.pdf
```

Then open `http://127.0.0.1:8765/` (loopback only; change with `--port`). Run `uv run pdf-view` with no file to upload a PDF in the browser.

### Optional: MinerU backend

Both scripts accept `--backend mineru`. It needs the MinerU CLI as a separate uv tool (not part of `uv sync`):

```sh
uv tool install --python 3.13 'mineru[pipeline]==3.4.5' --with six
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
```

Version pin, RAM needs, and uninstalling: [docs/parser.md](docs/parser.md).

### Send a prompt to vLLM

With the Compose stack running:

```sh
uv run test_prompt "Say hello in one sentence"
```

This calls vLLM directly, not the API. Options: `uv run test_prompt --help`.

## Checks and tests

Same commands as CI:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy .
uv run pytest -m "not integration"
```

Auto-fix: `uv run ruff check --fix` and `uv run ruff format`.

Integration tests need Redis at `BIOPARSER_TEST_REDIS_URL` (default `redis://localhost:6379/15`); without it they are skipped:

```sh
uv run pytest -m integration
```

Install the pre-commit hooks (uv lock, ruff, mypy) once:

```sh
uv run pre-commit install
```

Run them on all files: `uv run pre-commit run --all-files`.

## Documentation

- [Architecture](docs/architecture.md): target design, components, data contracts
- [API](docs/api.md): HTTP contract, current scope, module layout
- [Docker Compose](docs/docker-compose.md): services, environment variables, logs, `test_prompt`
- [Local PDF parser](docs/parser.md): parser backends and the dev viewer
- [Collaboration practices](docs/collaboration.md): branching, PRs, reviews
- [Definition of Done](docs/dod.md)
