from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from bioparser.parser import (
    DEFAULT_PARSER_NAME,
    PARSER_BACKENDS,
    ParserArtifact,
    ParserBackendUnavailableError,
    UnsupportedDocumentError,
    get_parser,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class _Document:
    pdf_bytes: bytes
    artifacts: dict[str, ParserArtifact]


@dataclass
class _Store:
    backend: str = DEFAULT_PARSER_NAME
    document: _Document | None = field(default=None)


def _normalize_backend(name: str | None, fallback: str) -> str:
    chosen = fallback if name is None or name == "" else name
    if chosen not in PARSER_BACKENDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown parser backend {chosen!r}. Choose one of: {', '.join(PARSER_BACKENDS)}.",
        )
    return chosen


def _parse_pdf_bytes(pdf_bytes: bytes, backend: str) -> ParserArtifact:
    with NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
        tmp.write(pdf_bytes)
        tmp.flush()
        return get_parser(backend).parse(Path(tmp.name))


async def _parse_or_http_error(pdf_bytes: bytes, backend: str) -> ParserArtifact:
    try:
        return await run_in_threadpool(_parse_pdf_bytes, pdf_bytes, backend)
    except ParserBackendUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnsupportedDocumentError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _load_path(path: Path, backend: str) -> _Document:
    pdf_path = path.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    pdf_bytes = pdf_path.read_bytes()
    artifact = get_parser(backend).parse(pdf_path)
    return _Document(pdf_bytes=pdf_bytes, artifacts={backend: artifact})


def create_app(initial_pdf: Path | None = None, *, backend: str = DEFAULT_PARSER_NAME) -> FastAPI:
    store = _Store(backend=_normalize_backend(backend, DEFAULT_PARSER_NAME))
    if initial_pdf is not None:
        store.document = _load_path(initial_pdf, store.backend)

    app = FastAPI(title="BioParser PDF viewer", docs_url=None, redoc_url=None)

    def _cached_artifact(backend_name: str) -> ParserArtifact:
        if store.document is None:
            raise HTTPException(status_code=404, detail="No document loaded")
        artifact = store.document.artifacts.get(backend_name)
        if artifact is None:
            raise HTTPException(
                status_code=404,
                detail=f"No cached parse for backend {backend_name!r}. Re-parse to create one.",
            )
        return artifact

    @app.get("/cache.json")
    def get_cache() -> dict[str, object]:
        cached = list(store.document.artifacts) if store.document is not None else []
        return {"active": store.backend, "cached": cached}

    @app.get("/artifact.json")
    def get_artifact(request: Request) -> Response:
        backend_name = _normalize_backend(request.query_params.get("backend"), store.backend)
        artifact = _cached_artifact(backend_name)
        store.backend = backend_name
        return Response(content=artifact.model_dump_json(), media_type="application/json")

    @app.get("/document.pdf")
    def get_document() -> Response:
        if store.document is None:
            raise HTTPException(status_code=404, detail="No document loaded")
        return Response(content=store.document.pdf_bytes, media_type="application/pdf")

    @app.post("/parse")
    async def parse_upload(request: Request) -> Response:
        backend_name = _normalize_backend(request.query_params.get("backend"), store.backend)
        pdf_bytes = await request.body()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="Empty request body")
        artifact = await _parse_or_http_error(pdf_bytes, backend_name)
        store.backend = backend_name
        store.document = _Document(pdf_bytes=pdf_bytes, artifacts={backend_name: artifact})
        return Response(content=artifact.model_dump_json(), media_type="application/json")

    @app.post("/reparse")
    async def reparse_current(request: Request) -> Response:
        document = store.document
        if document is None:
            raise HTTPException(status_code=404, detail="No document loaded")
        backend_name = _normalize_backend(request.query_params.get("backend"), store.backend)
        artifact = await _parse_or_http_error(document.pdf_bytes, backend_name)
        store.backend = backend_name
        document.artifacts[backend_name] = artifact
        return Response(content=artifact.model_dump_json(), media_type="application/json")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
