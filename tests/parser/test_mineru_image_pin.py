import re
from pathlib import Path

from bioparser.parser.backend.mineru.schema import MINERU_PIPELINE_VERSION

_DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "parser-worker.Dockerfile"
_PIN = re.compile(r"mineru\[pipeline\]==([0-9]+\.[0-9]+\.[0-9]+)")


def test_worker_image_pins_the_schema_pipeline_version() -> None:
    pins = set(_PIN.findall(_DOCKERFILE.read_text(encoding="utf-8")))
    assert pins == {MINERU_PIPELINE_VERSION}
