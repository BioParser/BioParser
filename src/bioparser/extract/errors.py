class ExtractorUnavailableError(RuntimeError):
    """Raised when the requested extractor is unknown or cannot be run."""


class ExtractionFailedError(RuntimeError):
    """The model answered, but there were problems with the schema."""
