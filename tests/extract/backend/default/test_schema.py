import pytest
from pydantic import ValidationError

from bioparser.extract.backend.default.schema import Extraction, ModelObservation


def test_the_model_payload_keeps_its_observation_cap() -> None:
    observation = ModelObservation(
        taxon="Mus musculus",
        trait_name="body mass",
        value="12.5",
        unit="g",
        evidence_block_id="b0",
        quotation="12.5 g",
    )

    with pytest.raises(ValidationError):
        Extraction(observations=[observation for _ in range(9)])
