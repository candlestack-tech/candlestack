"""What the API reference says about API keys on each route (``accounts.reference``)."""

from typing import Any

import pytest

from candlestack.accounts import document_api_keys
from candlestack.core import Settings
from candlestack.main import create_app


@pytest.fixture(scope="module")
def paths() -> dict[str, Any]:
    return create_app(Settings(app_env="test")).openapi()["paths"]


@pytest.mark.parametrize(
    ("method", "path", "security"),
    [
        ("get", "/api/v1/me", [{"apiKey": ["read"]}]),
        ("get", "/api/v1/data/instruments", [{"apiKey": ["read"]}]),
        ("post", "/api/v1/backtests", [{"apiKey": ["backtests"]}]),
        (
            "patch",
            "/api/v1/backtests/{backtest_id}",
            [{"apiKey": ["backtests"]}, {"apiKey": ["publish"]}],
        ),
        ("post", "/api/v1/models", [{"apiKey": ["models"]}]),
        ("put", "/api/v1/models/{model_id}/manifest", [{"apiKey": ["models"]}]),
        ("delete", "/api/v1/models/{model_id}", [{"apiKey": ["models"]}]),
        ("patch", "/api/v1/me", None),
        ("delete", "/api/v1/me", None),
        ("get", "/api/v1/me/api-keys", None),
        ("post", "/api/v1/me/api-keys", None),
        ("get", "/api/v1/preprocessing/catalog", None),
    ],
)
def test_the_scope_each_route_needs(
    paths: dict[str, Any], method: str, path: str, security: list[dict[str, list[str]]] | None
) -> None:
    assert paths[path][method].get("security") == security


def test_a_route_a_key_may_call_documents_its_problems(paths: dict[str, Any]) -> None:
    responses = paths["/api/v1/models"]["post"]["responses"]

    assert "`not-signed-in`" in responses["401"]["description"]
    assert "`invalid-api-key`" in responses["401"]["description"]
    assert "`cross-origin-request`" in responses["403"]["description"]
    assert "`missing-scope`" in responses["403"]["description"]
    assert "`rate-limited`" in responses["429"]["description"]
    assert responses["429"]["content"]["application/problem+json"]


@pytest.mark.parametrize(
    ("method", "path"),
    [("patch", "/api/v1/me"), ("get", "/api/v1/me/api-keys"), ("delete", "/api/v1/me")],
)
def test_a_route_of_the_session_only_says_so(paths: dict[str, Any], method: str, path: str) -> None:
    responses = paths[path][method]["responses"]

    assert "`session-required`" in responses["403"]["description"]
    assert "429" not in responses


def test_a_public_route_says_nothing_of_keys(paths: dict[str, Any]) -> None:
    assert set(paths["/api/v1/preprocessing/catalog"]["get"]["responses"]) == {"200"}


def test_a_scheme_other_than_the_key_stays() -> None:
    schema = {"paths": {"/x": {"post": {"security": [{"other": []}], "responses": {}}}}}

    document_api_keys(schema)

    assert schema["paths"]["/x"]["post"]["security"] == [{"other": []}]
