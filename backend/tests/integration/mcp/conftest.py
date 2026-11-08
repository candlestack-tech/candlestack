"""Fixtures of the MCP integration tests: the app on a real Postgres and Redis
(tests/integration/conftest.py), with Ada on the beta list, and ``mcp``, which runs a session of
the SDK's client against ``/mcp`` over HTTP, signed in with a real API key.

Test modules cannot import this file, so ``keys`` makes the keys they sign in with."""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from candlestack.core import Settings

ADA = "usr_adaaaaaaaa"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(autouse=True)
def _empty(empty_tables: None) -> None:
    """Every test starts without users."""


@pytest.fixture
def settings(database_url: str, redis_url: str, tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=database_url,
        redis_url=redis_url,
        model_storage_dir=tmp_path / "models",
        beta_allowlist=f"{ADA}@example.com",
    )


@pytest.fixture
def ada(account):
    return account(ADA)


@pytest.fixture
def keys(ada) -> Callable[..., str]:
    """``keys("read", "backtests")``: the secret of a new key of Ada's with those scopes."""

    def make(*scopes: str) -> str:
        created = ada.request(
            "POST", "/api/v1/me/api-keys", json={"name": "agent", "scopes": list(scopes)}
        )
        assert created.status_code == 201, created.text
        return created.json()["secret"]

    return make


@pytest.fixture
def mcp(app: FastAPI, client: TestClient) -> Callable[..., Any]:
    """``mcp(fn, key)``: the result of ``await fn(session)`` for a session of the SDK's client
    signed in with `key`, in the TestClient's event loop (the app's lifespan runs there)."""

    async def run(fn: Callable[[Client], Awaitable[Any]], key: str) -> Any:
        transport = httpx2.ASGITransport(app=app)
        async with (
            httpx2.AsyncClient(
                transport=transport, headers={"Authorization": f"Bearer {key}"}
            ) as http_client,
            Client(streamable_http_client("http://testserver/mcp", http_client=http_client)) as s,
        ):
            return await fn(s)

    def call(fn: Callable[[Client], Awaitable[Any]], key: str) -> Any:
        assert client.portal is not None
        return client.portal.call(run, fn, key)

    return call
