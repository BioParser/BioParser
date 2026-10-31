from fastapi.testclient import TestClient


def test_the_default_response_is_an_ets_traitlist(client: TestClient) -> None:
    response = client.get("/api/traits")

    assert response.status_code == 200
    body = response.json()
    mass = next(row for row in body["traits"] if row["trait"] == "Body mass")
    assert mass["valueType"] == "numeric"
    assert mass["identifier"].startswith("http://purl.obolibrary.org/obo/VT_")


def test_full_detail_includes_the_domains(client: TestClient) -> None:
    response = client.get("/api/traits", params={"detail": "full", "domain": "acoustics"})

    assert response.status_code == 200
    body = response.json()
    assert any(domain["name"] == "morphology" for domain in body["domains"])
    assert {trait["code"] for trait in body["traits"]} <= {
        trait["code"] for trait in body["traits"] if trait["domain"] == "acoustics"
    }
    assert body["traits"]
    assert all(trait["domain"] == "acoustics" for trait in body["traits"])


def test_an_unknown_domain_is_rejected(client: TestClient) -> None:
    response = client.get("/api/traits", params={"domain": "diet"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_trait_query"
