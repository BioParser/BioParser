from fastapi.testclient import TestClient

from bioparser.api.schema import HealthResponse


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    HealthResponse.model_validate(response.json())
    assert response.json() == {"status": "ok"}
