"""Extractor names.

Kept outside `bioparser.extract` on purpose: importing that package loads the
vLLM client. Code that only needs to know which names are valid can import this
module cheaply.
"""

from collections.abc import Mapping
from types import MappingProxyType

DEFAULT_EXTRACTOR_NAME = "default"

# Version 0 is a placeholder. The real default extractor starts at 1 and does not
# reuse files written by this implementation.
EXTRACTOR_VERSIONS: Mapping[str, str] = MappingProxyType({DEFAULT_EXTRACTOR_NAME: "0"})
EXTRACTORS = tuple(EXTRACTOR_VERSIONS)
