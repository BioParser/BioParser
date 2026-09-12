# CPU DEMO PURPOSES ONLY
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/

# defaults to CPU
ARG TORCH_BACKEND=cpu

ENV UV_SYSTEM_PYTHON=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_TORCH_BACKEND=${TORCH_BACKEND}

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install "mineru[pipeline]==3.4.5" "six>=1.17"

# smoke test
RUN python3 -c "from mineru.backend.pipeline.pipeline_analyze import doc_analyze_streaming"

RUN useradd --create-home --uid 10001 mineru
USER mineru

WORKDIR /home/mineru

ENV HOME=/home/mineru \
    MINERU_MODEL_SOURCE=local \
    PYTHONUNBUFFERED=1 \
    MINERU_API_OUTPUT_ROOT=/home/mineru/output

RUN mkdir -p /home/mineru/output

RUN mineru-models-download -s huggingface -m pipeline

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=90s --retries=5 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["mineru-api", "--host", "0.0.0.0", "--port", "8000"]
