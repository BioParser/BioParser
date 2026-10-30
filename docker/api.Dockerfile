# The BioParser API.
#   docker build -f docker/api.Dockerfile -t bioparser-api .

# uv 0.13.6, the same pin as CI. Index digest so amd64 and arm64 both resolve.
FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# python 3.13.16-slim-trixie; index digest so amd64 and arm64 both resolve.
FROM python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f AS base

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

# Builds the UI into static files. Node stays in this stage; the runtime image has none.
# Same version as frontend/.nvmrc. Index digest so amd64 and arm64 both resolve.
# $BUILDPLATFORM: the output is the same on every platform, so it builds natively.
FROM --platform=$BUILDPLATFORM node:26.13.0-slim@sha256:e4db4c914bf8ad241bd24804fc25d272d89bad97e68cdc8a1414862790a02c39 AS ui

WORKDIR /ui

# Locked dependencies first, then the source, so a code change reuses the dependency layer.
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

#-------------------------------------------

FROM base AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home \
        --shell /usr/sbin/nologin app \
    && mkdir -p /data \
    && chown app:app /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    BIOPARSER_UI_DIST_PATH=/app/ui

WORKDIR /app

# Non-editable install: the project lives in the venv, so src is not copied.
COPY --from=build --chown=app:app /app/.venv /app/.venv

# The built UI. Owned by root: the app user only reads it.
COPY --from=ui /ui/dist /app/ui

USER app:app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "bioparser.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
