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

## mineru-middle.json

MinerU 4.0.12 `middle_json` for `plos-biology-3000248.pdf`, from `POST /v1/file_parse` with `output_formats=middle_json`, written one block per line. It was produced with `tier=flash`. The protocol (`docvortex.middle` 2.0) is the same for every tier; the mix of blocks and spans is not. Regenerate it whenever `MINERU_VERSION` changes.

## mineru-middle-1002000.json

The same, for `plos-biology-1002000.pdf` (`tier=flash`). It has what the editorial lacks: paragraphs continued across columns and pages (`continues_prev`), a table body in HTML, inline equations, and superscript and subscript spans.
