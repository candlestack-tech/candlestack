"""The OAuth sign-in end to end with the MCP SDK's own client: it finds the authorization server
from the 401 of ``/mcp``, registers itself, sends the browser through the consent page, trades
the code and calls a tool with the token, on a real Postgres and Redis (conftest.py). The browser
is Ada's signed-in session, which allows read and backtests."""

from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from pydantic import AnyUrl

pytestmark = pytest.mark.integration

URL = "http://testserver/mcp"
CALLBACK = "http://127.0.0.1:33418/callback"


class Storage:
    """The client's token storage, in memory."""

    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client = client_info


class ConsentingBrowser:
    """The user's browser: opens the authorization URL with Ada's session, allows read and
    backtests on the consent page and lands on the client's callback."""

    def __init__(self, app: FastAPI, cookie: str) -> None:
        self._app = app
        self._cookie = cookie
        self.result: AuthorizationCodeResult | None = None

    async def open(self, authorization_url: str) -> None:
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self._app),
            base_url="http://testserver",
            headers={"Cookie": self._cookie},
        ) as browser:
            authorized = await browser.get(authorization_url)
            assert authorized.status_code == 302, authorized.text
            consent_page = authorized.headers["location"]
            request_id = parse_qs(urlsplit(consent_page).query)["request"][0]
            shown = await browser.get(f"/api/v1/oauth/requests/{request_id}")
            assert shown.json()["client"]["name"] == "Test agent"
            decided = await browser.post(
                f"/api/v1/oauth/requests/{request_id}",
                json={"decision": "allow", "scopes": ["read", "backtests"]},
                headers={"Origin": "http://testserver"},
            )
            assert decided.status_code == 200, decided.text
        back = decided.json()["redirect_to"]
        assert back.startswith(CALLBACK + "?")
        query = {key: values[0] for key, values in parse_qs(urlsplit(back).query).items()}
        self.result = AuthorizationCodeResult(
            code=query["code"], state=query["state"], iss=query["iss"]
        )

    async def callback(self) -> AuthorizationCodeResult:
        assert self.result is not None
        return self.result


def test_the_sdk_s_client_signs_in_with_oauth_and_calls_a_tool(
    app: FastAPI, client: TestClient, ada, sql
) -> None:
    storage = Storage()
    browser = ConsentingBrowser(app, ada.cookie)

    async def run() -> dict[str, Any]:
        provider = OAuthClientProvider(
            server_url=URL,
            client_metadata=OAuthClientMetadata(
                redirect_uris=[AnyUrl(CALLBACK)],
                client_name="Test agent",
                grant_types=["authorization_code", "refresh_token"],
            ),
            storage=storage,
            redirect_handler=browser.open,
            callback_handler=browser.callback,
        )
        async with (
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), auth=provider
            ) as http_client,
            Client(streamable_http_client(URL, http_client=http_client)) as session,
        ):
            result = await session.call_tool("get_me", {})
            assert not result.is_error, result.content
            assert result.structured_content is not None
            return result.structured_content

    assert client.portal is not None
    me = client.portal.call(run)

    assert me["id"] == ada.id
    assert me["scopes"] == ["read", "backtests"]
    assert storage.tokens is not None
    assert storage.tokens.access_token.startswith("csa_")
    assert storage.tokens.refresh_token is not None
    assert storage.client is not None
    assert storage.client.client_id.startswith("ocl_")
    # The SDK registers for client_secret_basic by default, so the server gave it a secret.
    assert storage.client.client_secret is not None
    [(scopes, last_used)] = sql("SELECT scopes, last_used_at FROM connected_apps")
    assert scopes == ["read", "backtests"]
    assert last_used is not None
