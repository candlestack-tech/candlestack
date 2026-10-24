"""The preprocessing catalog route, which ml serves for the manifests of its models."""

from fastapi.testclient import TestClient

from candlestack.preprocessing import describe_catalog


def test_the_catalog_lists_every_block_without_signing_in(client: TestClient) -> None:
    response = client.get("/api/v1/preprocessing/catalog")

    assert response.status_code == 200
    assert response.json() == describe_catalog().model_dump(mode="json")
    blocks = {block["id"]: block for block in response.json()["blocks"]}
    assert blocks["candles@1"]["step"] == "representation"
    assert blocks["window@1"]["params"][0]["name"] == "length"


def test_the_catalog_is_in_the_api_reference(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()

    route = schema["paths"]["/api/v1/preprocessing/catalog"]["get"]
    assert route["tags"] == ["preprocessing"]
    assert route["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CatalogOut"
    }
