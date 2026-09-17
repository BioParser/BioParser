from fastapi import HTTPException, UploadFile

from . import config
from .errors import (
    EMPTY_FILE,
    INVALID_CONTENT_LENGTH,
    INVALID_PDF,
    MISSING_FILE,
    UNSUPPORTED_CONTENT_TYPE,
    http_error,
)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_PDF_MAGIC = b"%PDF-"
CONTENT_TOO_LARGE = "Content Too Large"


def require_file(file: UploadFile | None) -> UploadFile:
    if file is None:
        raise http_error(400, MISSING_FILE)
    return file


def validate_content_type(file: UploadFile) -> None:
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type != "application/pdf":
        raise http_error(415, UNSUPPORTED_CONTENT_TYPE)


def validate_content_length(header: str | None) -> None:
    if header is None:
        return
    try:
        length = int(header)
    except ValueError:
        raise http_error(400, INVALID_CONTENT_LENGTH) from None
    if length < 0:
        raise http_error(400, INVALID_CONTENT_LENGTH)
    if length > config.get_api_settings().max_upload_bytes:
        raise HTTPException(status_code=413, detail=CONTENT_TOO_LARGE)


async def read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > config.get_api_settings().max_upload_bytes:
            raise HTTPException(status_code=413, detail=CONTENT_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def validate_pdf_content(content: bytes) -> None:
    if not content:
        raise http_error(400, EMPTY_FILE)
    if not content.startswith(_PDF_MAGIC):
        raise http_error(400, INVALID_PDF)
