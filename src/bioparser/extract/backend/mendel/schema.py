from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ModelObservation(BaseModel):
    """One measurement as the model reports it, in the paper's own words.

    The length caps are defence in depth behind max_tokens and are deliberately
    generous: exceeding one fails that measurement, not the whole page.

    ``value`` is the measured number. A range with no single number uses
    ``minimum`` or ``maximum`` instead. ``dispersion`` and ``statistical_method``
    are the spread and the phrase around the value. They are not the unit.
    The printed scientific name may be empty when the paper does not name a subject.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    verbatim_scientific_name: str = Field(default="", max_length=120)
    trait_name: str = Field(min_length=1, max_length=120)
    value: str = Field(default="", max_length=40)
    unit: str = Field(default="", max_length=40)
    dispersion: str = Field(default="", max_length=40)
    statistical_method: str = Field(default="", max_length=120)
    minimum: str = Field(default="", max_length=40)
    maximum: str = Field(default="", max_length=40)

    @model_validator(mode="after")
    def _has_a_number(self) -> "ModelObservation":
        """A measurement is a printed value, or a printed minimum or maximum."""
        if self.value or self.minimum or self.maximum:
            return self
        raise ValueError("a measurement needs a value or a bound")


_KEYS = {
    "verbatimscientificname": "verbatim_scientific_name",
    "verbatimtraitname": "trait_name",
    "verbatimtraitvalue": "value",
    "verbatimtraitunit": "unit",
    "dispersion": "dispersion",
    "method": "statistical_method",
    "statistical_method": "statistical_method",
    "min": "minimum",
    "minimum": "minimum",
    "max": "maximum",
    "maximum": "maximum",
}


def parse_measurements(text: str) -> list[ModelObservation]:
    """Read the line format Mendel asks the model to write.

    A measurement names the trait and a value or a bound. The printed scientific
    name may be absent. A blank line separates measurements. Unit, dispersion,
    and method may be absent. A record that is missing a required field, or that
    is too long, is skipped.
    """
    found: list[ModelObservation] = []
    for chunk in text.split("\n\n"):
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            key, separator, raw = line.partition(":")
            if separator != ":":
                continue
            mapped = _KEYS.get(key.strip().lower())
            if mapped is not None:
                fields[mapped] = raw.strip()
        if "trait_name" not in fields:
            continue
        if "value" not in fields and "minimum" not in fields and "maximum" not in fields:
            continue
        try:
            found.append(ModelObservation.model_validate(fields))
        except ValidationError:
            continue
    return found
