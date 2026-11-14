"""The mammal trait list: the thesaurus behind ``traitName``, ``traitID`` and the trait unit.

The list is data (``mammal_traits.json``), validated by the models below. The
artifact stores only the internal ``code``. The adapter is the one place that turns
a code into a name and an identifier.

An observation with no code, or one the list cannot apply, is kept as printed.
The row has no name and no identifier.
"""

import json
import re
import weakref
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from functools import cache
from importlib import resources
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from bioparser.traits.normalize import (
    alias_keys,
    apply_unit,
    clean_unit,
    normalize_level,
    normalize_unit,
    same_unit,
)
from bioparser.traits.problems import ListProblem, ListRuleError

_CODE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_TRAIT_ID = re.compile(r"^[A-Za-z]+:[0-9]{7}$")
_ID_PREFIX = re.compile(r"^[A-Za-z]+$")
_IRI = re.compile(r"^https?://\S+$")


class _Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    name: str
    trait_id: str
    definition: str
    domain: str

    @model_validator(mode="after")
    def _shape_problems(self) -> Self:
        problems: list[ListProblem] = []
        if _CODE.fullmatch(self.code) is None:
            problems.append(
                ListProblem(
                    "invalid_code",
                    f"The trait code {self.code!r} must be lowercase words joined by "
                    "underscores, like body_mass.",
                    self.code,
                    "code",
                    self.code,
                )
            )
        if _TRAIT_ID.fullmatch(self.trait_id) is None:
            problems.append(
                ListProblem(
                    "invalid_trait_id",
                    f"{self.code} has the trait id {self.trait_id!r}, which must look like "
                    "PREFIX:0000000.",
                    self.code,
                    "trait_id",
                    self.trait_id,
                )
            )
        owner = self.code.strip()
        who = owner if owner else "A trait"
        problems.extend(
            problem
            for problem in (
                _blank_text(
                    self.name, f"{who} has an empty name.", trait=owner or None, field="name"
                ),
                _blank_text(
                    self.definition,
                    f"{who} has an empty definition.",
                    trait=owner or None,
                    field="definition",
                ),
            )
            if problem is not None
        )
        if problems:
            raise ListRuleError(problems)
        return self


