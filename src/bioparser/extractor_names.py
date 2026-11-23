"""Extractor names.

Kept outside `bioparser.extract` on purpose: importing that package loads the
vLLM client. Code that only needs to know which names are valid can import this
module cheaply.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

# The names the API accepts. A Literal becomes an enum in the OpenAPI schema, so the
# frontend's generated types list the same names.
ExtractorName = Literal["default", "mendel"]

# Final keeps the Literal type: mypy sees Literal["default"], not str.
DEFAULT_EXTRACTOR_NAME: Final = "default"
MENDEL_EXTRACTOR_NAME: Final = "mendel"

# Bump the version when the extractor's output changes. A new version gets a new
# extraction path, so results from the old version are not reused.
EXTRACTOR_VERSIONS: Mapping[str, str] = MappingProxyType(
    {DEFAULT_EXTRACTOR_NAME: "2", MENDEL_EXTRACTOR_NAME: "1"}
)
EXTRACTORS = tuple(EXTRACTOR_VERSIONS)
