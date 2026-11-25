# The BioParser parse worker. MinerU runs as its own service (docker/mineru.Dockerfile),
# reached at BIOPARSER_MINERU_BASE_URL.
#   docker build -f docker/parser-worker.Dockerfile -t bioparser-parser-worker .

# uv 0.13.7, the same pin as CI. Index digest so amd64 and arm64 both resolve.
FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

# python 3.13.16-slim-trixie; index digest so amd64 and arm64 both resolve.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base

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
    UV_LINK_MODE=copy

WORKDIR /app

# Locked dependencies first, then the project, so a code change reuses the dependency layer.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-editable --no-install-project --no-default-groups --group parser-worker

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --no-default-groups --group parser-worker

#-------------------------------------------

# Dev stage for the Compose overlay. Not the last stage, so the default build
# stays the production image. Adds dev-reload (watchfiles) on the production venv.

FROM base AS parser-worker-dev

COPY --from=uv /uv /uvx /bin/
COPY --from=build /app/.venv /app/.venv

ENV UV_CACHE_DIR=/root/.cache/uv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --inexact --no-install-project --no-default-groups --group dev-reload

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/app --no-create-home \
        --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 0 -m 0770 /home/app /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/src \
    HOME=/home/app

USER 10001:10001

RUN PYTHONDONTWRITEBYTECODE=1 python -c "import bioparser.worker.parse, watchfiles"

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('BIOPARSER_LIVENESS_PORT', '8081') + '/health', timeout=3)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "watchfiles", "--target-type", "command", "python -m bioparser.worker.parse", "/src"]

#-------------------------------------------

FROM base AS runtime

# Numeric user: OpenShift runs the container as a random UID in group 0.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/app --no-create-home \
        --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 0 -m 0770 /home/app /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app

WORKDIR /app

# Non-editable install: the project lives in the venv, so src is not copied.
COPY --from=build /app/.venv /app/.venv

USER 10001:10001

# Runs as uid 10001: the worker imports from the copied venv.
RUN PYTHONDONTWRITEBYTECODE=1 python -c "import bioparser.worker.parse"

# Port matches DEFAULT_LIVENESS_PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('BIOPARSER_LIVENESS_PORT', '8081') + '/health', timeout=3)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bioparser.worker.parse"]