class NumericTrait(_Entry):
    """A measured quantity. Each printed unit maps to the factor into ``trait_unit``."""

    kind: Literal["numeric"] = "numeric"
    trait_unit: str = Field(min_length=1)
    printed_units: dict[str, str]
    # Printed units whose factor is a convention, such as a year of 365.25 days.
    approximate_units: tuple[str, ...] = ()
    # A spelling only this trait accepts, mapped to one of its printed units. It wins over
    # a list-wide alias. An alias of a unit to itself turns the list-wide alias off here.
    unit_aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _factors_are_positive_decimals(self) -> Self:
        if not self.printed_units:
            raise ListRuleError(
                [
                    ListProblem(
                        "no_printed_units",
                        f"{self.code} accepts no printed units. Give it at least one, with its "
                        "factor into the trait unit.",
                        self.code,
                        "printed_units",
                    )
                ]
            )
        problems: list[ListProblem] = []
        for unit, factor in self.printed_units.items():
            try:
                parsed = Decimal(factor)
                valid = parsed.is_finite() and parsed > 0
            except InvalidOperation:
                valid = False
            if not valid:
                problems.append(
                    ListProblem(
                        "invalid_factor",
                        f"{self.code} has the factor {factor!r} for {unit!r}, which must be a "
                        "positive decimal number.",
                        self.code,
                        "printed_units",
                        unit,
                    )
                )
        if problems:
            raise ListRuleError(problems)
        return self

    def _aliases(self, shared: Mapping[str, str] | None) -> dict[str, str]:
        return {**(shared or {}), **self.unit_aliases}

    def printed_keys(self, shared: Mapping[str, str] | None = None) -> list[str]:
        """The key of each printed unit, with the aliases applied and case kept."""
        aliases = self._aliases(shared)
        return [normalize_unit(unit, aliases, casefold=False) for unit in self.printed_units]

    def unit_problems(self, shared: Mapping[str, str] | None) -> list[ListProblem]:
        """What is wrong with the units and aliases. ``shared`` is the list-wide aliases.

        The list calls this, because a spelling such as ``d`` for ``day`` can be a list-wide alias.
        """
        aliases = self._aliases(shared)
        problems = _distinct_problems(
            self.unit_aliases,
            clean_unit,
            owner=f"{self.code}, unit aliases",
            field="unit_aliases",
            trait=self.code,
        )
        keys = self.printed_keys(shared)
        first: dict[str, str] = {}
        for printed, key in zip(self.printed_units, keys, strict=True):
            if key in first:
                problems.append(
                    ListProblem(
                        "duplicate_unit",
                        f"{self.code} prints {first[key]!r} and {printed!r}, which are spellings "
                        "of the same unit. Keep one.",
                        self.code,
                        "printed_units",
                        printed,
                    )
                )
            first.setdefault(key, printed)
        if self.factor_for(self.trait_unit, shared) != 1:
            problems.append(
                ListProblem(
                    "trait_unit_not_printed",
                    f"{self.code} has the trait unit {self.trait_unit!r}, which must be one of its "
                    "printed units with factor 1.",
                    self.code,
                    "trait_unit",
                    self.trait_unit,
                )
            )
        for unit in self.approximate_units:
            if normalize_unit(unit, aliases, casefold=False) not in keys:
                problems.append(
                    ListProblem(
                        "approximate_not_printed",
                        f"{self.code} marks {unit!r} as approximate, but approximate units "
                        "must be printed units of the trait.",
                        self.code,
                        "approximate_units",
                        unit,
                    )
                )
        for spelling, unit in self.unit_aliases.items():
            if normalize_unit(unit, aliases, casefold=False) not in keys:
                problems.append(
                    ListProblem(
                        "unit_alias_target_not_printed",
                        f"{self.code} has the unit alias {spelling!r} for {unit!r}, but the "
                        "trait does not print that unit.",
                        self.code,
                        "unit_aliases",
                        spelling,
                    )
                )
        return problems

    def _index(self, shared: Mapping[str, str] | None) -> "_UnitIndex":
        key = (id(self), id(shared) if shared is not None else 0)
        found = _UNIT_INDEXES.get(key)
        if found is None:
            found = _UnitIndex(self._aliases(shared), self.printed_units)
            _keep(_UNIT_INDEXES, self, key, found)
        return found

    def matched_unit(self, unit: str, shared: Mapping[str, str] | None = None) -> str | None:
        """The printed unit that a spelling stands for, matched with its case first."""
        return self._index(shared).lookup(unit)

    def factor_for(self, unit: str, shared: Mapping[str, str] | None = None) -> Decimal | None:
        """The multiplier from a printed unit into the trait unit, or None.

        The unit may be spelled any common way, such as ``Kg`` or ``kilograms``. ``shared``
        is the list-wide unit aliases. The ``TraitTable`` passes its own.
        """
        printed = self.matched_unit(unit, shared)
        return None if printed is None else Decimal(self.printed_units[printed])

    def is_approximate(self, unit: str, shared: Mapping[str, str] | None = None) -> bool:
        """True when the factor for this printed unit is a convention."""
        printed = self.matched_unit(unit, shared)
        if printed is None:
            return False
        aliases = self._aliases(shared)
        key = normalize_unit(printed, aliases, casefold=False)
        return any(
            normalize_unit(item, aliases, casefold=False) == key for item in self.approximate_units
        )


