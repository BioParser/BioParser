class UnsupportedDocumentError(ValueError):
    """Raised when a PDF cannot be parsed (no usable content, malformed file, or other document problem)."""


class ParserTimeoutError(RuntimeError):
    """Raised when a backend ran past its time limit and was stopped."""


class ParserBackendUnavailableError(RuntimeError):
    """Raised when the requested backend is unknown or cannot be run."""
