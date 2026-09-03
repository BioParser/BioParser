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

- [Architecture](docs/architecture.md)
- [Definition of Done](docs/dod.md)
- [Collaboration practices](docs/collaboration.md)

Lint and format with Ruff. Type-check with mypy.

### Pre-commit
After cloning the repository, install the dependencies and pre-commit hooks:

```sh
uv sync
uv run pre-commit install
```
Once installed, the pre-commit hooks will run automatically before each commit.

To run all pre-commit hooks manually on the entire repository:
```sh
uv run pre-commit run --all-files
```

### Manual checks

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
