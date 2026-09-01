# BioParser

## Running

### Requirements

- **Python** `== 3.13` (backend)
- **uv** (package management)

### Dependencies

Download/update using `uv sync`
- FastAPI (API framework for the REST API)
- Uvicorn (ASGI web server)

#### Dev
- mypy (type checker)
- ruff (linter and formatter)
- Pytest (automated tests)

### Server
- A localhost (127.0.0.1) Uvicorn server can be started on port 8000 with `uv run bioparser`.

## Development

- [Definition of Done](docs/dod.md)
- [Collaboration practices](docs/collaboration.md)

Lint and format with Ruff. Type-check with mypy.

```bash
uv run ruff check
uv run ruff format
uv run mypy
```

Safe auto-fixes for lint:

```bash
uv run ruff check --fix
```

Run tests with Pytest:

`uv run pytest`
