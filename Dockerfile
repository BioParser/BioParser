# python 3.13.15-slim-trixie; index digest so amd64 and arm64 both resolve
FROM python:3.13-slim@sha256:9d2e5553305c7c7b0097999bb17187c69b921ccd6bc9d40e4bb5ebe652c00285

# TODO: ENV UV compile bytecode etc

WORKDIR /app

# uv 0.12.9; index digest so amd64 and arm64 both resolve
COPY --from=ghcr.io/astral-sh/uv:0.12.9@sha256:8b940d3a9d65bed080436972241af2e21c84b5e8c9193f7014ed71479ee795ff /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./

RUN uv sync --frozen --no-dev
# TODO: run as non-root

EXPOSE 8080

CMD ["uv", "run", "--no-dev", "uvicorn", "bioparser.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
