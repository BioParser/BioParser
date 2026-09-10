from fastapi import HTTPException, UploadFile

from . import config

_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_PDF_MAGIC = b"%PDF-"
CONTENT_TOO_LARGE = "Content Too Large"


def require_file(file: UploadFile | None) -> UploadFile:
    if file is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "missing_file", "message": "No file part in the request"},
        )
    return file


def validate_content_type(file: UploadFile) -> None:
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if content_type != "application/pdf":
        raise HTTPException(
            status_code=415,
            detail={
                "code": "unsupported_content_type",
                "message": "Only application/pdf uploads are accepted",
            },
        )


def validate_content_length(header: str | None) -> None:
    if header is None:
        return
    try:
        length = int(header)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_content_length",
                "message": "Content-Length header must be a non-negative integer",
            },
        ) from None
    if length < 0:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_content_length",
                "message": "Content-Length header must be a non-negative integer",
            },
        )
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
        raise HTTPException(
            status_code=400,
            detail={"code": "empty_file", "message": "Uploaded file is empty"},
        )
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_pdf", "message": "File does not look like a PDF"},
        )
