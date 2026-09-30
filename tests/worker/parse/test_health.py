import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from bioparser.worker.parse.health import LivenessServer


def test_health_returns_ok_and_other_paths_do_not() -> None:
    server = LivenessServer(0)
    server.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.port}/health", timeout=3) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{server.port}/ready", timeout=3)
        assert exc_info.value.code == 404
    finally:
        server.close()
