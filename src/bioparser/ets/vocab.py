"""Match text to an ETS controlled value. A miss is not coerced."""

from typing import Literal

Sex = Literal["male", "female", "subadult", "unknown", "hermaphrodite"]
BasisOfRecord = Literal[
    "LivingSpecimen",
    "PreservedSpecimen",
    "FossilSpecimen",
    "literatureData",
    "traitDatabase",
    "expertKnowledge",
]
CentralKind = Literal["mean", "median"]
DispersionKind = Literal["standardDeviation", "variance", "standardError"]

_SEX: dict[str, Sex] = {
    "male": "male",
    "female": "female",
    "subadult": "subadult",
    "unknown": "unknown",
    "hermaphrodite": "hermaphrodite",
}
_BASIS: dict[str, BasisOfRecord] = {
    "livingspecimen": "LivingSpecimen",
    "preservedspecimen": "PreservedSpecimen",
    "fossilspecimen": "FossilSpecimen",
    "literaturedata": "literatureData",
    "traitdatabase": "traitDatabase",
    "expertknowledge": "expertKnowledge",
}
_CENTRAL: dict[str, CentralKind] = {
    "mean": "mean",
    "median": "median",
}
_DISPERSION_KIND: dict[str, DispersionKind] = {
    "standarddeviation": "standardDeviation",
    "variance": "variance",
    "standarderror": "standardError",
}


def match_sex(text: str) -> Sex | None:
    """ETS sex token. Matching ignores case. Abbreviations do not match."""
    return _SEX.get(text.casefold())


def match_basis(text: str) -> BasisOfRecord | None:
    """One of the six ETS basisOfRecord tokens. Matching ignores case."""
    return _BASIS.get(text.casefold())


def match_aggregate(text: str) -> bool | None:
    """TRUE or FALSE, ignoring case. Any other text does not match."""
    folded = text.casefold()
    if folded == "true":
        return True
    if folded == "false":
        return False
    return None


def match_central(text: str) -> CentralKind | None:
    """mean or median. Matching ignores case."""
    return _CENTRAL.get(text.casefold())


def match_dispersion_kind(text: str) -> DispersionKind | None:
    """How a dispersion number was summarized. Matching ignores case."""
    return _DISPERSION_KIND.get(text.casefold())
