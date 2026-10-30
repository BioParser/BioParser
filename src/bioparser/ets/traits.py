"""The mammal trait list: the thesaurus behind ``traitName``, ``traitID`` and the stored unit.

The list is data (``mammal_traits.json``), validated by the models below. The
artifact stores only the internal ``code``. The adapter is the one place that turns
a code into a name and an identifier, so a public identifier can change without
touching a stored artifact.

Rules that keep old results valid:

- A code keeps its meaning. A change to the measurement or the stored unit is a new
  code and a new identifier.
- A retired code is marked ``deprecated``. It still converts. Only a code that is
  absent from the list is ``trait_unknown``.
"""

import json
import re
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib import resources
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_TRAIT_ID = re.compile(r"^[A-Za-z]+:\d{7}$")

Status = Literal["active", "deprecated"]


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    name: str = Field(min_length=1)
    trait_id: str
    definition: str = Field(min_length=1)
    domain: str
    status: Status = "active"
    replaced_by: str | None = None

    @field_validator("code")
    @classmethod
    def _code_shape(cls, value: str) -> str:
        if _CODE.fullmatch(value) is None:
            raise ValueError("code is lowercase words joined by underscores")
        return value

    @field_validator("trait_id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if _TRAIT_ID.fullmatch(value) is None:
            raise ValueError("trait_id is PREFIX:0000000")
        return value

    @model_validator(mode="after")
    def _replacement_needs_deprecation(self) -> Self:
        if self.replaced_by is not None and self.status != "deprecated":
            raise ValueError("replaced_by is only for a deprecated trait")
        return self


class NumericTrait(_Entry):
    """A measured quantity. Each printed unit maps to the factor into ``stored_unit``."""

    kind: Literal["numeric"] = "numeric"
    stored_unit: str = Field(min_length=1)
    printed_units: dict[str, str]

    @field_validator("printed_units")
    @classmethod
    def _factors_are_positive(cls, value: dict[str, str]) -> dict[str, str]:
        if not value:
            raise ValueError("a numeric trait accepts at least one printed unit")
        for unit, factor in value.items():
            try:
                parsed = Decimal(factor)
            except InvalidOperation as error:
                raise ValueError(f"factor for {unit!r} is not a decimal") from error
            if not parsed.is_finite() or parsed <= 0:
                raise ValueError(f"factor for {unit!r} is not positive")
        return value

    @model_validator(mode="after")
    def _stored_unit_is_accepted_as_is(self) -> Self:
        if Decimal(self.printed_units.get(self.stored_unit, "0")) != 1:
            raise ValueError("stored_unit must be a printed unit with factor 1")
        return self

    def factor_for(self, unit: str) -> Decimal | None:
        """The multiplier from a printed unit into the stored unit, or None."""
        factor = self.printed_units.get(unit)
        return None if factor is None else Decimal(factor)


class CountTrait(_Entry):
    """A whole number of things. It is never converted, so the printed unit is not checked."""

    kind: Literal["count"] = "count"
    stored_unit: Literal["individuals"] = "individuals"


class CategoricalTrait(_Entry):
    """One of a closed set of levels. It has no unit."""

    kind: Literal["categorical"] = "categorical"
    levels: tuple[str, ...]

    @field_validator("levels")
    @classmethod
    def _levels_are_distinct(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        folded = [level.casefold() for level in value]
        if len(value) < 2 or len(set(folded)) != len(folded) or "" in folded:
            raise ValueError("levels are two or more distinct non-empty strings")
        return value

    def level_for(self, text: str) -> str | None:
        """The allowed level that matches the text, ignoring case."""
        folded = text.casefold().strip()
        return next((level for level in self.levels if level.casefold() == folded), None)


Trait = Annotated[NumericTrait | CountTrait | CategoricalTrait, Field(discriminator="kind")]


class Domain(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class TraitTable(BaseModel):
    """The whole list: a version, the identifier prefixes, the domains, and the traits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(
        pattern=r"^\d{4}\.\d{2}\.\d+$",
        description=(
            "YYYY.MM.REVISION. The year and month the release was made, then a revision "
            "that starts at 0 and counts further releases in that month."
        ),
    )
    id_prefixes: dict[str, str]
    domains: tuple[Domain, ...]
    traits: tuple[Trait, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        codes = [trait.code for trait in self.traits]
        ids = [trait.trait_id for trait in self.traits]
        if len(set(codes)) != len(codes):
            raise ValueError("trait codes are not unique")
        if len(set(ids)) != len(ids):
            raise ValueError("trait ids are not unique")
        domains = {domain.name for domain in self.domains}
        if len(domains) != len(self.domains):
            raise ValueError("domain names are not unique")
        for trait in self.traits:
            if trait.domain not in domains:
                raise ValueError(f"{trait.code}: unknown domain {trait.domain!r}")
            if trait.trait_id.split(":")[0] not in self.id_prefixes:
                raise ValueError(f"{trait.code}: id prefix is not declared")
            if trait.replaced_by is not None and trait.replaced_by not in codes:
                raise ValueError(f"{trait.code}: replaced_by names an unknown code")
        return self

    def get(self, code: str) -> NumericTrait | CountTrait | CategoricalTrait | None:
        """The entry for an internal code, or None when the code is off the list."""
        return next((trait for trait in self.traits if trait.code == code), None)

    def select(
        self, *, domains: tuple[str, ...] = (), codes: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        """Codes in the named domains plus the named codes. An unknown name is an error.

        Deprecated traits are left out of a domain selection. A deprecated code can
        still be named directly.
        """
        known_domains = {domain.name for domain in self.domains}
        unknown_domains = [name for name in domains if name not in known_domains]
        known_codes = {trait.code for trait in self.traits}
        unknown_codes = [code for code in codes if code not in known_codes]
        if unknown_domains or unknown_codes:
            raise ValueError(f"unknown domains {unknown_domains}, unknown codes {unknown_codes}")
        chosen = {
            trait.code
            for trait in self.traits
            if trait.domain in domains and trait.status == "active"
        }
        chosen.update(codes)
        return tuple(trait.code for trait in self.traits if trait.code in chosen)


_TABLE_ADAPTER = TypeAdapter(TraitTable)


def load_table(text: str) -> TraitTable:
    """Validate a trait list from JSON text."""
    return _TABLE_ADAPTER.validate_python(json.loads(text))


@cache
def mammal_traits() -> TraitTable:
    """The shipped mammal trait list."""
    text = resources.files("bioparser.ets").joinpath("mammal_traits.json").read_text("utf-8")
    return load_table(text)


def json_schema() -> dict[str, object]:
    """JSON Schema of the trait list file."""
    return _TABLE_ADAPTER.json_schema()