class CountTrait(_Entry):
    """A number of things, such as young or teats.

    It is never converted, so the printed unit is not checked. It is a whole number, unless
    the paper prints a mean or a median of counts. ``trait_unit`` names what is counted.
    """

    kind: Literal["count"] = "count"
    trait_unit: str = Field(min_length=1)
    # A spelling only this trait accepts for ``trait_unit``.
    unit_aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _aliases_name_the_unit(self) -> Self:
        problems = _distinct_problems(
            self.unit_aliases,
            clean_unit,
            owner=f"{self.code}, unit aliases",
            field="unit_aliases",
            trait=self.code,
        )
        for spelling, unit in self.unit_aliases.items():
            if not same_unit(unit, self.trait_unit, self.unit_aliases):
                problems.append(
                    ListProblem(
                        "unit_alias_target_not_trait_unit",
                        f"{self.code} has the unit alias {spelling!r} for {unit!r}, which is "
                        f"not its unit {self.trait_unit!r}.",
                        self.code,
                        "unit_aliases",
                        spelling,
                    )
                )
        if problems:
            raise ListRuleError(problems)
        return self

    def _index(self, shared: Mapping[str, str] | None) -> "_CountIndex":
        key = (id(self), id(shared) if shared is not None else 0)
        found = _COUNT_INDEXES.get(key)
        if found is None:
            aliases = {**(shared or {}), **self.unit_aliases}
            found = _CountIndex(self.trait_unit, aliases)
            _keep(_COUNT_INDEXES, self, key, found)
        return found

    def accepts(self, unit: str, shared: Mapping[str, str] | None = None) -> bool:
        """True when ``unit`` is this count's unit, aliases and case applied."""
        return self._index(shared).accepts(unit)


class CategoricalTrait(_Entry):
    """One of a closed set of levels. It has no unit."""

    kind: Literal["categorical"] = "categorical"
    levels: tuple[str, ...]
    # A printed answer mapped to the level it stands for. It wins over a list-wide alias, so a
    # plain "yes" can stand for a trait's more specific level. A level mapped to itself turns
    # a list-wide alias of that spelling off for this trait.
    level_aliases: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _levels_are_distinct(self) -> Self:
        keys = [normalize_level(level) for level in self.levels]
        if len(keys) < 2 or len(set(keys)) != len(keys) or "" in keys:
            raise ListRuleError(
                [
                    ListProblem(
                        "levels_not_distinct",
                        f"{self.code} needs two or more distinct, non-empty levels. Levels that "
                        "differ only in case or separators count as the same level.",
                        self.code,
                        "levels",
                    )
                ]
            )
        return self

    @model_validator(mode="after")
    def _aliases_name_levels(self) -> Self:
        keys = {normalize_level(level) for level in self.levels}
        problems = _distinct_problems(
            self.level_aliases,
            normalize_level,
            owner=f"{self.code}, level aliases",
            field="level_aliases",
            trait=self.code,
        )
        for printed, level in self.level_aliases.items():
            if normalize_level(level) not in keys:
                problems.append(
                    ListProblem(
                        "level_alias_target_not_a_level",
                        f"{self.code} has the level alias {printed!r} for {level!r}, an unknown "
                        "level of this trait.",
                        self.code,
                        "level_aliases",
                        printed,
                    )
                )
            # A level may only be aliased to itself. That switches a list-wide alias off here.
            elif normalize_level(printed) in keys and normalize_level(printed) != normalize_level(
                level
            ):
                problems.append(
                    ListProblem(
                        "level_alias_is_already_a_level",
                        f"{self.code} has the level alias {printed!r} for {level!r}, but "
                        f"{printed!r} is already a level, so it can only map to itself.",
                        self.code,
                        "level_aliases",
                        printed,
                    )
                )
        if problems:
            raise ListRuleError(problems)
        return self

    def overrides_level_alias(self, spelling: str) -> bool:
        """True when this trait maps the spelling, which is one of its levels, to itself."""
        key = normalize_level(spelling)
        return any(
            normalize_level(printed) == key and normalize_level(level) == key
            for printed, level in self.level_aliases.items()
        )

    def level_for(self, text: str, shared: Mapping[str, str] | None = None) -> str | None:
        """The allowed level that matches the text, ignoring case and separators.

        ``shared`` is the list-wide level aliases, such as ``yes`` for ``Y``. An alias of this
        trait wins over a list-wide one for the same printed form.
        """
        wanted = normalize_level(text, {**(shared or {}), **self.level_aliases})
        return next((level for level in self.levels if normalize_level(level) == wanted), None)


