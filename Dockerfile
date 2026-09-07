FROM python:3.13-slim

# TODO: ENV UV compile bytecode etc

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
COPY README.md ./

RUN uv sync --frozen --no-dev
# TODO: run as non-root

EXPOSE 8080

CMD ["uv", "run", "--no-dev", "uvicorn", "bioparser.app:app", "--host", "0.0.0.0", "--port", "8080"]
