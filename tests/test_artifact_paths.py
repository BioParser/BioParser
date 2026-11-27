import json
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError

from bioparser.artifact_paths import (
    ARTIFACT_PATH_MAX_LENGTH,
    ARTIFACT_PATH_MAX_SEGMENT_LENGTH,
    ArtifactPath,
    ArtifactPathError,
    ArtifactPathTraversalError,
    extract_result_path,
    parse_result_path,
    pdf_path,
    validate_artifact_path,
    validate_path_segment,
)
from bioparser.hashing import sha256_bytes

CHECKSUM = "f2ca1bb6c7e907d06dafe4687e579fce76b37e4e93b7605022da52e6ccc26fd2"


class _Holder(BaseModel):
    path: ArtifactPath


@pytest.mark.parametrize(
    "path",
    [
        "a",
        "a/b/c/d/e/f/g",
        f"pdf/{CHECKSUM}",
        f"pdf/{CHECKSUM}/parsed/mineru/1.0",
        f"pdf/{CHECKSUM}/parsed/mineru/1.0/extracted/{CHECKSUM}",
        "doc_123.v1-final",
        "SimpleArtifactPath",
        "..hidden",
        "a..b",
        "a" * ARTIFACT_PATH_MAX_SEGMENT_LENGTH,
        "/".join(["a" * 150] * 3),
        "/".join(["a" * ARTIFACT_PATH_MAX_SEGMENT_LENGTH] * 2 + ["a" * 110]),
    ],
)
def test_valid_paths_are_returned_unchanged(path: str) -> None:
    assert validate_artifact_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        " ",
        "   ",
        "/a",
        "a/",
        "/",
        "//",
        "a//b",
        "has space",
        "has@symbol",
        "has:colon",
        "has\\backslash",
        "\\windows\\path",
        "C:\\Windows\\System32",
        "D:/escaped",
        "C:file",
        "~",
        "foo\0bar",
        "nisäkkäät_1.pdf",
        "a/b c/d",
        "a" * (ARTIFACT_PATH_MAX_SEGMENT_LENGTH + 1),
        "/".join(["a" * ARTIFACT_PATH_MAX_SEGMENT_LENGTH] * 3),
    ],
)
def test_malformed_paths_are_rejected(path: str) -> None:
    with pytest.raises(ArtifactPathError) as exc_info:
        validate_artifact_path(path)
    assert not isinstance(exc_info.value, ArtifactPathTraversalError)


def test_total_length_limit_is_enforced() -> None:
    at_limit = "/".join(["a" * 100] * 4 + ["a" * 108])
    assert len(at_limit) == ARTIFACT_PATH_MAX_LENGTH
    assert validate_artifact_path(at_limit) == at_limit

    too_long = "/".join(["a" * 100] * 6)
    assert len(too_long) > ARTIFACT_PATH_MAX_LENGTH
    with pytest.raises(ArtifactPathError, match="maximum length"):
        validate_artifact_path(too_long)


@pytest.mark.parametrize(
    "path",
    [
        ".",
        "..",
        "../escaped",
        "../../etc/passwd",
        "something/../../root",
        "subdir/../escaped",
        "./escaped",
        "a/./b",
        "a/..",
    ],
)
def test_dot_segments_raise_traversal_error(path: str) -> None:
    with pytest.raises(ArtifactPathTraversalError):
        validate_artifact_path(path)


@pytest.mark.parametrize("value", [None, 0, 123, b"pdf/abc", ["pdf", "abc"]])
def test_non_strings_are_rejected(value: object) -> None:
    with pytest.raises(ArtifactPathError, match="must be a string"):
        validate_artifact_path(value)  # type: ignore[arg-type]


def test_traversal_error_is_an_artifact_path_error_and_value_error() -> None:
    assert issubclass(ArtifactPathTraversalError, ArtifactPathError)
    assert issubclass(ArtifactPathError, ValueError)


def test_pydantic_field_accepts_valid_path() -> None:
    holder = _Holder(path=f"pdf/{CHECKSUM}")
    assert holder.path == f"pdf/{CHECKSUM}"


