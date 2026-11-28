# The MinerU 4 parse service (mineru-api). The API and the parser worker call it over HTTP.
#   docker build -f docker/mineru.Dockerfile --target cpu -t bioparser-mineru:cpu .
#   docker build -f docker/mineru.Dockerfile --target gpu -t bioparser-mineru:gpu .
#
# cpu: mineru with ONNX small models, serving the Basic tier (layout, OCR, formula and table
# models, no VLM). gpu: mineru[full], torch small models plus MinerU's VLM on vLLM, serving
# the Advanced tier and below (Standard included). Each target builds its venv in a throwaway
# stage, so uv's cache never lands in an image layer, and bakes in the models its tier needs:
# the service sits on an internal network and cannot fetch weights at runtime.

# uv 0.13.10, the same pin as CI. Index digest so amd64 and arm64 both resolve.
FROM ghcr.io/astral-sh/uv:0.13.10@sha256:a3ea1357342c1a4bf9a0c53a3deb1b55379aefa037338d8113d5771efa01774f AS uv

# python 3.13.16-slim-trixie; index digest so amd64 and arm64 both resolve.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90 AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

#-------------------------------------------

FROM base AS build

COPY --from=uv /uv /uvx /bin/

# uv's cache goes to a cache mount. Hugging Face's cache stays under /root, outside the trees
# the final stages copy. The models and MinerU's config land in $HOME/.mineru, downloaded again
# whenever their layer rebuilds.
ENV UV_CACHE_DIR=/root/.cache/uv \
    UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/root/.cache/huggingface \
    HOME=/home/mineru

FROM build AS cpu-build

# The mineru and docvortex pins are checked against MINERU_VERSION and DOCVORTEX_VERSION.
# mineru takes any docvortex 0.6.x, the library that builds its middle.json: pinned so a new
# build gives the output the fixtures were made with.
RUN --mount=type=cache,id=uv-mineru,target=/root/.cache/uv,sharing=locked \
    uv venv /opt/mineru \
    && uv pip install --python /opt/mineru/bin/python "mineru==4.2.3" "docvortex==0.6.24"

# g=u gives OpenShift's random UID (group 0) the owner's access.
RUN /opt/mineru/bin/mineru-models-download --tier basic --small-backend onnx -s huggingface \
    && /opt/mineru/bin/mineru-kit models verify --tier basic --small-backend onnx \
    && chmod -R g=u /home/mineru

FROM build AS gpu-build

# full = torch and vLLM. vLLM pins its torch, so no torch index is chosen here: the PyPI
# wheels (vLLM 0.30, torch 2.13) are CUDA 13 builds and need an NVIDIA driver for CUDA 13.
RUN --mount=type=cache,id=uv-mineru,target=/root/.cache/uv,sharing=locked \
    uv venv /opt/mineru \
    && uv pip install --python /opt/mineru/bin/python "mineru[full]==4.2.3" "docvortex==0.6.24"

# Fail the build if torch is not a CUDA build or vLLM does not import.
RUN /opt/mineru/bin/python -c "import torch, vllm; assert torch.version.cuda, torch.__version__"

RUN /opt/mineru/bin/mineru-models-download --tier standard --small-backend torch \
        --vlm-engine vllm -s huggingface \
    && /opt/mineru/bin/mineru-kit models verify --tier standard --small-backend torch \
        --vlm-engine vllm \
    && chmod -R g=u /home/mineru

#-------------------------------------------

FROM base AS runtime

# Numeric user: OpenShift runs the container as a random UID in group 0.
RUN groupadd --system --gid 10001 mineru \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/mineru --no-create-home \
        --shell /usr/sbin/nologin mineru \
    && install -d -o 10001 -g 0 -m 0770 /home/mineru

# Uploads and results are kept for an hour, not MinerU's default day: callers download the
# result as soon as the task ends. The interactive API docs are off. MINERU_API_CONCURRENCY is
# how many parse jobs run at once; more wait in MinerU's queue. Each running job needs its own
# host memory. MINERU_API_TASK_TIMEOUT stops one parse after that many seconds of parsing (queue
# time not counted). A parse that reaches it makes mineru-api refuse all work (503 on
# /v1/health) until the container restarts. Keep it above the client's wait so the client
# cancels its task first. 3900 is above the 3600 second wait used when no limit is passed.
ENV PATH="/opt/mineru/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOME=/home/mineru \
    MINERU_MODEL_SOURCE=local \
    HF_HUB_OFFLINE=1 \
    MINERU_API_RETENTION_SECONDS=3600 \
    MINERU_API_ENABLE_FASTAPI_DOCS=0 \
    MINERU_API_CONCURRENCY=1 \
    MINERU_API_TASK_TIMEOUT=3900

WORKDIR /home/mineru
USER 10001:10001

EXPOSE 8000

# /v1/health answers once the preloaded models are ready.
HEALTHCHECK --interval=10s --timeout=5s --start-period=120s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3)"]

# mineru-api reads no variable for --concurrency or --task-timeout, so sh passes them, quoted:
# each value stays one argument. exec replaces sh with mineru-api, and "$@" is the CMD.
ENTRYPOINT ["/usr/bin/tini", "--", "/bin/sh", "-c", \
    "exec mineru-api --host 0.0.0.0 --port 8000 --preload-models --concurrency \"$MINERU_API_CONCURRENCY\" --task-timeout \"$MINERU_API_TASK_TIMEOUT\" \"$@\"", \
    "mineru-api"]

#-------------------------------------------

FROM runtime AS gpu

# Triton, which runs vLLM's kernels, builds a C launcher with the system compiler when the engine
# starts; g++ is for torch's and FlashInfer's own JIT builds.
USER 0
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc g++ libc6-dev \
    && rm -rf /var/lib/apt/lists/*
USER 10001:10001

COPY --chown=10001:0 --from=gpu-build /home/mineru /home/mineru
COPY --from=gpu-build /opt/mineru /opt/mineru

# No usage reports from vLLM, and its PyTorch sampler: FlashInfer's JIT-compiles CUDA kernels
# at first use.
ENV MINERU_MODEL_SMALL_BACKEND=torch \
    MINERU_MODEL_VLM_ENGINE=vllm \
    VLLM_NO_USAGE_STATS=1 \
    VLLM_USE_FLASHINFER_SAMPLER=0

# Runs as uid 10001, after the trees are copied together.
RUN mineru-kit models verify --tier standard \
    && python -c "import torch, vllm; assert torch.version.cuda, torch.__version__" \
    && gcc --version >/dev/null

# Loading the VLM into vLLM takes longer than the small models alone.
HEALTHCHECK --interval=10s --timeout=5s --start-period=600s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=3)"]

# The tier ceiling the service serves: Advanced and every tier below it. Since MinerU 4.1 a
# Standard ceiling no longer serves Advanced. Advanced runs on Standard's models, the ones
# verified above.
CMD ["--tier", "advanced"]

FROM runtime AS cpu

COPY --chown=10001:0 --from=cpu-build /home/mineru /home/mineru
COPY --from=cpu-build /opt/mineru /opt/mineru

# ONNX runs on the CPU. Without MINERU_MODEL_ONNX_DEVICE, MinerU 4.1+ probes for a GPU at the
# first parse, may time a calibration run on one, and writes its choice to its config file.
ENV MINERU_MODEL_SMALL_BACKEND=onnx \
    MINERU_MODEL_ONNX_DEVICE=cpu

# Runs as uid 10001, after the trees are copied together.
RUN mineru-kit models verify --tier basic

# The tier ceiling the service serves: Basic, and Flash below it.
CMD ["--tier", "basic"]
