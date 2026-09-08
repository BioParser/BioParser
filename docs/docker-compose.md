# Docker Compose

Compose runs two services.

`vllm` is a local OpenAI compatible model server on host port `VLLM_PORT` (default 8000).
`bioparser` is FastAPI on host port `BIOPARSER_PORT` (default 8080).

The README has the default install and start commands. This page covers env vars, networking, and `test_prompt`.

## Environment

vLLM reads the model id from `VLLM_MODEL`. If that variable is empty, the `vllm` container exits immediately with an error like `Repo id must use alphanumeric chars ...: ''`.

```sh
cp .env.example .env
```

Required:

`VLLM_MODEL`: Hugging Face repo id, for example `Qwen/Qwen3-0.6B` (already set in `.env.example`).
`VLLM_PORT`: host port for vLLM (default `8000`).
`BIOPARSER_PORT`: host port for the API (default `8080`).

Optional:

`HF_TOKEN`: needed for gated or private Hugging Face models.

Inside Compose, the API uses `BIOPARSER_VLLM_BASE_URL=http://vllm:8000/v1`. On the host, `test_prompt` should use `http://localhost:8000/v1` (the default in `.env.example`).

Weights cache on the host at `~/.cache/huggingface`.

## Start

Both services start together; the API does not wait for vLLM. The first model download can take several minutes (`start_period` 600s). `/health` on the API can succeed while the model is still loading.

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

The command fails until vLLM has finished loading and is serving `/v1`.
