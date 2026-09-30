import json
from urllib.error import HTTPError
from urllib.request import urlopen

from bioparser.worker.parse.health import LivenessServer


def test_health_returns_ok_and_other_paths_do_not() -> None:
    server = LivenessServer(0)
    server.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.port}/health", timeout=3) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"status": "ok"}
        try:
            urlopen(f"http://127.0.0.1:{server.port}/ready", timeout=3)
        except HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("expected 404")
    finally:
        server.close()