@pytest.mark.parametrize("path", ["", "a//b", "/a", "a/", "../a", "has space"])
def test_pydantic_field_rejects_malformed_path(path: str) -> None:
    with pytest.raises(ValidationError):
        _Holder(path=path)


@pytest.mark.parametrize("value", [None, 1, ["pdf", "abc"]])
def test_pydantic_field_rejects_non_strings(value: object) -> None:
    with pytest.raises(ValidationError):
        _Holder.model_validate({"path": value})


def test_pydantic_field_round_trips_json() -> None:
    original = _Holder(path=f"pdf/{CHECKSUM}/parsed/mineru/1.0")
    restored = _Holder.model_validate_json(original.model_dump_json())
    assert restored == original


def test_pdf_path_follows_the_convention() -> None:
    assert pdf_path(CHECKSUM) == f"pdf/{CHECKSUM}"


def test_parse_result_path_follows_the_convention() -> None:
    path = parse_result_path(CHECKSUM, ["mineru", "1"])
    assert path == f"pdf/{CHECKSUM}/parsed/mineru/1"
    assert path.startswith(pdf_path(CHECKSUM) + "/")


def test_parse_result_path_keeps_multi_segment_configuration() -> None:
    path = parse_result_path(CHECKSUM, ["mineru", "2", "standard"])
    assert path == f"pdf/{CHECKSUM}/parsed/mineru/2/standard"


def test_extract_result_path_keeps_the_parse_path() -> None:
    parsed = parse_result_path(CHECKSUM, ["mineru", "2", "standard"])
    path = extract_result_path(parsed, ["default", "0"])
    assert path == f"{parsed}/extracted/default/0"


def test_extract_result_path_appends_short_segments_unchanged() -> None:
    parsed = parse_result_path(CHECKSUM, ["mineru", "2", "standard"])
    job_id = UUID("3f2b8c1e-9a4d-4f6b-8c2e-1d5a7b9c0e34")
    assert extract_result_path(parsed, ["custom", "1", "prompt-v2"]) == (
        f"{parsed}/extracted/custom/1/prompt-v2"
    )
    assert extract_result_path(parsed, ["default", "2", str(job_id)]) == (
        f"{parsed}/extracted/default/2/{job_id}"
    )


def _expected_segments_digest(segments: list[str]) -> str:
    payload = json.dumps(segments, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(payload)


@pytest.mark.parametrize(
    "config",
    [
        "a" * (ARTIFACT_PATH_MAX_SEGMENT_LENGTH + 1),
        '{"model": "Qwen/Qwen3-0.6B", "prompt": "' + ("x" * 200) + '"}',
        "has/slash/" + ("x" * 200),
        "has space " + ("x" * 200),
    ],
)
def test_long_segment_hashes_the_backend_segments(config: str) -> None:
    segments = ["custom", "1", config]
    digest = _expected_segments_digest(segments)
    parsed = parse_result_path(CHECKSUM, segments)
    base_parsed = parse_result_path(CHECKSUM, ["default", "1"])
    extracted = extract_result_path(base_parsed, segments)

    assert parsed == f"pdf/{CHECKSUM}/parsed/{digest}"
    assert extracted == f"{base_parsed}/extracted/{digest}"


def test_path_exceeding_max_length_hashes_the_backend_segments() -> None:
    parser_segments = ["custom", "1", "a" * 180, "b" * 180, "c" * 180]
    parser_digest = _expected_segments_digest(parser_segments)
    parsed = parse_result_path(CHECKSUM, parser_segments)
    assert parsed == f"pdf/{CHECKSUM}/parsed/{parser_digest}"
    assert len(parsed) <= ARTIFACT_PATH_MAX_LENGTH

    base_parsed = parse_result_path(CHECKSUM, ["mineru", "3", "standard"])
    extract_segments = ["custom", "1", "a" * 200, "b" * 200]
    extract_digest = _expected_segments_digest(extract_segments)
    extracted = extract_result_path(base_parsed, extract_segments)
    assert extracted == f"{base_parsed}/extracted/{extract_digest}"
    assert len(extracted) <= ARTIFACT_PATH_MAX_LENGTH


def test_hashed_segments_preserve_segment_boundaries() -> None:
    first = parse_result_path(CHECKSUM, ["a/b", "x" * 201])
    second = parse_result_path(CHECKSUM, ["a", "b/" + ("x" * 201)])
    assert first != second


def test_too_long_parse_ref_still_rejects_extract_path() -> None:
    long_parsed = "/".join(["a" * 150] * 3 + ["b" * 42])
    assert len(validate_artifact_path(long_parsed)) == 495
    with pytest.raises(ArtifactPathError, match="maximum length"):
        extract_result_path(long_parsed, ["default", "1"])


@pytest.mark.parametrize(
    "bad_segments",
    [
        "default",
        b"default",
        [],
        [123],
        ["default", 1],
    ],
)
def test_builders_reject_non_sequence_or_empty_segments(bad_segments: object) -> None:
    parsed = parse_result_path(CHECKSUM, ["default", "1"])
    with pytest.raises(ArtifactPathError):
        parse_result_path(CHECKSUM, bad_segments)  # type: ignore[arg-type]
    with pytest.raises(ArtifactPathError):
        extract_result_path(parsed, bad_segments)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["", "a/b", "/a", "has space", "bad:char"])
