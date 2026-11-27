"""A second model call for a printed string that occurs more than once.

The first call reports the printed text. A citation is that string wherever it
sits, including inside a longer word. Each ambiguous row is asked again from
the same passage. The model replies with the number of the occurrence.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from bioparser.extract.backend.mendel.schema import ModelObservation

# Words of context on each side of the occurrence. The window stops before a
# second copy of the reported string, so the quote stays a single choice.
_CONTEXT = 4

# block id, the text shown to the model, and where that text starts in the parser block.
Source = tuple[str, str] | tuple[str, str, int]

_FIELDS = (
    ("verbatim_scientific_name", "verbatimScientificName"),
    ("trait_name", "verbatimTraitName"),
    ("value", "verbatimTraitValue"),
    ("unit", "verbatimTraitUnit"),
    ("dispersion", "dispersion"),
    ("statistical_method", "method"),
    ("minimum", "min"),
    ("maximum", "max"),
)


@dataclass(frozen=True, slots=True)
class Occurrence:
    """One place the reported string occurs. The span need not be a whole word."""

    block_id: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class QuoteOption:
    """One occurrence, shown to the model as a quote that contains it once."""

    quote: str
    block_id: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class AmbiguousField:
    """A reported field whose text occurs more than once."""

    observation_index: int
    field: str
    label: str
    trait_name: str
    value: str
    reported: str
    options: tuple[QuoteOption, ...]


def find_occurrences(blocks: Sequence[Source], needle: str) -> list[Occurrence]:
    """Each occurrence of ``needle``. A hit inside a longer word still counts.

    A block may carry an origin, the start of its text in the parser block. The
    returned spans are positions in that parser block.
    """
    found: list[Occurrence] = []
    for block_id, text, origin in _sources(blocks):
        for start, end in _spans(text, needle):
            found.append(Occurrence(block_id, origin + start, origin + end))
    return found


def ambiguous_fields(
    observations: list[ModelObservation],
    blocks: Sequence[Source],
) -> list[AmbiguousField]:
    """Fields a second call can choose between.

    A field is included when the reported text occurs more than once and at
    least two occurrences have a quote that no other occurrence shares.
    """
    pending: list[AmbiguousField] = []
    for index, observation in enumerate(observations):
        value = observation.value or observation.minimum or observation.maximum
        reported = {
            "verbatim_scientific_name": observation.verbatim_scientific_name,
            "trait_name": observation.trait_name,
            "value": observation.value,
            "unit": observation.unit,
            "dispersion": observation.dispersion,
            "statistical_method": observation.statistical_method,
            "minimum": observation.minimum,
            "maximum": observation.maximum,
        }
        for field, label in _FIELDS:
            text = reported[field]
            if not text or len(find_occurrences(blocks, text)) < 2:
                continue
            options = quote_options(blocks, text)
            if len(options) < 2:
                continue
            pending.append(
                AmbiguousField(
                    observation_index=index,
                    field=field,
                    label=label,
                    trait_name=observation.trait_name,
                    value=value,
                    reported=text,
                    options=tuple(options),
                )
            )
    return pending


def quote_options(blocks: Sequence[Source], needle: str) -> list[QuoteOption]:
    """One quote per occurrence, each containing ``needle`` once.

    Two occurrences that would be shown with the same quote are left out.
    Copying that quote would not pick one of them.
    """
    built: list[QuoteOption] = []
    for block_id, text, origin in _sources(blocks):
        for start, end in _spans(text, needle):
            quote = _quote(text, start, end, needle)
            if quote is None:
                continue
            built.append(QuoteOption(quote, block_id, origin + start, origin + end))
    counts = Counter(option.quote for option in built)
    return [option for option in built if counts[option.quote] == 1]


def disambiguation_prompt(
    passage: str,
    observation: ModelObservation,
    fields: list[AmbiguousField],
) -> str:
    """One row, asked from the same passage as the first call.

    The choices are numbered. The model replies with a number, not the quote.
    """
    lines = [
        "Passage:",
        passage,
        "",
        "You reported this measurement:",
        *_reported_lines(observation),
        "",
    ]
    for field in fields:
        lines.append(f"{field.label} occurs more than once.")
        lines.extend(
            f"{number}. {option.quote}" for number, option in enumerate(field.options, start=1)
        )
        lines.append("")
    if len(fields) == 1:
        lines.append("Reply with the number only.")
    else:
        lines.append("Reply with one line per field: the field name, a colon, and the number.")
    return "\n".join(lines).rstrip() + "\n"


def chosen_choices(
    reply: str,
    fields: list[AmbiguousField],
) -> dict[tuple[int, str], QuoteOption]:
    """The number chosen for each field of one row."""
    if len(fields) == 1:
        field = fields[0]
        number = _choice_number(reply, len(field.options))
        if number is None:
            return {}
        return {(field.observation_index, field.field): field.options[number - 1]}
    chosen: dict[tuple[int, str], QuoteOption] = {}
    for line in reply.splitlines():
        label, separator, raw = line.partition(":")
        if separator != ":":
            continue
        match = next((item for item in fields if item.label.lower() == label.strip().lower()), None)
        if match is None:
            continue
        number = _choice_number(raw, len(match.options))
        if number is None:
            continue
        chosen[(match.observation_index, match.field)] = match.options[number - 1]
    return chosen


def _reported_lines(observation: ModelObservation) -> list[str]:
    lines = [
        f"verbatimScientificName: {observation.verbatim_scientific_name}",
        f"verbatimTraitName: {observation.trait_name}",
    ]
    if observation.value:
        lines.append(f"verbatimTraitValue: {observation.value}")
    if observation.minimum:
        lines.append(f"min: {observation.minimum}")
    if observation.maximum:
        lines.append(f"max: {observation.maximum}")
    if observation.unit:
        lines.append(f"verbatimTraitUnit: {observation.unit}")
    if observation.dispersion:
        lines.append(f"dispersion: {observation.dispersion}")
    if observation.statistical_method:
        lines.append(f"method: {observation.statistical_method}")
    return lines


def _choice_number(reply: str, count: int) -> int | None:
    # isdigit() is true for superscripts such as ², and int() then raises. A PDF can
    # contain that character. isdecimal() keeps only digits int() accepts.
    numbers = [int(token.strip(".")) for token in reply.split() if token.strip(".").isdecimal()]
    picked = [number for number in numbers if 1 <= number <= count]
    if len(picked) != 1:
        return None
    return picked[0]


def _sources(blocks: Sequence[Source]) -> list[tuple[str, str, int]]:
    sources: list[tuple[str, str, int]] = []
    for block in blocks:
        if len(block) == 2:
            block_id, text = block
            sources.append((block_id, text, 0))
        else:
            block_id, text, origin = block
            sources.append((block_id, text, origin))
    return sources


def _spans(text: str, needle: str) -> list[tuple[int, int]]:
    if needle == "":
        return []
    found: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return found
        found.append((index, index + len(needle)))
        start = index + 1


def _quote(text: str, start: int, end: int, needle: str) -> str | None:
    lo, hi = start, end
    for _ in range(_CONTEXT):
        earlier = _previous_word_start(text, lo)
        if earlier == lo or _count(text[earlier:hi], needle) != 1:
            break
        lo = earlier
    for _ in range(_CONTEXT):
        later = _next_word_end(text, hi)
        if later == hi or _count(text[lo:later], needle) != 1:
            break
        hi = later
    if _count(text[lo:hi], needle) != 1:
        return None
    return " ".join(text[lo:hi].split())


def _count(text: str, needle: str) -> int:
    return len(_spans(text, needle))


def _previous_word_start(text: str, index: int) -> int:
    if index == 0:
        return 0
    cursor = index - 1
    while cursor > 0 and text[cursor].isspace():
        cursor -= 1
    while cursor > 0 and not text[cursor - 1].isspace():
        cursor -= 1
    return cursor


def _next_word_end(text: str, index: int) -> int:
    cursor = index
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    while cursor < len(text) and not text[cursor].isspace():
        cursor += 1
    return cursor
