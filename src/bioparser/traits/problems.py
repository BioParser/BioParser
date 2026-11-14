"""What is wrong with one trait list, as data a tool can show next to the right field.

A list can be wrong by itself: a unit alias that names a unit no trait prints, two traits with
one code. ``ListProblem`` names the rule, the trait, the field and the spelling, and carries a
plain sentence.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

Rule = Literal[
    "not_json",
    "invalid_value",
    "invalid_code",
    "empty_text",
    "invalid_trait_id",
    "invalid_prefix",
    "invalid_iri",
    "invalid_factor",
    "no_printed_units",
    "levels_not_distinct",
    "duplicate_code",
    "duplicate_id",
    "duplicate_domain",
    "unknown_domain",
    "undeclared_prefix",
    "duplicate_unit",
    "trait_unit_not_printed",
    "approximate_not_printed",
    "duplicate_alias",
    "unit_alias_target_not_printed",
    "unit_alias_target_not_trait_unit",
    "unit_alias_target_unused",
    "level_alias_target_not_a_level",
    "level_alias_target_unused",
    "level_alias_takes_a_level",
    "level_alias_is_already_a_level",
]


@dataclass(frozen=True)
class ListProblem:
    """One thing wrong with a trait list.

    ``trait`` is the code, ``None`` when the problem is about the whole list. ``field`` is the
    JSON field, such as ``unit_aliases``. ``spelling`` is the alias or unit involved.
    """

    rule: Rule
    message: str
    trait: str | None = None
    field: str | None = None
    spelling: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)


class ListRuleError(ValueError):
    """Raised inside a validator. It carries the problems, so a tool can read them back."""

    def __init__(self, problems: Sequence[ListProblem]) -> None:
        super().__init__(" ".join(problem.message for problem in problems))
        self.problems = list(problems)
