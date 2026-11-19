# Image patches

Scripts here rewrite installed packages in the same Docker layer as the install. BioParser does not import them. A script exits if the rewritten text is gone. Version pins stay on the `uv pip install` line.

| Patch | Reason | Upstream status | Check when updating the package |
| --- | --- | --- | --- |
| MinerU, in [mineru_glyph_edges.py](mineru_glyph_edges.py) | Copy each character box before it is deleted. A span with no character list adds no glyphs. | Not upstream. MinerU 4.2.3. | The network test downloads the wheel for `MINERU_VERSION`. The patch must rewrite those files, and it fails when the text is gone. |
| DocVortex, in [mineru_glyph_edges.py](mineru_glyph_edges.py) | Keep those edges after each block has its public index. Figure grouping then drops the caption lines. | Not upstream. DocVortex 0.6.24. | Same test, against the wheel for `DOCVORTEX_VERSION`. |
