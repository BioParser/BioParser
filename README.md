# BioParser

**Pitch:** https://bioparser.github.io/BioParser/

[![CI](https://github.com/BioParser/BioParser/actions/workflows/ci.yml/badge.svg)](https://github.com/BioParser/BioParser/actions/workflows/ci.yml)

BioParser extracts mammal trait data (for example body length and weight) from born-digital scientific PDFs. Everything runs locally: a PDF parser (pdfplumber or MinerU) splits the article into traceable blocks, and a local LLM served by vLLM extracts observations with evidence.

> **Status: early development.** `POST /api/extractions` accepts a PDF and creates a job, but no worker processes jobs yet, so every job stays `queued`. To run the full parse → extract pipeline today, use the synchronous test route `POST /extract`. Details: [docs/api.md](docs/api.md).

## Quick start (Docker Compose)

**NOTE:** Make sure to have at least 50GB of available storage space before running BioParser.

Runs the full stack: API, the parser worker, the extractor worker, MinerU, vLLM, Redis, and central logs (Loki, Alloy, Grafana).

Prerequisites: Docker with Compose v2. The default stack uses an NVIDIA GPU and needs the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). A CPU stack is also available.

1. Create your config (`.env` is gitignored):

```sh
   cp .env.example .env
```

   Set `GRAFANA_ADMIN_PASSWORD` in `.env`; Compose does not start without it.
   Set `BIOPARSER_API_KEY` in `.env`; the API does not start without it. Make a key with
   `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`.

   This selects the GPU stack. For CPU, change the `COMPOSE_FILE` line in `.env` to:

```sh
   COMPOSE_FILE=docker-compose.yml:docker-compose.cpu.yml:docker-compose.logging.yml
```

2. Build and start:

```sh
   docker compose up --build
```

   The first run is slow: the MinerU service downloads its models during its image build, and vLLM downloads the model set in `VLLM_MODEL` (default `Qwen/Qwen3-0.6B`). The extractor image does not download a model; it calls the vLLM service. The API starts only after `vllm`, `mineru`, and `redis` pass their health checks and `migrate` has created the `jobs` table. `migrate` waits for `postgres`. The parser worker starts after `mineru` and Redis do. The extractor worker starts after `vllm` and Redis do. Set `POSTGRES_PASSWORD` in `.env` before the first start.

3. Check the API:

```sh
   curl localhost:8080/health
   # {"status":"ok"}
```

4. Run the pipeline on a sample paper (synchronous; can take minutes on CPU):

```sh
   curl -H "X-API-Key: <BIOPARSER_API_KEY from .env>" \
     -F file=@tests/parser/fixtures/plos-biology-3000248.pdf localhost:8080/extract
```

Stop with `Ctrl+C` or `docker compose down`.

| Service | Address on the host |
|---|---|
| API | `http://localhost:8080` (Swagger UI: `/docs`) |
| Web UI | `http://localhost:8080/ui/` |
| vLLM (OpenAI-compatible) | `http://localhost:8000/v1` |
| Grafana (logs of all services) | `http://localhost:3000` (user `admin`) |
| MinerU, Redis, Postgres, Loki, Alloy, docker-api | not published (internal network only) |

Published ports bind to `127.0.0.1` only. Ports, environment variables, log queries, and `test_prompt`: [docs/docker-compose.md](docs/docker-compose.md).

## Running on an OKD-cluster (kubernetes)

The manifest files can be bound [here](manifests/). For more info on running BioParser on an OKD-cluster see [here](manifests/README.md).

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

> The host API reads `.env` if it exists. `.env.example` sets `BIOPARSER_REDIS_URL=redis://redis:6379/0`, which only resolves inside Compose, so `/ready` returns `503`. Empty that line for host-only runs. Postgres is checked only when `BIOPARSER_DATABASE_URL` is set. Compose sets that URL on the API container.
> The API does not start without `BIOPARSER_API_KEY`. For local development only, set `BIOPARSER_AUTH_DISABLED=true` instead.

### Run the UI

Prerequisites: [Node.js](https://nodejs.org/), the version in `.nvmrc`. With [fnm](https://github.com/Schniz/fnm) or nvm, run `fnm use` (or `nvm use`) to switch to it.

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173/ui/`. The page reloads when you save a file. The dev server forwards `/api` and `/ready` to the API on `127.0.0.1:8080`, so run `uv run bioparser` in a second terminal.

> The UI does not send the API key yet (#253), so its API calls get `401`. For local development only, set `BIOPARSER_AUTH_DISABLED=true` in `.env`.

After a change to the API, regenerate the UI's API types and commit both files. CI fails while they are out of date.

```sh
cd frontend
npm run gen:api
```

The API image builds the UI, and the API serves it at `/ui`. To serve a local build without Compose:

```sh
(cd frontend && npm run build)
BIOPARSER_UI_DIST_PATH=frontend/dist uv run bioparser
```

### Parse a PDF

```sh
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf -o artifact.json
```

Omit `-o` to print the artifact JSON to stdout. This uses the default backend. For the PDF viewer (`pdf-view`), the MinerU backend, or building the worker image, see [docs/parser.md](docs/parser.md).

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

For the UI, in `frontend/` (the same checks as CI's Frontend job):

```sh
npm run lint
npm run typecheck
npm run test
npm run build
```

Auto-fix lint and formatting: `npx biome check --write .`

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
