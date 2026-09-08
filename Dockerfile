# python 3.13.15-slim-trixie; index digest so amd64 and arm64 both resolve
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285 AS builder

# TODO: ENV UV compile bytecode etc
ENV UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# uv 0.12.9; index digest so amd64 and arm64 both resolve
COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable
#-------------------------------------------
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

# Create an unprivileged user
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 \
    --no-create-home

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"
# Copy the virtual environment and give ownership to the non-root user
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Run as non-root
USER app:app

EXPOSE 8080

CMD ["uvicorn", "bioparser.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
