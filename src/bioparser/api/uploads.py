from __future__ import annotations

import unicodedata
import urllib.parse

from fastapi import UploadFile

from . import config
from .errors import (
    CONTENT_TOO_LARGE,
    EMPTY_FILE,
    INVALID_CONTENT_LENGTH,
    INVALID_PDF,
    MISSING_FILE,
    UNSUPPORTED_CONTENT_TYPE,
    http_error,
)

_CHUNK_SIZE = 1024 * 1024  # 1 MiB
_PDF_MAGIC = b"%PDF-"
_MAX_DISPLAY_NAME_LENGTH = 255


def sanitize_display_name(
    filename: str | None, max_length: int = _MAX_DISPLAY_NAME_LENGTH
) -> str | None:
    """Sanitize an uploaded filename for safe storage as display_name and HTTP header usage.

    Drops control characters (newlines, tabs, etc.), strips quotes ('"', "'"),
    trims whitespace, and caps length. Returns None if the resulting string is empty.
    """
    if not filename:
        return None
    unquoted = urllib.parse.unquote(filename)
    cleaned = "".join(
        c for c in unquoted if c not in ('"', "'") and not unicodedata.category(c).startswith("C")
    ).strip()
    if not cleaned:
        return None
    return cleaned[:max_length]


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
        raise http_error(413, CONTENT_TOO_LARGE)


async def read_upload(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_CHUNK_SIZE):
        total += len(chunk)
        if total > config.get_api_settings().max_upload_bytes:
            raise http_error(413, CONTENT_TOO_LARGE)
        chunks.append(chunk)
    return b"".join(chunks)


def validate_pdf_content(content: bytes) -> None:
    if not content:
        raise http_error(400, EMPTY_FILE)
    if not content.startswith(_PDF_MAGIC):
        raise http_error(400, INVALID_PDF)
