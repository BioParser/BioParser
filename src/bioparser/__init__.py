import uvicorn

from bioparser.app import app


def main() -> None:
    uvicorn.run(app, host="0.0.0.0", port=8000)
