import pytest

from bioparser.api.uploads import sanitize_file_name
from bioparser.jobstate import JOB_FILE_NAME_MAX_LENGTH


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        (None, None),
        ("", None),
        ("paper.pdf", "paper.pdf"),
        (
            "folder/\x00subfolder\\paper\n.pdf",
            "folder/subfolder\\paper.pdf",
        ),
        ("\x00\n\t", None),
    ],
)
def test_sanitize_file_name(
    file_name: str | None,
    expected: str | None,
) -> None:
    assert sanitize_file_name(file_name) == expected


def test_sanitize_file_name_caps_length() -> None:
    file_name = "a" * (JOB_FILE_NAME_MAX_LENGTH + 10)

    assert sanitize_file_name(file_name) == "a" * JOB_FILE_NAME_MAX_LENGTH
