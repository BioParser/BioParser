"""Parser backend names.

Kept outside `bioparser.parser` on purpose: importing anything from that package
loads every backend (pdfplumber, MinerU mapper). Code that only needs to know
which names are valid, such as job state, can import this module cheaply.
"""

DEFAULT_PARSER_NAME = "default"
MINERU_PARSER_NAME = "mineru"
PARSER_BACKENDS = (DEFAULT_PARSER_NAME, MINERU_PARSER_NAME)
