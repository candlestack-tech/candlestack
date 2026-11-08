"""The account tools end to end: the SDK's client over HTTP, signed in with a real API key, on
a real Postgres and Redis (conftest.py)."""

import json

import pytest
from mcp import Client

from candlestack.accounts import dependencies
from candlestack.core import ratelimit

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


def test_a_tool_call_over_the_rate_limit_is_the_tool_s_error(
    mcp, keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dependencies, "API_KEY_RATE_LIMIT", 2)
    # Every call in one window of the limit, 55 s before its end.
    monkeypatch.setattr(ratelimit, "_time", lambda: 1790372345.0)
    key = keys("read")

    async def calls(session: Client):
        # Connecting and listing the tools do not count: the two calls fit, the third not.
        await session.list_tools()
        answers = [await get_me(session) for _ in range(3)]
        await session.list_tools()
        return answers

    first, second, third = mcp(calls, key)

    assert not first.is_error, first.content
    assert not second.is_error, second.content
    assert third.is_error
    problem = json.loads(third.content[0].text)
    assert problem["type"] == PROBLEMS + "rate-limited"
    assert problem["limit"] == 2
    assert problem["retry_after_s"] == 55
    assert "Retry in 55 seconds" in problem["detail"]
