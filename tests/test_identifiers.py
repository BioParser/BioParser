import pytest
from pydantic import BaseModel, ValidationError

from bioparser.identifiers import (
    ARTIFACT_PATH_MAX_LENGTH,
    ARTIFACT_PATH_MAX_SEGMENT_LENGTH,
    ArtifactPath,
    ArtifactPathError,
    ArtifactPathTraversalError,
    validate_artifact_path,
)

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
