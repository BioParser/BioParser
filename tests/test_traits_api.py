from fastapi.testclient import TestClient


def test_the_default_response_is_an_ets_traitlist(client: TestClient) -> None:
    response = client.get("/api/traits")

    assert response.status_code == 200
    body = response.json()
    mass = next(row for row in body["traits"] if row["trait"] == "Body mass")
    assert mass["valueType"] == "numeric"
    assert mass["identifier"].startswith("http://purl.obolibrary.org/obo/VT_")
    assert body["version"] == "2026.11.0"


def test_full_detail_includes_the_selected_domain(client: TestClient) -> None:
    response = client.get("/api/traits", params={"detail": "full", "domain": "skeleton"})

    assert response.status_code == 200
    body = response.json()
    assert any(domain["name"] == "morphology" for domain in body["domains"])
    assert body["traits"]
    assert all(trait["domain"] == "skeleton" for trait in body["traits"])
    assert body["id_prefixes"]["BPT"] == "https://bioparser.github.io/BioParser/traits/BPT/"
    assert body["unit_aliases"]["gram"] == "g"
    assert body["level_aliases"]["y"] == "yes"


def test_an_unknown_domain_is_rejected(client: TestClient) -> None:
    response = client.get("/api/traits", params={"domain": "diet"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unknown_trait_query"


def test_a_named_code_is_returned_and_an_unknown_code_is_rejected(client: TestClient) -> None:
    response = client.get("/api/traits", params={"code": "body_mass"})

    assert response.status_code == 200
    assert [row["trait"] for row in response.json()["traits"]] == ["Body mass"]

    missing = client.get("/api/traits", params={"code": "wing_length"})

    assert missing.status_code == 400
    assert missing.json()["detail"]["code"] == "unknown_trait_query"


def test_a_code_adds_to_a_domain_filter(client: TestClient) -> None:
    response = client.get(
        "/api/traits", params={"domain": "skeleton", "code": ["body_mass", "tail_length"]}
    )

    assert response.status_code == 200
    names = {row["trait"] for row in response.json()["traits"]}
    assert {"Body mass", "Tail length", "Greatest skull length"} <= names
    assert "Litter size" not in names
