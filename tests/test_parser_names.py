from typing import get_args

from bioparser.parser_names import PARSER_VERSIONS, ParserName


def test_parser_name_literal_matches_parser_versions() -> None:
    # The Literal is what the API accepts; PARSER_VERSIONS is what the backends run.
    assert set(get_args(ParserName)) == set(PARSER_VERSIONS)
