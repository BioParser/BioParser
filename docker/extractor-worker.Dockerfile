# The BioParser extractor worker. It calls the Compose vLLM service.
# The model is not in this image.
#   docker build -f docker/extractor-worker.Dockerfile -t bioparser-extractor-worker .

# uv 0.12.23, the same pin as CI. Index digest so amd64 and arm64 both resolve.
FROM ghcr.io/astral-sh/uv:0.12.23@sha256:61d393e44e249f2e4b526b6c7ddcecce245946826e608e11c93ad4f5bba55b21 AS uv

# python 3.13.15-slim-trixie; index digest so amd64 and arm64 both resolve.
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

#-------------------------------------------

FROM base AS build

COPY --from=uv /uv /uvx /bin/

# uv's cache goes to the cache mount, never into an image layer.
ENV UV_CACHE_DIR=/root/.cache/uv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

# Locked dependencies first, then the project, so a code change reuses the dependency layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-editable --no-install-project

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

#-------------------------------------------

FROM base AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
        --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Non-editable install: the project lives in the venv, so src is not copied.
COPY --from=build --chown=app:app /app/.venv /app/.venv

USER app:app

# Port matches DEFAULT_LIVENESS_PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('BIOPARSER_LIVENESS_PORT', '8081') + '/health', timeout=3)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bioparser.worker.extract"]
