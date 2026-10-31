import json

import pytest
from fastapi.testclient import TestClient

from bioparser.api.app import trait_list
from bioparser.traits.table import load_table, mammal_traits


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


def test_full_detail_keeps_a_replacement_outside_the_selection(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.loads(mammal_traits().model_dump_json())
    retired = raw["traits"][0]
    replacement = next(trait for trait in raw["traits"] if trait["domain"] != retired["domain"])
    retired["status"] = "deprecated"
    retired["replaced_by"] = replacement["code"]
    patched = load_table(json.dumps(raw))
    # The package binds `app` over the module, so patch the route's globals.
    monkeypatch.setitem(trait_list.__globals__, "mammal_traits", lambda: patched)

    response = client.get(
        "/api/traits",
        params={
            "detail": "full",
            "domain": retired["domain"],
            "include_deprecated": "true",
        },
    )

    assert response.status_code == 200
    codes = {trait["code"] for trait in response.json()["traits"]}
    kept = next(trait for trait in response.json()["traits"] if trait["code"] == retired["code"])
    assert kept["replaced_by"] == replacement["code"]
    assert replacement["code"] not in codes


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
        "/api/traits", params={"domain": "acoustics", "code": ["body_mass", "tail_length"]}
    )

    assert response.status_code == 200
    names = {row["trait"] for row in response.json()["traits"]}
    assert {"Body mass", "Tail length", "Call peak frequency"} <= names
    assert "Litter size" not in names
