# The BioParser parse worker plus MinerU's pipeline backend.
#   docker build -f docker/parser-worker.Dockerfile --target cpu -t bioparser-parser-worker:cpu .
#   docker build -f docker/parser-worker.Dockerfile --target gpu -t bioparser-parser-worker:gpu .
#
# MinerU lives in its own venv, built in a throwaway stage, so its torch stack never
# touches the app's locked dependencies and uv's download cache never lands in an image
# layer. The model weights are downloaded once, in the CPU stage, and copied into both
# targets. The GPU stage only swaps in a CUDA build of that same venv.

# uv 0.12.9; index digest so amd64 and arm64 both resolve.
FROM ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff AS uv

# python 3.13.15-slim-trixie; index digest so amd64 and arm64 both resolve.
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 tini \
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

FROM build AS mineru-cpu

# mineru[pipeline] pin is checked against MINERU_PIPELINE_VERSION.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/mineru \
    && UV_TORCH_BACKEND=cpu uv pip install --python /opt/mineru/bin/python \
        "mineru[pipeline]==3.4.5" "six==1.17.0"

# Fail the build if the pipeline install cannot be imported, or torch is not the CPU build.
RUN /opt/mineru/bin/python -c "import torch; from mineru.backend.pipeline.pipeline_analyze import doc_analyze_streaming; assert torch.version.cuda is None, torch.__version__"

# g=u gives OpenShift's random UID (group 0) the owner's access.
RUN mkdir -p /home/app \
    && HOME=/home/app /opt/mineru/bin/mineru-models-download -s huggingface -m pipeline \
    && chmod -R g=u /home/app

# Fail the build if the pipeline model config or directory is missing.
RUN /opt/mineru/bin/python -c "import json, os, pathlib; cfg=json.loads((pathlib.Path('/home/app')/'mineru.json').read_text()); root=cfg['models-dir']['pipeline']; assert os.path.isdir(root), root"

FROM build AS mineru-gpu

ARG TORCH_BACKEND=cu129

RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /opt/mineru \
    && UV_TORCH_BACKEND="${TORCH_BACKEND}" uv pip install --python /opt/mineru/bin/python \
        "mineru[pipeline]==3.4.5" "six==1.17.0"

# Fail the build if the pipeline install cannot be imported, or torch is not a CUDA build.
RUN /opt/mineru/bin/python -c "import torch; from mineru.backend.pipeline.pipeline_analyze import doc_analyze_streaming; assert torch.version.cuda, torch.__version__"

#-------------------------------------------

FROM build AS app

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

# Numeric user: OpenShift runs the container as a random UID in group 0.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/app --no-create-home \
        --shell /usr/sbin/nologin app \
    && install -d -o 10001 -g 0 -m 0770 /home/app /data

# App venv first, so `python` is BioParser. `mineru` resolves from /opt/mineru/bin;
# that script's shebang uses the MinerU interpreter.
ENV PATH="/app/.venv/bin:/opt/mineru/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/app \
    MINERU_MODEL_SOURCE=local \
    HF_HUB_OFFLINE=1

WORKDIR /app
USER 10001:10001

# Port matches DEFAULT_LIVENESS_PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('BIOPARSER_LIVENESS_PORT', '8081') + '/health', timeout=3)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "bioparser.worker.parse"]

#-------------------------------------------

FROM runtime AS gpu

COPY --chown=10001:0 --from=mineru-cpu /home/app /home/app
COPY --from=mineru-gpu /opt/mineru /opt/mineru
COPY --from=app /app/.venv /app/.venv

# The earlier checks run as root, before these trees are copied together.
# This runs as uid 10001 and calls the worker's own startup check.
RUN PYTHONDONTWRITEBYTECODE=1 \
    /app/.venv/bin/python -c "import bioparser.worker.parse; from bioparser.parser.backend.mineru.runtime import require_mineru_cli; require_mineru_cli()" \
    && /opt/mineru/bin/python -c "import torch; assert torch.version.cuda, torch.__version__"

FROM runtime AS cpu

COPY --chown=10001:0 --from=mineru-cpu /home/app /home/app
COPY --from=mineru-cpu /opt/mineru /opt/mineru
COPY --from=app /app/.venv /app/.venv

# The earlier checks run as root, before these trees are copied together.
# This runs as uid 10001 and calls the worker's own startup check.
RUN PYTHONDONTWRITEBYTECODE=1 \
    /app/.venv/bin/python -c "import bioparser.worker.parse; from bioparser.parser.backend.mineru.runtime import require_mineru_cli; require_mineru_cli()" \
    && /opt/mineru/bin/python -c "import torch; assert torch.version.cuda is None, torch.__version__"
