"""Parser backend names.

Kept outside `bioparser.parser` on purpose: importing anything from that package
loads every backend (pdfplumber, MinerU mapper). Code that only needs to know
which names are valid, such as job state, can import this module cheaply.
"""

from collections.abc import Mapping
from types import MappingProxyType

DEFAULT_PARSER_NAME = "default"
MINERU_PARSER_NAME = "mineru"

# Bump a backend's version when its output changes. A new version gets a new parse-result
# path, so results from the old version are not reused.
PARSER_VERSIONS: Mapping[str, str] = MappingProxyType(
    {DEFAULT_PARSER_NAME: "1", MINERU_PARSER_NAME: "1"}
)
PARSER_BACKENDS = tuple(PARSER_VERSIONS)
