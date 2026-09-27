# Docker Compose

Compose runs four services:

- `vllm` is a local OpenAI-compatible model server on host port `VLLM_PORT` (default 8000).  
- `mineru` is the PDF parser service on internal network port 8000 (optional host port `MINERU_PORT`, default 8001).  
- `redis` is queue and job state storage on port 6379 (accessed inside the Docker network using `redis:6379`).  
- `bioparser` is FastAPI on host port `BIOPARSER_PORT` (default 8080).  

The README has the default install and start commands. This page covers env vars, networking, and `test_prompt`.

## Environment

vLLM reads the model id from `VLLM_MODEL`. If that variable is empty, the `vllm` container exits immediately with an error like `Repo id must use alphanumeric chars ...: ''`.

```sh
cp .env.example .env
```

Required:

`COMPOSE_FILE`: Compose configuration preference, e.g. `docker-compose.yml:docker-compose.gpu.yml` (GPU) or `docker-compose.yml:docker-compose.cpu.yml` (CPU).  
`VLLM_MODEL`: Hugging Face repo id, for example `Qwen/Qwen3-0.6B` (already set in `.env.example`).  
`VLLM_PORT`: host port for vLLM (default `8000`).  
`BIOPARSER_PORT`: host port for the API (default `8080`).  

Optional:

`HF_TOKEN`: needed for gated or private Hugging Face models.  
`MINERU_PORT`: host port for MinerU (default `8001`).  

Inside Compose, the API uses `BIOPARSER_VLLM_BASE_URL=http://vllm:8000/v1` and `BIOPARSER_MINERU_BASE_URL=http://mineru:8000`. On the host, `test_prompt` should use `http://localhost:8000/v1` (the default in `.env.example`).

Weights cache on the host at `~/.cache/huggingface`.

## Start

All services start together; the API container waits for `vllm`, `mineru`, and `redis` health checks to pass before accepting traffic. The first model download can take several minutes (`start_period` 600s).

```sh
docker compose up --build
curl localhost:8080/health
```

Stop with `Ctrl+C`, or `docker compose down`.

## Test prompt

`test_prompt` sends one prompt to vLLM. It does not go through the FastAPI app. Needs [uv](https://docs.astral.sh/uv/) and Python 3.13 on the host (`uv sync`).

With Compose vLLM published on localhost:8000:

```sh
uv run test_prompt "Say hello in one sentence"
uv run test_prompt --help
uv run test_prompt "Summarize this" --system-prompt "Be brief" --temperature 0.0 --max-tokens 256
```

Override the URL if needed:

```sh
BIOPARSER_VLLM_BASE_URL=http://localhost:8000/v1 uv run test_prompt "Hello"
```

If vLLM is still loading, `test_prompt` exits with a short message instead of a connection traceback. Wait until `curl http://127.0.0.1:8000/health` succeeds, then retry.
