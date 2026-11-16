from pathlib import Path

from bioparser.hashing import is_sha256_hex, sha256_bytes, sha256_file


def test_sha256_file_matches_sha256_bytes(tmp_path: Path) -> None:
    payload = b"%PDF-1.4\nhello world\n%%EOF\n"
    path = tmp_path / "sample.pdf"
    path.write_bytes(payload)
    assert sha256_file(path) == sha256_bytes(payload)


def test_is_sha256_hex_valid() -> None:
    digest = sha256_bytes(b"hello world")
    assert is_sha256_hex(digest) is True
    assert is_sha256_hex("a" * 64) is True
    assert is_sha256_hex("0123456789abcdef" * 4) is True


def test_is_sha256_hex_invalid() -> None:
    assert is_sha256_hex("") is False
    assert is_sha256_hex("a" * 63) is False
    assert is_sha256_hex("a" * 65) is False
    assert is_sha256_hex("A" * 64) is False  # uppercase is rejected
    assert is_sha256_hex("g" * 64) is False  # non-hex
    assert is_sha256_hex(" " + "a" * 64) is False
