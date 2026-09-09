# Local PDF parser

The parser is a local library at the moment. `pdf-parse` and `pdf-view` are developer scripts that call it.

## Default backend

```sh
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf -o artifact.json
uv run pdf-view tests/parser/fixtures/plos-biology-1002000.pdf
```

`default` uses pdfplumber for digital-born text. It is fast and deterministic; the default light option that works anywhere. Schema and grouping details live in `src/bioparser/parser/`.

## MinerU CPU backend

MinerU is an optional local backend for comparing parser output. It is slower and more resource-intensive than the default backend, but can produce richer and more accurate layout information. It also parses reading-order accurately.

Install the MinerU CLI as an isolated UV tool:

```sh
uv tool install --python 3.13 'mineru[pipeline]==3.4.5' --with six
# If `mineru` is not found afterwards: uv tool update-shell
uv run pdf-parse tests/parser/fixtures/plos-biology-3000248.pdf --backend mineru -o artifact.json
uv run pdf-view tests/parser/fixtures/plos-biology-1002000.pdf --backend mineru
```

The version pin must match the pipeline `middle.json` schema in `src/bioparser/parser/backend/mineru/schema.py`. Do not bump the CLI without updating that schema and the `para_blocks` excerpt in `tests/parser/fixtures/mineru-middle.json`.

`uv tool` keeps MinerU's dependencies separate from the project's environment. If `mineru` is not found after installation, run `uv tool update-shell` and start a new shell.

Remove the tool with:

```sh
uv tool uninstall mineru
```

The first run downloads models. CPU parsing may need roughly 16 GB of RAM and take tens of seconds to minutes per paper.

Use the dev viewer's backend selector to compare results. Uploading a new PDF clears the previous results. **Re-parse** refreshes the selected backend.