Trait = Annotated[NumericTrait | CountTrait | CategoricalTrait, Field(discriminator="kind")]


class Domain(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    definition: str

    @model_validator(mode="after")
    def _text_is_present(self) -> Self:
        name = self.name.strip()
        who = name if name else "A domain"
        problems = [
            problem
            for problem in (
                _blank_text(self.name, "A domain has an empty name.", trait=None, field="name"),
                _blank_text(
                    self.definition,
                    f"{who} has an empty definition.",
                    trait=None,
                    field="definition",
                ),
            )
            if problem is not None
        ]
        if problems:
            raise ListRuleError(problems)
        return self


class TraitTable(BaseModel):
    """The whole list: a version, the identifier prefixes, the domains, and the traits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(
        pattern=r"^\d{4}\.\d{2}\.\d+$",
        description=(
            "YYYY.MM.REVISION. The year and month the list was named, then a revision "
            "that starts at 0."
        ),
    )
    # Prefix to IRI. None means the identifiers with that prefix do not resolve yet.
    id_prefixes: dict[str, str | None]
    # Spellings every numeric trait accepts, mapped to the unit they stand for. The target is a
    # printed unit of some trait, or one side of a printed unit, such as ``ha`` in
    # ``individuals/ha``.
    unit_aliases: dict[str, str] = Field(default_factory=dict)
    # Printed answers every categorical trait accepts, mapped to the level they stand for. An
    # alias only applies to a trait that has that level.
    level_aliases: dict[str, str] = Field(default_factory=dict)
    domains: tuple[Domain, ...]
    traits: tuple[Trait, ...]

    @field_validator("id_prefixes")
    @classmethod
    def _prefixes_are_letters_and_iris_are_addresses(
        cls, value: dict[str, str | None]
    ) -> dict[str, str | None]:
        problems: list[ListProblem] = []
        for prefix, iri in value.items():
            if _ID_PREFIX.fullmatch(prefix) is None:
                problems.append(
                    ListProblem(
                        "invalid_prefix",
                        f"The id prefix {prefix!r} must be letters only, like VT.",
                        None,
                        "id_prefixes",
                        prefix,
                    )
                )
            if iri is not None and _IRI.fullmatch(iri) is None:
                problems.append(
                    ListProblem(
                        "invalid_iri",
                        f"The address for the id prefix {prefix!r} must be null or start with "
                        "http:// or https://.",
                        None,
                        "id_prefixes",
                        prefix,
                    )
                )
        if problems:
            raise ListRuleError(problems)
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        problems = [*self._structure_problems(), *self._alias_problems()]
        if problems:
            raise ListRuleError(problems)
        return self

    def _structure_problems(self) -> list[ListProblem]:
        problems: list[ListProblem] = []
        problems.extend(
            ListProblem(
                "duplicate_code",
                f"Trait codes are not unique: {code!r} is used more than once.",
                code,
                "code",
                code,
            )
            for code in _repeated([trait.code for trait in self.traits])
        )
        problems.extend(
            ListProblem(
                "duplicate_id",
                f"Trait ids are not unique: {trait_id!r} is used more than once.",
                None,
                "trait_id",
                trait_id,
            )
            for trait_id in _repeated([trait.trait_id for trait in self.traits])
        )
        problems.extend(
            ListProblem(
                "duplicate_domain",
                f"Domain names are not unique: {name!r} is used more than once.",
                None,
                "domains",
                name,
            )
            for name in _repeated([domain.name for domain in self.domains])
        )
        domains = {domain.name for domain in self.domains}
        for trait in self.traits:
            if trait.domain not in domains:
                problems.append(
                    ListProblem(
                        "unknown_domain",
                        f"{trait.code} uses the unknown domain {trait.domain!r}. Add it to "
                        "domains or fix the name.",
                        trait.code,
                        "domain",
                        trait.domain,
                    )
                )
            prefix = trait.trait_id.split(":")[0]
            if prefix not in self.id_prefixes:
                problems.append(
                    ListProblem(
                        "undeclared_prefix",
                        f"{trait.code} has the id prefix {prefix!r}, which has no entry in the "
                        "list of id prefixes. Add the prefix there, with its IRI or null.",
                        trait.code,
                        "trait_id",
                        prefix,
                    )
                )
        return problems

    def _alias_problems(self) -> list[ListProblem]:
        problems = [
            *_distinct_problems(
                self.unit_aliases,
                clean_unit,
                owner="The list-wide unit aliases",
                field="unit_aliases",
                trait=None,
            ),
            *_distinct_problems(
                self.level_aliases,
                normalize_level,
                owner="The list-wide level aliases",
                field="level_aliases",
                trait=None,
            ),
        ]
        unit_sides: set[str] = set()
        for trait in self.traits:
            if isinstance(trait, NumericTrait):
                problems.extend(trait.unit_problems(self.unit_aliases))
                for key in trait.printed_keys(self.unit_aliases):
                    unit_sides.add(key)
                    unit_sides.update(key.split("/"))
            elif isinstance(trait, CountTrait):
                key = normalize_unit(
                    trait.trait_unit, {**self.unit_aliases, **trait.unit_aliases}, casefold=False
                )
                unit_sides.add(key)
                unit_sides.update(key.split("/"))
        for spelling, unit in self.unit_aliases.items():
            if clean_unit(unit) not in unit_sides:
                problems.append(
                    ListProblem(
                        "unit_alias_target_unused",
                        f"The unit alias {spelling!r} names {unit!r}, which no trait prints. "
                        "Remove it or print the unit on a trait.",
                        None,
                        "unit_aliases",
                        spelling,
                    )
                )
        categorical = [t for t in self.traits if isinstance(t, CategoricalTrait)]
        level_keys_all = {normalize_level(level) for t in categorical for level in t.levels}
        for spelling, level in self.level_aliases.items():
            if normalize_level(level) not in level_keys_all:
                problems.append(
                    ListProblem(
                        "level_alias_target_unused",
                        f"The level alias {spelling!r} names {level!r}, which no trait has "
                        "as a level.",
                        None,
                        "level_aliases",
                        spelling,
                    )
                )
            for trait in categorical:
                has_level = normalize_level(spelling) in {normalize_level(i) for i in trait.levels}
                if has_level and not trait.overrides_level_alias(spelling):
                    problems.append(
                        ListProblem(
                            "level_alias_takes_a_level",
                            f"The level alias {spelling!r} is the name of a level of "
                            f"{trait.code}. That trait must map it to itself, with "
                            f"{spelling!r}: {spelling!r} in its level aliases, to keep it.",
                            trait.code,
                            "level_aliases",
                            spelling,
                        )
                    )
        return problems

    def factor_for(self, code: str, unit: str) -> Decimal | None:
        """The factor from a printed unit into the trait's stored unit, or None."""
        entry = self.get(code)
        if not isinstance(entry, NumericTrait):
            return None
        return entry.factor_for(unit, self.unit_aliases)

    def is_approximate(self, code: str, unit: str) -> bool:
        """True when the trait's factor for this printed unit is a convention."""
        entry = self.get(code)
        return isinstance(entry, NumericTrait) and entry.is_approximate(unit, self.unit_aliases)

    def level_for(self, code: str, text: str) -> str | None:
        """The trait's level a printed answer stands for, or None."""
        entry = self.get(code)
        if not isinstance(entry, CategoricalTrait):
            return None
        return entry.level_for(text, self.level_aliases)

    def accepts_unit(self, code: str, unit: str) -> bool:
        """True when the trait takes this printed unit, with the aliases applied."""
        entry = self.get(code)
        if isinstance(entry, NumericTrait):
            return entry.factor_for(unit, self.unit_aliases) is not None
        if isinstance(entry, CountTrait):
            return entry.accepts(unit, self.unit_aliases)
        return False

    def knows_unit(self, unit: str) -> bool:
        """True when some numeric trait prints this unit, or a count names it, aliases applied."""
        return any(self.accepts_unit(entry.code, unit) for entry in self.traits)

    def get(self, code: str) -> NumericTrait | CountTrait | CategoricalTrait | None:
        """The entry for an internal code, or None when the code is off the list."""
        codes = _CODE_INDEXES.get(id(self))
        if codes is None:
            codes = {trait.code: trait for trait in self.traits}
            _keep(_CODE_INDEXES, self, id(self), codes)
        return codes.get(code)

    def select(
        self, *, domains: tuple[str, ...] = (), codes: tuple[str, ...] = ()
    ) -> tuple[str, ...]:
        """Codes in the named domains plus the named codes. An unknown name is an error."""
        known_domains = {domain.name for domain in self.domains}
        unknown_domains = [name for name in domains if name not in known_domains]
        known_codes = {trait.code for trait in self.traits}
        unknown_codes = [code for code in codes if code not in known_codes]
        if unknown_domains or unknown_codes:
            raise ValueError(f"unknown domains {unknown_domains}, unknown codes {unknown_codes}")
        chosen = {trait.code for trait in self.traits if trait.domain in domains}
        chosen.update(codes)
        return tuple(trait.code for trait in self.traits if trait.code in chosen)


_TABLE_ADAPTER = TypeAdapter(TraitTable)


def list_problems(text: str) -> list[ListProblem]:
    """Everything wrong with a trait list in JSON text, or an empty list when it is sound."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        return [ListProblem("not_json", f"The list is not valid JSON: {error}.")]
    try:
        _TABLE_ADAPTER.validate_python(data)
    except ValidationError as error:
        return _problems_from(error, data)
    return []


_TAGS = {"numeric", "count", "categorical"}


def _problems_from(error: ValidationError, data: object) -> list[ListProblem]:
    problems: list[ListProblem] = []
    for item in error.errors():
        raised = (item.get("ctx") or {}).get("error")
        if isinstance(raised, ListRuleError):
            problems.extend(raised.problems)
            continue
        path = [part for part in item["loc"] if part not in _TAGS]
        trait = _trait_at(data, path)
        field = ".".join(
            str(part) for part in path if not isinstance(part, int) and part != "traits"
        )
        label = field.replace("_", " ")
        message = str(item["msg"]).removeprefix("Value error, ")
        where = f"{trait}, {label}" if trait and field else trait or label or "The list"
        problems.append(ListProblem("invalid_value", f"{where}: {message}.", trait, field or None))
    return problems


def _trait_at(data: object, path: Sequence[object]) -> str | None:
    """The code of the trait a validation error points into, when it points into one."""
    if len(path) >= 2 and path[0] == "traits" and isinstance(path[1], int):
        traits = data.get("traits") if isinstance(data, dict) else None
        if isinstance(traits, list) and path[1] < len(traits) and isinstance(traits[path[1]], dict):
            code = traits[path[1]].get("code")
            return code if isinstance(code, str) else None
    return None


def load_table(text: str) -> TraitTable:
    """Validate a trait list from JSON text."""
    return _TABLE_ADAPTER.validate_python(json.loads(text))


@cache
def mammal_traits() -> TraitTable:
    """The shipped mammal trait list."""
    text = resources.files("bioparser.traits").joinpath("mammal_traits.json").read_text("utf-8")
    return load_table(text)


def json_schema() -> dict[str, object]:
    """JSON Schema of the trait list file."""
    return _TABLE_ADAPTER.json_schema()


# Helpers for the models above. They are only called when a list is validated or matched.


def _distinct_problems(
    aliases: Mapping[str, str],
    key: Callable[[str], str],
    *,
    owner: str,
    field: str,
    trait: str | None,
) -> list[ListProblem]:
    """A problem for each pair of spellings in one alias table that are alike once normalized."""
    seen: dict[str, str] = {}
    problems: list[ListProblem] = []
    for spelling in aliases:
        normalized = key(spelling)
        if normalized in seen:
            problems.append(
                ListProblem(
                    "duplicate_alias",
                    f"{owner} has the aliases {seen[normalized]!r} and {spelling!r}, "
                    "which are the same spelling. Keep one.",
                    trait,
                    field,
                    spelling,
                )
            )
        else:
            seen[normalized] = spelling
    return problems


def _blank_text(value: str, message: str, *, trait: str | None, field: str) -> ListProblem | None:
    """A problem when a name or a definition is empty or only spaces."""
    if value.strip():
        return None
    return ListProblem("empty_text", message, trait, field)


def _repeated(values: list[str]) -> list[str]:
    """The values that occur more than once, each once, in order."""
    return [value for value in dict.fromkeys(values) if values.count(value) > 1]


# A loaded list is not mutated, so these indexes are built once per list object. They stay off the model,
# because a cache must not change whether two copies of a list are equal. The entry is
# dropped when the list is freed, so a later object cannot reuse the id with the old keys.
_UNIT_INDEXES: dict[tuple[int, int], "_UnitIndex"] = {}
_COUNT_INDEXES: dict[tuple[int, int], "_CountIndex"] = {}
_CODE_INDEXES: dict[int, dict[str, "Trait"]] = {}


def _keep(store: dict[Any, Any], owner: object, key: object, value: object) -> None:
    store[key] = value

    def drop(store: dict[Any, Any] = store, key: object = key, value: object = value) -> None:
        if store.get(key) is value:
            store.pop(key, None)

    weakref.finalize(owner, drop)


class _UnitIndex:
    """Printed units of one numeric trait, keyed once for an exact match and once ignoring case."""

    def __init__(self, aliases: Mapping[str, str], printed_units: Mapping[str, str]) -> None:
        printed_folds = {clean_unit(printed).casefold() for printed in printed_units}
        folded = _foldable(aliases, printed_folds)
        self.exact_aliases = alias_keys(aliases, casefold=False)
        self.folded_aliases = alias_keys(folded, casefold=True)
        self.exact_printed = _printed_index(printed_units, self.exact_aliases, casefold=False)
        self.folded_printed = _printed_index(printed_units, self.folded_aliases, casefold=True)

    def lookup(self, unit: str) -> str | None:
        wanted = apply_unit(unit, self.exact_aliases, casefold=False)
        found = self.exact_printed.get(wanted)
        if found is not None:
            return found
        wanted = apply_unit(unit, self.folded_aliases, casefold=True)
        return self.folded_printed.get(wanted)


class _CountIndex:
    """One count unit, keyed once with its case and once without."""

    def __init__(self, trait_unit: str, aliases: Mapping[str, str]) -> None:
        self.exact_aliases = alias_keys(aliases, casefold=False)
        self.folded_aliases = alias_keys(aliases, casefold=True)
        self.exact = apply_unit(trait_unit, self.exact_aliases, casefold=False)
        self.folded = apply_unit(trait_unit, self.folded_aliases, casefold=True)

    def accepts(self, unit: str) -> bool:
        if apply_unit(unit, self.exact_aliases, casefold=False) == self.exact:
            return True
        return apply_unit(unit, self.folded_aliases, casefold=True) == self.folded


def _printed_index(
    printed_units: Mapping[str, str], table: Mapping[str, str], *, casefold: bool
) -> dict[str, str]:
    """The first printed unit for each normalized key. Later duplicates keep the first."""
    found: dict[str, str] = {}
    for printed in printed_units:
        found.setdefault(apply_unit(printed, table, casefold=casefold), printed)
    return found


def _foldable(aliases: Mapping[str, str], printed: set[str]) -> dict[str, str]:
    """The aliases that are safe to use when case is ignored.

    An alias is left out when its case-folded spelling is a printed unit of the trait, or when
    two aliases fold to one spelling with different targets. ``Mg`` for a tonne must not turn
    ``MG`` into a tonne, or turn a printed ``mg`` into one.
    """
    groups: dict[str, dict[str, str]] = {}
    for spelling, unit in aliases.items():
        groups.setdefault(clean_unit(spelling).casefold(), {})[spelling] = unit
    usable: dict[str, str] = {}
    for folded, members in groups.items():
        if folded in printed or len({clean_unit(unit).casefold() for unit in members.values()}) > 1:
            continue
        usable.update(members)
    return usable
