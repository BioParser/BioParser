# python 3.13.15-slim-trixie; index digest so amd64 and arm64 both resolve.
# This stage does not take TORCH_BACKEND, so CPU and GPU builds share the download.
# The CPU image uses this MinerU venv directly. The GPU image installs its own.
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS models

ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_TORCH_BACKEND=cpu

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# uv 0.12.9; index digest so amd64 and arm64 both resolve
COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/

# mineru[pipeline] pin is checked against MINERU_PIPELINE_VERSION.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/mineru \
    && uv pip install --python /opt/mineru/bin/python \
        "mineru[pipeline]==3.4.5" "six==1.17.0"

# Fail the build if the pipeline install cannot be imported.
RUN /opt/mineru/bin/python -c "from mineru.backend.pipeline.pipeline_analyze import doc_analyze_streaming"

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/app app

ENV PATH="/opt/mineru/bin:$PATH" \
    HOME=/home/app \
    MINERU_MODEL_SOURCE=local

USER app:app

# Fail the build if the pipeline model config or directory is missing.
RUN mineru-models-download -s huggingface -m pipeline \
    && python3 -c "import json, os, pathlib; cfg=json.loads((pathlib.Path.home()/'mineru.json').read_text()); root=cfg['models-dir']['pipeline']; assert os.path.isdir(root), root"

#-------------------------------------------

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS builder

ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable

#-------------------------------------------

FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 tini \
    && rm -rf /var/lib/apt/lists/*

# App venv first so `python` is BioParser. `mineru` resolves from /opt/mineru/bin;
# that script's shebang uses the MinerU interpreter.
ENV PATH="/app/.venv/bin:/opt/mineru/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    MINERU_MODEL_SOURCE=local \
    HF_HUB_OFFLINE=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/app app \
    && mkdir -p /data \
    && chown app:app /data

COPY --from=models --chown=app:app /home/app /home/app
# Fail the build if the pipeline model config or directory is missing.
RUN python3 -c "import json, os, pathlib; cfg=json.loads((pathlib.Path.home()/'mineru.json').read_text()); root=cfg['models-dir']['pipeline']; assert os.path.isdir(root), root"

USER app:app

# Port matches DEFAULT_LIVENESS_PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os,urllib.request; port=os.environ.get('BIOPARSER_LIVENESS_PORT','8081'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3)"

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bioparser.worker.parse"]

#-------------------------------------------

FROM runtime AS gpu

USER root

ARG TORCH_BACKEND=cu129

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/

RUN --mount=type=cache,target=/root/.cache/uv \
    UV_TORCH_BACKEND=${TORCH_BACKEND} uv venv /opt/mineru \
    && UV_TORCH_BACKEND=${TORCH_BACKEND} uv pip install --python /opt/mineru/bin/python \
        "mineru[pipeline]==3.4.5" "six==1.17.0" \
    && /opt/mineru/bin/python -c "from mineru.backend.pipeline.pipeline_analyze import doc_analyze_streaming" \
    && chown -R app:app /opt/mineru

USER app:app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

#-------------------------------------------

FROM runtime AS cpu

COPY --from=models --chown=app:app /opt/mineru /opt/mineru
COPY --from=builder --chown=app:app /app/.venv /app/.venv
