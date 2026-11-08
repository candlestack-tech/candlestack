"""Client metadata and redirect URIs (``accounts.client_metadata``): the rules, the loopback
ports of native apps, and the guarded fetch of a client ID metadata document, with the address
lookup faked and the document served by respx. The fetch through the routes is an integration
test (tests/integration/accounts/test_oauth.py)."""

import asyncio

import httpx
import pytest
import respx

from candlestack.accounts import client_metadata
from candlestack.accounts.client_metadata import (
    ClientDocumentFailed,
    ClientDocuments,
    redirect_uri_matches,
    redirect_uri_problem,
)

URL = "https://app.example.com/client.json"


@pytest.mark.parametrize(
    "uri",
    [
        "https://claude.ai/api/mcp/auth_callback",
        "http://127.0.0.1:3118/callback",
        "http://[::1]/callback",
        "http://localhost:8080/cb?x=1",
    ],
)
def test_https_and_loopback_redirect_uris_are_allowed(uri: str) -> None:
    assert redirect_uri_problem(uri) is None


@pytest.mark.parametrize(
    "uri",
    [
        "http://claude.ai/callback",
        "https://claude.ai/cb#fragment",
        "https://user:pw@claude.ai/cb",
        "cursor://callback",
        "https://claude.ai:99999/cb",
        "https:///nohost",
        "https://claude.ai/" + "x" * 600,
    ],
)
def test_other_redirect_uris_are_refused(uri: str) -> None:
    assert redirect_uri_problem(uri) is not None


@pytest.mark.parametrize(
    ("given", "matches"),
    [
        ("https://claude.ai/cb", True),
        ("https://claude.ai/cb/", False),
        ("https://claude.ai:8443/cb", False),
        ("http://127.0.0.1:50000/cb", True),
        ("http://127.0.0.1/cb", True),
        ("http://127.0.0.1:50000/other", False),
        ("http://localhost:50000/cb", False),
        ("http://127.0.0.1:99999/cb", False),
    ],
)
def test_redirect_uris_match_exactly_but_for_loopback_ports(given: str, matches: bool) -> None:
    registered = ["https://claude.ai/cb", "http://127.0.0.1:3118/cb"]

    assert redirect_uri_matches(registered, given) is matches


async def _public(_host: str, _port: int) -> list[str]:
    return ["2606:2800:220:1::1"]


def _document(url: str = URL) -> dict[str, object]:
    return {"client_id": url, "client_name": "App", "redirect_uris": ["https://app.example.com/cb"]}


@pytest.mark.anyio
async def test_a_document_on_ipv6_and_another_port_comes_from_the_checked_address() -> None:
    url = "https://app.example.com:8443/client.json?v=2"
    with respx.mock() as router:
        route = router.get("https://[2606:2800:220:1::1]:8443/client.json?v=2").respond(
            200, json=_document(url)
        )
        async with httpx.AsyncClient() as http:
            metadata = await ClientDocuments(http, _public).fetch(url)

    assert metadata.client_name == "App"
    assert route.calls[0].request.headers["host"] == "app.example.com:8443"


@pytest.mark.anyio
async def test_a_document_that_takes_too_long_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(_host: str, _port: int) -> list[str]:
        await asyncio.sleep(1)
        return ["93.184.216.34"]

    monkeypatch.setattr(client_metadata, "FETCH_SECONDS", 0.01)
    async with httpx.AsyncClient() as http:
        with pytest.raises(ClientDocumentFailed, match="too long"):
            await ClientDocuments(http, slow).fetch(URL)


@pytest.mark.anyio
async def test_a_host_without_addresses_is_refused() -> None:
    async def nothing(_host: str, _port: int) -> list[str]:
        return []

    async with httpx.AsyncClient() as http:
        with pytest.raises(ClientDocumentFailed, match="public internet"):
            await ClientDocuments(http, nothing).fetch(URL)


@pytest.mark.anyio
async def test_a_document_that_is_no_object_is_refused() -> None:
    with respx.mock() as router:
        router.get("https://[2606:2800:220:1::1]/client.json").respond(200, json=[URL])
        async with httpx.AsyncClient() as http:
            with pytest.raises(ClientDocumentFailed, match="client_id is not its URL"):
                await ClientDocuments(http, _public).fetch(URL)
