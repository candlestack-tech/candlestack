"""The account tools end to end: the SDK's client over HTTP, signed in with a real API key, on
a real Postgres and Redis (conftest.py)."""

import json

import pytest
from mcp import Client

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"


async def get_me(session: Client):
    return await session.call_tool("get_me", {})


def test_get_me_answers_the_key_s_user_and_scopes(mcp, keys, ada, sql) -> None:
    key = keys("publish", "read")

    result = mcp(get_me, key)

    assert not result.is_error, result.content
    me = result.structured_content
    assert me["id"] == ada.id
    assert me["email"] == ada.email
    assert me["display_name"] == ada.id
    assert me["scopes"] == ["read", "publish"]
    assert json.loads(result.content[0].text) == me
    [(last_used,)] = sql("SELECT last_used_at FROM api_keys")
    assert last_used is not None


def test_get_me_needs_read(mcp, keys) -> None:
    result = mcp(get_me, keys("backtests"))

    assert result.is_error
    problem = json.loads(result.content[0].text)
    assert problem["type"] == PROBLEMS + "missing-scope"
    assert problem["scope"] == "read"


def test_a_revoked_key_is_refused_before_any_tool(client, keys, ada) -> None:
    key = keys("read")
    [listed] = ada.request("GET", "/api/v1/me/api-keys").json()["items"]
    assert ada.request("DELETE", f"/api/v1/me/api-keys/{listed['id']}").status_code == 204

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "invalid-api-key"
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


def test_someone_off_the_beta_list_is_refused(client, account, sql) -> None:
    bob = account("usr_bobbbbbbbb")
    secret = bob.request("POST", "/api/v1/me/api-keys", json={"name": "agent", "scopes": ["read"]})
    assert secret.status_code == 201

    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "Authorization": f"Bearer {secret.json()['secret']}",
            "Accept": "application/json",
        },
    )

    assert response.status_code == 401
    assert sql("SELECT last_used_at FROM api_keys") == [(None,)]
