import asyncio
from typing import cast

from bioparser.extract.backend.mendel.disambiguate import (
    _choice_number,
    ambiguous_fields,
    chosen_choices,
    quote_options,
)
from bioparser.extract.backend.mendel.extractor import (
    DISAMBIGUATION_PROMPT,
    extract_observations,
)
from bioparser.extract.backend.mendel.locate import locate_in_page
from bioparser.extract.backend.mendel.schema import ModelObservation
from bioparser.extract.citations import reject_mismatched_ranges
from bioparser.extract.models import Citation
from bioparser.parser.models import (
    PARSER_ARTIFACT_SCHEMA_VERSION,
    BlockKind,
    BlockRole,
    ContentBlock,
    Page,
    ParserArtifact,
    ParserInfo,
    TextContent,
)
from bioparser.services.vllm import VLLMService

SENTENCE = "Mirostrellus. Third finger (64.10 mm) 22.89 mm longer than fifth finger (41.21 mm)."


def _observation(**changes: str) -> ModelObservation:
    fields = {
        "verbatim_scientific_name": "Mirostrellus",
        "trait_name": "Third finger",
        "value": "64.10",
        "unit": "mm",
    }
    fields.update(changes)
    return ModelObservation.model_validate(fields)


def test_a_bound_inside_a_longer_word_is_cited() -> None:
    text = "A vespertilionid with a FA of 35.7–40.2 mm."
    blocks = [("b0", text)]

    [located] = locate_in_page(
        [
            ModelObservation(
                verbatim_scientific_name="vespertilionid",
                trait_name="FA",
                minimum="35.7",
                maximum="40.2",
                unit="mm",
            )
        ],
        blocks,
    )

    minimum_field = located.measurementValueMin
    assert isinstance(minimum_field, Citation)
    assert minimum_field.text == "35.7"
    minimum = minimum_field.evidence[0]
    assert text[minimum.start : minimum.end] == "35.7"
    assert text[minimum.start : minimum.end + 5] == "35.7–40.2"
    maximum_field = located.measurementValueMax
    assert isinstance(maximum_field, Citation)
    assert maximum_field.text == "40.2"
    reject_mismatched_ranges([located], _paper(text))


def test_a_quote_contains_the_string_once_inside_a_longer_word() -> None:
    text = "The difference was 22.89 mm, and the tip protrudes 2 mm."
    options = quote_options([("b0", text)], "2")

    assert options
    for option in options:
        assert option.quote.count("2") == 1
    assert any("protrudes" in option.quote for option in options)


def test_each_quote_contains_the_reported_string_once() -> None:
    options = quote_options([("b0", SENTENCE)], "mm")

    assert len(options) >= 2
    for option in options:
        assert option.quote.count("mm") == 1


def test_a_copied_quote_stores_that_occurrence() -> None:
    blocks = [("b0", SENTENCE)]
    chosen = next(option for option in quote_options(blocks, "mm") if "64.10" in option.quote)
    paper = _paper(SENTENCE)

    [located] = locate_in_page([_observation()], blocks, {(0, "unit"): chosen})

    assert located.verbatimTraitUnit is not None
    assert located.verbatimTraitUnit.text == "mm"
    assert SENTENCE[chosen.start : chosen.end] == "mm"
    assert "64.10" in SENTENCE[max(0, chosen.start - 8) : chosen.start]
    reject_mismatched_ranges([located], paper)


def test_a_repeated_unit_without_a_quote_is_left_empty() -> None:
    [located] = locate_in_page([_observation()], [("b0", SENTENCE)])

    assert located.verbatimTraitValue is not None
    assert located.verbatimTraitValue.text == "64.10"
    assert located.verbatimTraitUnit is None


def test_a_choice_number_selects_the_occurrence() -> None:
    blocks = [("b0", SENTENCE)]
    observation = _observation()
    [field] = [item for item in ambiguous_fields([observation], blocks) if item.field == "unit"]
    chosen = next(option for option in field.options if "64.10" in option.quote)
    number = field.options.index(chosen) + 1

    assert chosen_choices(str(number), [field]) == {(0, "unit"): chosen}


def test_a_superscript_digit_is_not_a_choice() -> None:
    assert _choice_number("1²", 3) is None


class _Answers:
    def __init__(self, first: str, replies: list[str]) -> None:
        self.first = first
        self.replies = replies
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []

    async def fit_prefix(
        self,
        parts: list[str],
        *,
        system_prompt: str | None = None,
        separator: str = "\n\n",
        max_tokens: int | None = None,
        enable_thinking: bool = False,
    ) -> int:
        del system_prompt, separator, max_tokens, enable_thinking
        return len(parts)

    async def generate(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        enable_thinking: bool = False,
        timeout: float | None = None,
    ) -> str:
        del temperature, max_tokens, enable_thinking, timeout
        self.prompts.append(prompt)
        self.system_prompts.append(system_prompt or "")
        if len(self.prompts) == 1:
            return self.first
        return self.replies[len(self.prompts) - 2]


def test_each_row_is_its_own_second_prompt() -> None:
    text = "Mus musculus body mass was 12.5 mm and the tail was 9 mm."
    options = quote_options([("b0", text)], "mm")
    mass = next(number for number, option in enumerate(options, start=1) if "12.5" in option.quote)
    tail = next(number for number, option in enumerate(options, start=1) if " 9 mm" in option.quote)
    first = (
        "verbatimScientificName: Mus musculus\n"
        "verbatimTraitName: body mass\n"
        "verbatimTraitValue: 12.5\n"
        "verbatimTraitUnit: mm\n"
        "\n"
        "verbatimScientificName: Mus musculus\n"
        "verbatimTraitName: tail\n"
        "verbatimTraitValue: 9\n"
        "verbatimTraitUnit: mm\n"
    )
    model = _Answers(first, [str(mass), str(tail)])

    result = asyncio.run(extract_observations(cast(VLLMService, model), _paper(text)))

    assert len(model.prompts) == 3
    assert model.prompts[1].startswith(f"Passage:\n{text}\n")
    assert model.prompts[2].startswith(f"Passage:\n{text}\n")
    assert "body mass" in model.prompts[1]
    assert "tail" in model.prompts[2]
    assert model.system_prompts[1] == DISAMBIGUATION_PROMPT
    assert model.system_prompts[2] == DISAMBIGUATION_PROMPT
    units = [observation.verbatimTraitUnit for observation in result.observations]
    assert [unit.text if unit is not None else None for unit in units] == ["mm", "mm"]
    starts = [unit.evidence[0].start for unit in units if unit is not None]
    assert "12.5" in text[max(0, starts[0] - 6) : starts[0]]
    assert "9" in text[max(0, starts[1] - 4) : starts[1]]


def _paper(text: str) -> ParserArtifact:
    return ParserArtifact(
        schema_version=PARSER_ARTIFACT_SCHEMA_VERSION,
        checksum="a" * 64,
        parser=ParserInfo(name="default", version="1", configuration={}),
        pages=[
            Page(
                page_number=1,
                width=1.0,
                height=1.0,
                blocks=[
                    ContentBlock(
                        block_id="b0",
                        order=0,
                        kind=BlockKind.TEXT,
                        role=BlockRole.BODY,
                        content=TextContent(text=text),
                    )
                ],
            )
        ],
    )
