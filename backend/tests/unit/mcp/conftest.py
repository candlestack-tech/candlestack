"""Fixtures of the MCP unit tests: the app's ``/mcp`` with the API keys faked (``keys``), and
``mcp``, which runs a session of the SDK's client against it over HTTP (in the TestClient's
event loop, so the app's lifespan is running). Signing in on Postgres and the real tools are
integration tests (tests/integration/mcp/)."""

from collections.abc import Awaitable, Callable, Iterator
from typing import Any, override

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.extension import ClientExtension
from mcp.client.streamable_http import streamable_http_client

from candlestack.accounts import Caller, Scope, User
from candlestack.core import ProblemError
from candlestack.mcp import http

ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")
READ_KEY = "cs_readaaaa" + "a" * 32
BACKTESTS_KEY = "cs_btaaaaaa" + "a" * 32
SCOPES = {READ_KEY: frozenset({Scope.read}), BACKTESTS_KEY: frozenset({Scope.backtests})}
URL = "http://testserver/mcp"

McpRun = Callable[..., Any]


class FakeKeys:
    """Stands in for ``accounts.key_caller``: the keys of ``SCOPES`` sign in as Ada."""

    read_key = READ_KEY
    backtests_key = BACKTESTS_KEY

    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def __call__(self, token: str, **_clients: Any) -> Caller:
        self.tokens.append(token)
        if token not in SCOPES:
            raise ProblemError(
                401,
                "invalid-api-key",
                "Invalid API key",
                "The API key is unknown.",
                headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
            )
        return Caller(ADA, SCOPES[token])


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> FakeKeys:
    fake = FakeKeys()
    monkeypatch.setattr(http, "key_caller", fake)
    return fake


@pytest.fixture
def mcp_client(app: FastAPI, keys: FakeKeys) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class ShowsApps(ClientExtension):
    """What a client that shows MCP Apps (claude.ai, Claude Desktop) says in its capabilities."""

    identifier = "io.modelcontextprotocol/ui"

    @override
    def settings(self) -> dict[str, Any]:
        return {"mimeTypes": ["text/html;profile=mcp-app"]}


@pytest.fixture
def mcp(app: FastAPI, mcp_client: TestClient) -> McpRun:
    """``mcp(fn, key=..., mode=..., apps=...)``: the result of ``await fn(session)`` for a
    session of the SDK's client signed in with `key`; `mode` as ``Client`` takes it (the
    2026-07-28 protocol by default, ``"legacy"`` for the initialize handshake); with `apps` the
    client says it shows MCP Apps (``ShowsApps``)."""

    async def run(fn: Callable[[Client], Awaitable[Any]], key: str, mode: str, apps: bool) -> Any:
        transport = httpx2.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {key}"}
        extensions = [ShowsApps()] if apps else None
        async with (
            httpx2.AsyncClient(transport=transport, headers=headers) as http_client,
            Client(
                streamable_http_client(URL, http_client=http_client),
                mode=mode,
                extensions=extensions,
            ) as session,
        ):
            return await fn(session)

    def call(
        fn: Callable[[Client], Awaitable[Any]],
        key: str = READ_KEY,
        mode: str = "auto",
        *,
        apps: bool = False,
    ) -> Any:
        assert mcp_client.portal is not None
        return mcp_client.portal.call(run, fn, key, mode, apps)

    return call
