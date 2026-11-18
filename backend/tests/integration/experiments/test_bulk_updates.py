"""Starring and publishing many backtests at once, over HTTP (``POST /api/v1/backtests/update``)
and MCP (``star_backtests``, ``publish_backtests``) against a real Postgres (conftest.py): the
IDs found and not found, what cannot be published, the limit, a call sent again and the scopes."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from candlestack.core import Settings

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
SPEC = {"made": "in the app"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Ada is on the beta list, so her keys sign in."""
    return settings.model_copy(update={"beta_allowlist": frozenset({"usr_adaaaaaaaa@example.com"})})


def update_many(user: Any, **payload: Any) -> httpx2.Response:
    return user.request("POST", "/api/v1/backtests/update", json=payload)


def problem(response: httpx2.Response, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    found = response.json()
    assert found["type"] == PROBLEMS + slug
    return found


def marks(user: Any) -> dict[str, tuple[bool, bool]]:
    """Each of the user's backtests: whether it is starred and published."""
    listed = user.backtests(sort="number", order="asc", per_page=100).json()["backtests"]
    return {backtest["id"]: (backtest["starred"], backtest["public"]) for backtest in listed}


# --- Over HTTP ------------------------------------------------------------------------------


def test_many_are_starred_and_those_not_found_are_named(ada, bob) -> None:
    first, second, third = ada.backtest(1), ada.backtest(2, starred=True), ada.backtest(3)
    of_bob = bob.backtest(1)

    starred = update_many(ada, ids=[third, "bt_nonexistent", second, of_bob, third], starred=True)

    assert starred.status_code == 200, starred.text
    assert starred.headers["cache-control"] == "no-store"
    assert starred.json() == {
        "updated": [third, second],
        "count": 2,
        "not_found": ["bt_nonexistent", of_bob],
        "cannot_publish": None,
    }
    assert marks(ada) == {first: (False, False), second: (True, False), third: (True, False)}
    assert marks(bob) == {of_bob: (False, False)}
    # The same request again changes nothing more, and unstarring takes them back.
    assert update_many(ada, ids=[third, second], starred=True).json()["updated"] == [third, second]
    update_many(ada, ids=[second, third], starred=False)
    assert marks(ada) == {first: (False, False), second: (False, False), third: (False, False)}


def test_only_finished_backtests_made_in_the_app_are_published(ada, bob) -> None:
    finished = ada.backtest(1, spec=SPEC)
    running = ada.backtest(2, status="running", spec=SPEC)
    failed = ada.backtest(3, status="failed", spec=SPEC)
    seeded = ada.backtest(4)  # test data: no result document
    already = ada.backtest(5, spec=SPEC, published=5)
    before = bob.experiments().json()["experiments"]

    published = update_many(ada, ids=[finished, running, failed, seeded, already], public=True)

    assert published.json() == {
        "updated": [finished, already],
        "count": 2,
        "not_found": [],
        "cannot_publish": [running, failed, seeded],
    }
    shown = {item["id"]: item for item in bob.experiments().json()["experiments"]}
    assert set(shown) == {finished, already}
    # One published already keeps when it was.
    assert shown[already]["published_at"] == before[0]["published_at"]


def test_a_backtest_that_cannot_be_published_is_not_starred_either(ada) -> None:
    finished, running = ada.backtest(1, spec=SPEC), ada.backtest(2, status="running", spec=SPEC)

    both = update_many(ada, ids=[finished, running], starred=True, public=True).json()

    assert (both["updated"], both["cannot_publish"]) == ([finished], [running])
    assert marks(ada) == {finished: (True, True), running: (False, False)}


def test_unpublishing_takes_them_out_of_experiments(ada, bob) -> None:
    first, second = ada.backtest(1, spec=SPEC, published=5), ada.backtest(2, spec=SPEC)

    taken = update_many(ada, ids=[first, second], public=False).json()

    assert taken == {
        "updated": [first, second],
        "count": 2,
        "not_found": [],
        "cannot_publish": None,
    }
    assert bob.experiments().json()["total"] == 0


def test_a_bulk_update_needs_ids_and_what_to_change(ada) -> None:
    problem(update_many(ada, ids=["bt_a"]), 422, "validation")
    problem(update_many(ada, ids=["bt_a"], starred=None, public=None), 422, "validation")
    problem(update_many(ada, ids=[], starred=True), 422, "validation")
    problem(update_many(ada, ids=[f"bt_{n}" for n in range(101)], starred=True), 422, "validation")
    problem(update_many(ada, ids=["bt_a"], starred=True, name="x"), 422, "validation")
    assert update_many(ada, ids=[f"bt_{n}" for n in range(100)], starred=True).json()["count"] == 0


# --- Scopes and MCP -------------------------------------------------------------------------


@pytest.fixture
def key(ada) -> Callable[..., str]:
    def make(*scopes: str) -> str:
        created = ada.request(
            "POST", "/api/v1/me/api-keys", json={"name": "agent", "scopes": list(scopes)}
        )
        assert created.status_code == 201, created.text
        return created.json()["secret"]

    return make


@pytest.fixture
def mcp(app: FastAPI, client: TestClient) -> Callable[..., Any]:
    async def run(fn: Callable[[Client], Awaitable[Any]], secret: str) -> Any:
        transport = httpx2.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {secret}"}
        async with (
            httpx2.AsyncClient(transport=transport, headers=headers) as http_client,
            Client(streamable_http_client("http://testserver/mcp", http_client=http_client)) as s,
        ):
            return await fn(s)

    def call(fn: Callable[[Client], Awaitable[Any]], secret: str) -> Any:
        assert client.portal is not None
        return client.portal.call(run, fn, secret)

    return call


def tool(name: str, **arguments: Any) -> Callable[[Client], Awaitable[Any]]:
    async def call(session: Client) -> Any:
        return await session.call_tool(name, arguments)

    return call


def tool_problem(result: Any, slug: str) -> dict[str, Any]:
    assert result.is_error
    found = json.loads(result.content[0].text)
    assert found["type"] == PROBLEMS + slug
    return found


def test_an_api_key_needs_backtests_to_star_and_publish_to_publish(ada, key, client) -> None:
    backtest_id = ada.backtest(1, spec=SPEC)

    def send(secret: str, **payload: Any) -> httpx2.Response:
        return client.post(
            "/api/v1/backtests/update",
            json={"ids": [backtest_id], **payload},
            headers={"Authorization": f"Bearer {secret}"},
        )

    reader, writer, publisher = key("read"), key("backtests"), key("publish")

    assert problem(send(reader, starred=True), 403, "missing-scope")["scope"] == "backtests"
    assert problem(send(writer, public=True), 403, "missing-scope")["scope"] == "publish"
    assert (
        problem(send(publisher, starred=True, public=True), 403, "missing-scope")["scope"]
        == "backtests"
    )
    assert marks(ada) == {backtest_id: (False, False)}
    assert send(writer, starred=True, public=None).json()["count"] == 1
    assert send(publisher, public=True).json()["count"] == 1
    assert marks(ada) == {backtest_id: (True, True)}


def test_an_agent_stars_many_backtests(ada, key, mcp) -> None:
    first, second = ada.backtest(1), ada.backtest(2)
    writer = key("backtests")

    without_scope = mcp(tool("star_backtests", ids=[first]), key("read"))
    starred = mcp(tool("star_backtests", ids=[first, second, "bt_nonexistent"]), writer)
    again = mcp(tool("star_backtests", ids=[first, second]), writer)
    too_many = mcp(tool("star_backtests", ids=[f"bt_{n}" for n in range(101)]), writer)

    tool_problem(without_scope, "missing-scope")
    assert starred.structured_content == {
        "updated": [first, second],
        "count": 2,
        "not_found": ["bt_nonexistent"],
        "cannot_publish": None,
    }
    # A model reads the text without the null fields.
    assert json.loads(starred.content[0].text) == {
        "updated": [first, second],
        "count": 2,
        "not_found": ["bt_nonexistent"],
    }
    assert again.structured_content["updated"] == [first, second]
    tool_problem(too_many, "validation")
    assert marks(ada) == {first: (True, False), second: (True, False)}

    mcp(tool("star_backtests", ids=[first], starred=False), writer)
    assert marks(ada) == {first: (False, False), second: (True, False)}


def test_an_agent_publishes_and_unpublishes_many_backtests(ada, bob, key, mcp) -> None:
    finished, running = ada.backtest(1, spec=SPEC), ada.backtest(2, status="running", spec=SPEC)
    publisher = key("publish")

    without_scope = mcp(tool("publish_backtests", ids=[finished]), key("backtests"))
    published = mcp(tool("publish_backtests", ids=[finished, running]), publisher)

    tool_problem(without_scope, "missing-scope")
    assert published.structured_content == {
        "updated": [finished],
        "count": 1,
        "not_found": [],
        "cannot_publish": [running],
    }
    assert [item["id"] for item in bob.experiments().json()["experiments"]] == [finished]

    taken = mcp(tool("publish_backtests", ids=[finished, running], published=False), publisher)

    assert taken.structured_content["updated"] == [finished, running]
    assert bob.experiments().json()["total"] == 0
