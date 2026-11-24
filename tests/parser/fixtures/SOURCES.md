# PDF fixtures

## plos-biology-3000248.pdf

- Bloom T, Ganley E, Winker M (2019) Broadening the scope of PLOS Biology.
PLOS Biology 17(4): e3000248.
- DOI: [https://doi.org/10.1371/journal.pbio.3000248](https://doi.org/10.1371/journal.pbio.3000248)
- Download: publisher PDF (`type=printable`) from PLOS Biology
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- SHA-256: `9be1975c0725a1c21a402799fa3326764bd1ef6d3a16a3a1642c4fd6ab565a1d`

The PDF remains CC BY 4.0. It is not relicensed with BioParser source.

## plos-biology-1002000.pdf

- Lewitus E, Kelava I, Kalinka AT, Tomancak P, Huttner WB (2014) An Adaptive
Threshold in Mammalian Neocortical Evolution. PLOS Biology 12(11): e1002000.
- DOI: [https://doi.org/10.1371/journal.pbio.1002000](https://doi.org/10.1371/journal.pbio.1002000)
- Download: publisher PDF (`type=printable`) from PLOS Biology
- License: [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- SHA-256: `66477e69fc5eba90fd056d184e5d69cd1c914e498d2adb576af93fbc1d577b42`

Research article (not an editorial): two-column body, mammal trait/neurogenesis tables (Table 1 and Table 2). Use it to test column grouping and table extraction.

The PDF remains CC BY 4.0. It is not relicensed with BioParser source.

## pgen-1000076.pdf

- Barchi M, Roig I, Di Giacomo M, de Rooij DG, Keeney S, Jasin M (2008) ATM
Promotes the Obligate XY Crossover and both Crossover Control and Chromosome
Axis Integrity on Autosomes. PLoS Genetics 4(5): e1000076.
- DOI: [https://doi.org/10.1371/journal.pgen.1000076](https://doi.org/10.1371/journal.pgen.1000076)
- Download: publisher PDF from PLOS Genetics
- License: Creative Commons Attribution License
- SHA-256: `9d14a6be7c444d9804f4538b1eda783a92e092a040aa7c3078a95d430baed5aa`

The PDF remains under that license. It is not relicensed with BioParser source.

## pone-0090887.pdf

- Yeste M, Estrada E, Rivera del Álamo MM, Bonet S, Rigau T, Rodríguez-Gil JE
(2014) The Increase in Phosphorylation Levels of Serine Residues of Protein
HSP70 during Holding Time at 17°C Is Concomitant with a Higher Cryotolerance
of Boar Spermatozoa. PLOS ONE 9(3): e90887.
- DOI: [https://doi.org/10.1371/journal.pone.0090887](https://doi.org/10.1371/journal.pone.0090887)
- Download: publisher PDF from PLOS ONE
- License: Creative Commons Attribution License
- SHA-256: `ff1b2b748c60c60879b0194a746f9b0c7e14007e4b78825df0f63c3c30665e5d`

The PDF remains under that license. It is not relicensed with BioParser source.

## mineru-middle.json

MinerU 4.2.3 `middle_json.json` for `plos-biology-3000248.pdf`, from the result ZIP of a `POST /v1/tasks` task with `output_formats=["default"]`, written one block per line. It was produced with `tier=flash`. The protocol (`docvortex.middle` 2.0) is the same for every tier; the mix of blocks and spans is not. Regenerate it whenever `MINERU_VERSION` changes.

## mineru-middle-1002000.json

The same, for `plos-biology-1002000.pdf` (`tier=flash`). It has what the editorial lacks: paragraphs continued across columns and pages (`continues_prev`), a table body in HTML, inline equations, and superscript and subscript spans.
