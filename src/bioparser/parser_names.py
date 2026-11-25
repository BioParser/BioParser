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
# path, so results from the old version are not reused. default: 1 stored spans, 2 stores
# glyph edges, 3 repairs Advent symbol fonts. mineru: 2 is MinerU 4.0.12, 3 is MinerU 4.2.3
# with spans, 4 stores glyph edges, 5 repairs Advent symbol fonts.
PARSER_VERSIONS: Mapping[str, str] = MappingProxyType(
    {DEFAULT_PARSER_NAME: "3", MINERU_PARSER_NAME: "5"}
)
PARSER_BACKENDS = tuple(PARSER_VERSIONS)
