from fastapi import HTTPException, UploadFile

from . import config

_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_PDF_MAGIC = b"%PDF-"


def require_file(file: UploadFile | None) -> UploadFile:
    if file is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "missing_file", "message": "No file part in the request"},
        )
    return file


def validate_content_type(file: UploadFile) -> None:
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=415,
            detail={
                "code": "unsupported_content_type",
                "message": "Only application/pdf uploads are accepted",
            },
        )


async def read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > config.MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "file_too_large",
                    "message": f"File exceeds the {config.MAX_UPLOAD_BYTES} byte limit",
                },
            )
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
