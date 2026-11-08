"""``/mcp`` over plain HTTP with the API keys faked (conftest.py): what refuses a request before
the SDK sees it, the 401 the MCP authorization spec asks for, and the protected resource
metadata it points to."""

import pytest
from fastapi.testclient import TestClient

from candlestack.accounts import Caller, Scope, User
from candlestack.core import ProblemError
from candlestack.mcp import http

ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")
PROBLEMS = "https://candlestack.tech/problems/"
METADATA = "http://testserver/.well-known/oauth-protected-resource/mcp"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
ACCEPT = {"Accept": "application/json, text/event-stream"}


def test_without_a_key_the_answer_points_to_the_metadata(mcp_client: TestClient, keys) -> None:
    response = mcp_client.post("/mcp", json=INITIALIZE, headers=ACCEPT)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == f'Bearer resource_metadata="{METADATA}"'
    assert response.json()["type"] == PROBLEMS + "not-signed-in"
    assert "Authorization: Bearer" in response.json()["detail"]
    assert keys.tokens == []


def test_a_key_that_does_not_sign_in_is_an_invalid_token(mcp_client: TestClient) -> None:
    response = mcp_client.post(
        "/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": "Bearer cs_nope"}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        f'Bearer error="invalid_token", resource_metadata="{METADATA}"'
    )
    assert response.json()["type"] == PROBLEMS + "invalid-api-key"


def test_a_signed_in_request_reaches_the_server(mcp_client: TestClient, keys) -> None:
    response = mcp_client.post(
        "/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": f"Bearer {keys.read_key}"}
    )

    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "candlestack"
    assert keys.tokens == [keys.read_key]


def test_the_key_s_rate_limit_passes_through(
    mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def limited(_token: str, **_clients: object) -> Caller:
        raise ProblemError(
            429, "rate-limited", "Too many requests", "Retry.", headers={"Retry-After": "7"}
        )

    monkeypatch.setattr(http, "key_caller", limited)

    response = mcp_client.post(
        "/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": "Bearer cs_x"}
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert "www-authenticate" not in response.headers


def test_a_page_of_another_site_is_refused(mcp_client: TestClient, keys) -> None:
    response = mcp_client.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            **ACCEPT,
            "Authorization": f"Bearer {keys.read_key}",
            "Origin": "https://evil.example",
        },
    )

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"
    assert keys.tokens == []


def test_the_app_s_own_origin_may_call(mcp_client: TestClient, keys) -> None:
    response = mcp_client.post(
        "/mcp",
        json=INITIALIZE,
        headers={
            **ACCEPT,
            "Authorization": f"Bearer {keys.read_key}",
            "Origin": "http://testserver",
        },
    )

    assert response.status_code == 200


def test_the_metadata_names_the_server_its_authorization_server_scopes_and_docs(
    mcp_client: TestClient,
) -> None:
    response = mcp_client.get("/.well-known/oauth-protected-resource/mcp")

    assert response.status_code == 200
    assert response.json() == {
        "resource": "http://testserver/mcp",
        "resource_name": "CandleStack",
        "authorization_servers": ["http://testserver/api/v1/oauth"],
        "resource_documentation": "https://candlestack.tech/docs/mcp/",
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["read", "backtests", "models", "publish"],
    }


def test_mcp_is_not_in_the_api_reference(mcp_client: TestClient) -> None:
    paths = mcp_client.get("/api/v1/openapi.json").json()["paths"]

    assert not [path for path in paths if "mcp" in path or "well-known" in path]


def test_an_access_token_signs_in_through_oauth_bound_to_the_server(
    mcp_client: TestClient, keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []

    async def token_caller(token: str, *, resource: str, **_clients: object) -> Caller:
        seen.append((token, resource))
        return Caller(ADA, frozenset({Scope.read}))

    monkeypatch.setattr(http, "token_caller", token_caller)

    response = mcp_client.post(
        "/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": "Bearer csa_token"}
    )

    assert response.status_code == 200
    assert seen == [("csa_token", "http://testserver/mcp")]
    assert keys.tokens == []


def test_an_access_token_that_does_not_sign_in_is_an_invalid_token(
    mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def token_caller(_token: str, **_clients: object) -> Caller:
        raise ProblemError(401, "invalid-access-token", "Invalid access token", "Unknown.")

    monkeypatch.setattr(http, "token_caller", token_caller)

    response = mcp_client.post(
        "/mcp", json=INITIALIZE, headers={**ACCEPT, "Authorization": "Bearer csa_nope"}
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == (
        f'Bearer error="invalid_token", resource_metadata="{METADATA}"'
    )
    assert response.json()["type"] == PROBLEMS + "invalid-access-token"
