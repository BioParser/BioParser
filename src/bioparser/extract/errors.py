class ExtractionFailedError(RuntimeError):
    """Raised when this artifact cannot be extracted. The same artifact fails the same way every time."""


class ExtractorProcessError(RuntimeError):
    """Raised when the extractor did not finish, so the artifact was not judged.

    Not an artifact problem: the same artifact may extract once the extractor is available again.
    """


class ExtractorBackendUnavailableError(RuntimeError):
    """Raised when the requested extractor is unknown, cannot be run, or as deployed cannot serve
    the request (a retry would fail the same way)."""