def test_builders_reject_short_illegal_segments_without_hashing(bad: str) -> None:
    parsed = parse_result_path(CHECKSUM, ["default", "1"])
    with pytest.raises(ArtifactPathError):
        parse_result_path(CHECKSUM, [bad, "1"])
    with pytest.raises(ArtifactPathError):
        parse_result_path(CHECKSUM, ["mineru", "2", bad])
    if bad != "a/b":
        with pytest.raises(ArtifactPathError):
            extract_result_path(bad, ["default", "1"])
    with pytest.raises(ArtifactPathError):
        extract_result_path(parsed, [bad, "1"])
    with pytest.raises(ArtifactPathError):
        extract_result_path(parsed, ["custom", "1", bad])


def test_empty_segment_is_rejected_even_with_long_sibling_segment() -> None:
    parsed = parse_result_path(CHECKSUM, ["default", "1"])
    segments = ["custom", "", "x" * (ARTIFACT_PATH_MAX_SEGMENT_LENGTH + 1)]
    with pytest.raises(ArtifactPathError):
        parse_result_path(CHECKSUM, segments)
    with pytest.raises(ArtifactPathError):
        extract_result_path(parsed, segments)


@pytest.mark.parametrize("bad", [".", ".."])
def test_builders_reject_dot_segments(bad: str) -> None:
    long_segment = "x" * (ARTIFACT_PATH_MAX_SEGMENT_LENGTH + 1)
    with pytest.raises(ArtifactPathTraversalError):
        parse_result_path(CHECKSUM, [bad, "1"])
    with pytest.raises(ArtifactPathTraversalError):
        parse_result_path(CHECKSUM, ["mineru", "2", bad])
    with pytest.raises(ArtifactPathTraversalError):
        parse_result_path(CHECKSUM, ["mineru", bad, long_segment])
    parsed = parse_result_path(CHECKSUM, ["mineru", "2"])
    with pytest.raises(ArtifactPathTraversalError):
        extract_result_path(bad, ["default", "1"])
    with pytest.raises(ArtifactPathTraversalError):
        extract_result_path(parsed, [bad, "1"])
    with pytest.raises(ArtifactPathTraversalError):
        extract_result_path(parsed, ["custom", "1", bad])
    with pytest.raises(ArtifactPathTraversalError):
        extract_result_path(parsed, ["custom", bad, long_segment])


@pytest.mark.parametrize("segment", ["a", CHECKSUM, "doc_123.v1-final", "..."])
def test_valid_segments_are_returned_unchanged(segment: str) -> None:
    assert validate_path_segment(segment) == segment


@pytest.mark.parametrize(
    "bad", ["", "a/b", "/a", "has space", "a" * (ARTIFACT_PATH_MAX_SEGMENT_LENGTH + 1)]
)
def test_malformed_segments_are_rejected(bad: str) -> None:
    with pytest.raises(ArtifactPathError):
        validate_path_segment(bad)


@pytest.mark.parametrize("bad", [".", ".."])
def test_dot_segments_are_traversal_errors(bad: str) -> None:
    with pytest.raises(ArtifactPathTraversalError):
        validate_path_segment(bad)
