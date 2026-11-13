"""Parser backend names.

Kept outside `bioparser.parser` on purpose: importing anything from that package
loads every backend (pdfplumber, MinerU mapper). Code that only needs to know
which names are valid, such as job state, can import this module cheaply.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, Literal

# The names the API accepts. A Literal becomes an enum in the OpenAPI schema, so the
# frontend's generated types list the same names.
ParserName = Literal["default", "mineru"]

# Final keeps the Literal type: mypy sees Literal["default"], not str.
DEFAULT_PARSER_NAME: Final = "default"
MINERU_PARSER_NAME: Final = "mineru"

# Bump a backend's version when its output changes. A new version gets a new parse-result
# path, so results from the old version are not reused.
PARSER_VERSIONS: Mapping[str, str] = MappingProxyType(
    {DEFAULT_PARSER_NAME: "1", MINERU_PARSER_NAME: "2"}
)
PARSER_BACKENDS = tuple(PARSER_VERSIONS)
