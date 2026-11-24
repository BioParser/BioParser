from bioparser.extract.backend.mendel.schema import ModelObservation, parse_measurements


def test_a_record_without_a_trait_is_skipped() -> None:
    parsed = parse_measurements("verbatimScientificName: Mus musculus\nverbatimTraitValue: 12.5\n")

    assert parsed == []


def test_a_record_without_a_printed_name_is_kept() -> None:
    [parsed] = parse_measurements("verbatimTraitName: body mass\nverbatimTraitValue: 12.5\n")

    assert parsed == ModelObservation(trait_name="body mass", value="12.5")


def test_a_repeated_key_starts_a_new_record() -> None:
    parsed = parse_measurements(
        "verbatimScientificName: Mus musculus\n"
        "verbatimTraitName: body mass\n"
        "verbatimTraitValue: 12.5\n"
        "verbatimTraitName: body length\n"
        "verbatimTraitValue: 9\n"
    )

    assert parsed == [
        ModelObservation(
            verbatim_scientific_name="Mus musculus", trait_name="body mass", value="12.5"
        ),
        ModelObservation(trait_name="body length", value="9"),
    ]
