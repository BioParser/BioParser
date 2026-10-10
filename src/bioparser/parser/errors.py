class UnsupportedDocumentError(ValueError):
    """Raised when a PDF cannot be parsed (no usable content, malformed file, or other document problem)."""


class ParserTimeoutError(RuntimeError):
    """Raised when a backend ran past its time limit and was stopped."""


class ParserProcessError(RuntimeError):
    """Raised when a backend process was killed, crashed or exited non-zero, so the PDF was not judged.

    Not a document problem: the same PDF may parse once the machine has room again.
    """


class ParserBackendUnavailableError(RuntimeError):
    """Raised when the requested backend is unknown or cannot be run."""
