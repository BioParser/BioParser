# Image patches

Scripts here rewrite installed Python packages in the same Docker layer as the install. BioParser does not import them. Version pins stay on the `uv pip install` line.

```
lib/hooks.py                 generic: Hook, Feature, apply_feature. Knows no package.
<image>/apply_patches.py     the image's entry point. Lists its FEATURES.
<image>/versions.py          the releases that image's patches are written against.
<image>/<feature>/hooks.py   the feature's hooks and its Feature record.
<image>/<feature>/<module>.py  the runtime, installed into site-packages as a top level module.
```

A hook swaps one exact piece of installed source for a few lines that call the feature's runtime. The runtime holds the logic: plain typed Python with the standard library only, so tests import it. Applying a feature twice changes nothing. A hook whose source has moved stops the script, so a new release never gets half a patch.

To add a feature, create `<image>/<feature>/` with a `hooks.py` that defines `FEATURE`, then add it to `FEATURES` in the image's `apply_patches.py`. The Dockerfile copies `lib` and the image directory, and runs `apply_patches.py` with the site-packages path.

## mineru image

| Feature | Purpose | Upstream status | Check when updating a package |
| --- | --- | --- | --- |
| [glyph_edges](mineru/glyph_edges/hooks.py) | MinerU and DocVortex measure each character box and then throw it away. The patch keeps the boxes so search highlights can follow the text. The runtime's docstring gives the data flow and the `bioparser_lines` wire format. | Not upstream. MinerU 4.2.3, DocVortex 0.6.24. | The network test downloads the wheels for `MINERU_VERSION` and `DOCVORTEX_VERSION`. Every hook must find its source. |

Tests are under `tests/patches/`, mirroring this layout. The backend side, which places the glyphs on the stored text, is [glyphs.py](../../src/bioparser/parser/backend/mineru/glyphs.py).
