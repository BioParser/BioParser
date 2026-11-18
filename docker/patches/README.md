# Image patches

Scripts here rewrite installed packages in the same Docker layer as the install. BioParser does not import them. A script exits if the rewritten text is gone. Version pins stay on the `uv pip install` line.

| Patch | Reason | Upstream status | Check when updating the package |
| --- | --- | --- | --- |
| MinerU, in [mineru_glyph_edges.py](mineru_glyph_edges.py) | Copy each character box before it is deleted. A span with no character list adds no glyphs. | Not upstream. MinerU 4.2.3. | `tests/parser/fixtures/glyph-patch` is the published file. The patch test fails when the text is gone. The network test matches it to the wheel for `MINERU_VERSION`. |
| DocVortex, in [mineru_glyph_edges.py](mineru_glyph_edges.py) | Keep those edges after each block has its public index. Figure grouping then drops the caption lines. | Not upstream. DocVortex 0.6.24. | Same fixture and tests, against the wheel for `DOCVORTEX_VERSION`. |
