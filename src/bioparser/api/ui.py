from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse

from . import config

# index.html names the current asset files, so the browser checks it on every load.
_INDEX_CACHE = "no-cache"
# Vite puts a content hash in every file name under assets/, so a name never gets new content.
_ASSET_CACHE = "public, max-age=31536000, immutable"
# Sent with every UI file. The build has no inline scripts or styles and loads nothing
# from other origins, so the policy allows only files and API calls from this server.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; object-src 'none'; base-uri 'none'; "
        "form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def add_ui_routes(app: FastAPI) -> None:
    """Add the UI routes to the app itself. include_router would add them behind one
    wrapper entry, and the auth tests check each route in app.routes."""
    # Not API endpoints, so not in the OpenAPI schema or on /docs.
    app.add_api_route("/ui", ui_root, methods=["GET"], include_in_schema=False)
    app.add_api_route("/ui/{path:path}", ui, methods=["GET"], include_in_schema=False)


def ui_root() -> RedirectResponse:
    return RedirectResponse("/ui/")


def ui(path: str) -> FileResponse:
    """A file of the built UI, or index.html for any other path so a deep link loads."""
    dist_path = config.get_api_settings().ui_dist_path
    if dist_path is None:
        raise HTTPException(status_code=404)
    dist = Path(dist_path).resolve()
    index = dist / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=404)

    if "\0" in path:
        # %00: no file can have that name. Checked here because resolve() raises
        # ValueError for it on Linux but not on Windows.
        raise HTTPException(status_code=404)

    # resolve() removes ".." and follows symlinks, so the check below keeps every
    # request inside dist.
    candidate = (dist / path).resolve()
    if candidate.is_relative_to(dist) and candidate.is_file():
        cache = _ASSET_CACHE if path.startswith("assets/") else _INDEX_CACHE
        return FileResponse(candidate, headers={**_SECURITY_HEADERS, "Cache-Control": cache})
    if path.startswith("assets/"):
        # A missing script or stylesheet must fail, not come back as HTML.
        raise HTTPException(status_code=404)
    return FileResponse(index, headers={**_SECURITY_HEADERS, "Cache-Control": _INDEX_CACHE})
