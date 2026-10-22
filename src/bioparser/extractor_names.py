"""Extractor names.

Kept outside `bioparser.extract` on purpose: importing that package loads the
vLLM client. Code that only needs to know which names are valid can import this
module cheaply.
"""

from collections.abc import Mapping
from types import MappingProxyType

DEFAULT_EXTRACTOR_NAME = "default"

# Bump the version when the extractor's output changes. A new version gets a new
# extraction path, so results from the old version are not reused.
EXTRACTOR_VERSIONS: Mapping[str, str] = MappingProxyType({DEFAULT_EXTRACTOR_NAME: "1"})
EXTRACTORS = tuple(EXTRACTOR_VERSIONS)
