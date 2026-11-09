"""``/mcp`` over plain HTTP with the API keys faked (conftest.py): what refuses a request before
the SDK sees it, the 401 the MCP authorization spec asks for, and the protected resource
metadata it points to."""

import json

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


class Counted:
    """Stands in for ``accounts.count_request``: records whether each request was light and
    refuses the requests that do work, as a key over its rate limit does, unless `refuse` is
    false."""

    def __init__(self) -> None:
        self.light: list[bool] = []
        self.refuse = True

    async def __call__(self, _limiter: object, _caller: Caller, *, light: bool = False) -> None:
        self.light.append(light)
        if self.refuse and not light:
            raise ProblemError(
                429,
                "rate-limited",
                "Too many requests",
                "Retry in 7 seconds.",
                headers={"Retry-After": "7"},
                extensions={"limit": 120, "retry_after_s": 7},
            )


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch, keys) -> Counted:
    fake = Counted()
    monkeypatch.setattr(http, "count_request", fake)
    return fake


def signed_in(keys) -> dict[str, str]:
    return {**ACCEPT, "Authorization": f"Bearer {keys.read_key}"}


def test_a_tool_call_over_the_rate_limit_is_the_tool_s_error(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    call = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_me"}}

    response = mcp_client.post("/mcp", json=call, headers=signed_in(keys))

    # An answer the model reads, with the problem and when to try again; not an HTTP 429.
    assert response.status_code == 200
    answer = response.json()
    assert (answer["jsonrpc"], answer["id"]) == ("2.0", 7)
    assert answer["result"]["isError"] is True
    [content] = answer["result"]["content"]
    assert json.loads(content["text"]) == {
        "type": PROBLEMS + "rate-limited",
        "title": "Too many requests",
        "status": 429,
        "detail": "Retry in 7 seconds.",
        "limit": 120,
        "retry_after_s": 7,
    }
    assert counted.light == [False]
    # The key was checked before anything else, and once.
    assert keys.tokens == [keys.read_key]


@pytest.mark.parametrize("call_id", [None, True, 1.5, {"a": 1}])
def test_a_tool_call_without_an_id_to_answer_stays_a_429(
    mcp_client: TestClient, keys, counted: Counted, call_id: object
) -> None:
    call = {"jsonrpc": "2.0", "id": call_id, "method": "tools/call", "params": {"name": "x"}}

    response = mcp_client.post("/mcp", json=call, headers=signed_in(keys))

    assert response.status_code == 429
    assert response.json()["retry_after_s"] == 7


@pytest.mark.parametrize(
    "message",
    [
        INITIALIZE,
        {"jsonrpc": "2.0", "id": 2, "method": "server/discover"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ],
)
def test_connecting_and_listing_count_as_light_requests(
    mcp_client: TestClient, keys, counted: Counted, message: dict[str, object]
) -> None:
    response = mcp_client.post("/mcp", json=message, headers=signed_in(keys))

    assert response.status_code != 429
    assert counted.light == [True]


def test_other_requests_over_the_rate_limit_stay_a_429(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    read = {"jsonrpc": "2.0", "id": 3, "method": "resources/read", "params": {"uri": "x"}}

    response = mcp_client.post("/mcp", json=read, headers=signed_in(keys))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "7"
    assert response.json()["retry_after_s"] == 7
    assert counted.light == [False]


def test_the_key_is_checked_before_the_body_is_read(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    response = mcp_client.post(
        "/mcp",
        content=b"{}",
        headers={**ACCEPT, "Authorization": "Bearer cs_nope", "Content-Length": "999999999"},
    )

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "invalid-api-key"
    assert counted.light == []


def test_a_body_declared_too_large_is_refused_unread(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    counted.refuse = False

    response = mcp_client.post(
        "/mcp",
        content=b"{}",
        headers={**signed_in(keys), "Content-Length": str(http.BODY_MAX + 1)},
    )

    assert response.status_code == 413
    assert response.json()["type"] == PROBLEMS + "request-too-large"
    # It still counts in full, so a key cannot send such bodies unlimited.
    assert counted.light == [False]


def test_a_body_too_large_over_the_rate_limit_is_rate_limited(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    response = mcp_client.post(
        "/mcp",
        content=b"{}",
        headers={**signed_in(keys), "Content-Length": str(http.BODY_MAX + 1)},
    )

    assert response.status_code == 429
    assert counted.light == [False]


def test_a_body_without_its_length_is_read_only_up_to_the_limit(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    counted.refuse = False

    def chunks():
        for _ in range(3):
            yield b" " * (http.BODY_MAX // 2 + 1)

    response = mcp_client.post("/mcp", content=chunks(), headers=signed_in(keys))

    assert response.status_code == 413
    assert response.json()["type"] == PROBLEMS + "request-too-large"
    assert counted.light == [False]


def test_a_light_method_in_a_large_body_counts_in_full(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    listed = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}).encode()
    padded = listed + b" " * http.LIGHT_BODY_MAX

    response = mcp_client.post(
        "/mcp", content=padded, headers={**signed_in(keys), "Content-Type": "application/json"}
    )

    assert response.status_code == 429
    assert counted.light == [False]


def test_a_batch_counts_in_full_and_is_no_tool_s_error(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    batch = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "get_me"}},
    ]

    response = mcp_client.post("/mcp", json=batch, headers=signed_in(keys))

    assert response.status_code == 429
    assert counted.light == [False]


def test_a_body_at_the_limit_reaches_the_server_whole(
    mcp_client: TestClient, keys, counted: Counted
) -> None:
    counted.refuse = False
    listed = json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}).encode()
    padded = listed + b" " * (http.BODY_MAX - len(listed))

    response = mcp_client.post(
        "/mcp",
        content=padded,
        headers={
            **signed_in(keys),
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-11-25",
        },
    )

    assert response.status_code == 200
    assert "get_me" in [tool["name"] for tool in response.json()["result"]["tools"]]
    # Too large a body for a light request: it counts in full.
    assert counted.light == [False]


@pytest.mark.parametrize(
    ("tool", "meta", "accept", "streamed"),
    [
        ("wait_for_backtest", {"progressToken": "p1"}, ACCEPT["Accept"], True),
        ("wait_for_sweep", {"progressToken": 7}, ACCEPT["Accept"], True),
        ("wait_for_backtest", None, ACCEPT["Accept"], False),
        ("wait_for_backtest", {"progressToken": True}, ACCEPT["Accept"], False),
        ("wait_for_backtest", {"progressToken": "p1"}, "application/json", False),
        ("get_backtest_status", {"progressToken": "p1"}, ACCEPT["Accept"], False),
        ("no_such_tool", {"progressToken": "p1"}, ACCEPT["Accept"], False),
    ],
    ids=["token", "integer-token", "no-token", "not-a-token", "no-sse", "no-progress", "unknown"],
)
def test_a_tool_call_that_asks_for_progress_is_answered_as_a_stream(
    mcp_client: TestClient,
    keys,
    tool: str,
    meta: dict[str, object] | None,
    accept: str,
    streamed: bool,
) -> None:
    # Arguments that do not fit, so the tool answers its problem without reading anything.
    params: dict[str, object] = {"name": tool, "arguments": {"timeout_s": 0}}
    if meta is not None:
        params["_meta"] = meta
    call = {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": params}
    headers = {
        **signed_in(keys),
        "Accept": accept,
        # The initialize handshake's protocol, whose SSE answer is a stream from the start.
        "MCP-Protocol-Version": "2025-06-18",
    }

    response = mcp_client.post("/mcp", json=call, headers=headers)

    assert response.status_code == 200, response.text
    kind = response.headers["content-type"].split(";")[0]
    assert kind == ("text/event-stream" if streamed else "application/json")
    assert '"isError":true' in response.text.replace(" ", "")
