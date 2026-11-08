"""Deleting backtests and the most an account keeps, over HTTP and MCP against a real Postgres and
the real job queue (conftest.py): one, many by their IDs and a whole sweep, whatever their
status, a published one gone for everyone, and ``backtest-limit`` on a start and a sweep."""

import json
import time
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
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
BH = {"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}
LIMIT = 4


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Ada is on the beta list, so her keys sign in, and an account keeps ``LIMIT`` backtests."""
    return settings.model_copy(
        update={
            "beta_allowlist": frozenset({"usr_adaaaaaaaa@example.com"}),
            "backtest_limit": LIMIT,
        }
    )


def body(model_id: str) -> dict[str, Any]:
    """A new backtest of the model on stock:AAPL over five days."""
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "decision": {"rule": {"name": "threshold", "limit": 0.1}},
        "benchmarks": [BH],
    }


def sweep_body(model_id: str) -> dict[str, Any]:
    rules = [{"name": "threshold", "limit": 0.05}, {"name": "threshold", "limit": 0.2}]
    return {"base": body(model_id), "vary": {"rules": rules}}


def soon(condition: Callable[[], object], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "it did not happen in time"
        time.sleep(0.02)


def delete(user: Any, backtest_id: str) -> httpx2.Response:
    return user.request("DELETE", f"/api/v1/backtests/{backtest_id}")


def delete_many(user: Any, **payload: Any) -> httpx2.Response:
    return user.request("POST", "/api/v1/backtests/delete", json=payload)


def problem(response: httpx2.Response, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    found = response.json()
    assert found["type"] == PROBLEMS + slug
    return found


def ids(user: Any) -> list[str]:
    return [
        backtest["id"]
        for backtest in user.backtests(sort="number", order="asc").json()["backtests"]
    ]


# --- One backtest ---------------------------------------------------------------------------


def test_a_backtest_is_deleted_for_good(ada, bob) -> None:
    kept, gone = ada.backtest(1), ada.backtest(2)

    of_another = delete(bob, gone)
    deleted = delete(ada, gone)
    again = delete(ada, gone)

    problem(of_another, 404, "not-found")
    assert deleted.status_code == 204
    assert deleted.content == b""
    problem(again, 404, "not-found")
    assert ids(ada) == [kept]
    assert ada.sql("SELECT count(*) FROM backtests WHERE id = :id", id=gone)[0][0] == 0


def test_a_published_backtest_is_gone_for_everyone(ada, bob) -> None:
    published = ada.backtest(1, published=5)
    assert bob.experiments().json()["total"] == 1

    assert delete(ada, published).status_code == 204

    assert bob.experiments().json()["total"] == 0
    problem(bob.document(published), 404, "not-found")
    problem(bob.request("GET", "/api/v1/compare", params={"ids": published}), 404, "not-found")


@pytest.mark.usefixtures("market")
def test_a_queued_backtest_leaves_the_queue(ada) -> None:
    model_id = ada.runnable_model()
    running = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    first = ada.start(body(model_id)).json()["backtest"]["id"]
    second = ada.start(body(model_id)).json()["backtest"]["id"]

    assert delete(ada, first).status_code == 204

    assert ada.document(second).json()["backtest"]["queue_position"] == 1
    assert ada.document(running).json()["backtest"]["status"] == "running"


@pytest.mark.usefixtures("market")
def test_a_running_backtest_is_stopped_and_the_next_one_starts(ada, app) -> None:
    model_id = ada.runnable_model()
    running = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    queued = ada.start(body(model_id)).json()["backtest"]["id"]
    runner = app.state.backtest_runner
    assert running in runner._tasks.values()

    assert delete(ada, running).status_code == 204

    assert ada.document(queued).json()["backtest"]["status"] == "running"
    assert ids(ada) == [queued]
    # Its run was cancelled; the next one's runs.
    soon(lambda: running not in runner._tasks.values())
    assert queued in runner._tasks.values()


# --- Many backtests -------------------------------------------------------------------------


def test_many_by_their_ids_skip_those_not_found(ada, bob) -> None:
    first, second, third = ada.backtest(1), ada.backtest(2), ada.backtest(3)
    of_bob = bob.backtest(1)

    deleted = delete_many(ada, ids=[third, "bt_nonexistent", first, of_bob, third])

    assert deleted.status_code == 200, deleted.text
    assert deleted.headers["cache-control"] == "no-store"
    assert deleted.json() == {
        "deleted": [third, first],
        "count": 2,
        "not_found": ["bt_nonexistent", of_bob],
    }
    assert ids(ada) == [second]
    assert ids(bob) == [of_bob]
    # The same request again deletes nothing more.
    assert delete_many(ada, ids=[third, first]).json()["not_found"] == [third, first]


def test_many_need_ids_or_a_sweep(ada) -> None:
    problem(delete_many(ada), 422, "validation")
    problem(delete_many(ada, ids=["bt_a"], sweep_id="sw_a"), 422, "validation")
    problem(delete_many(ada, ids=[]), 422, "validation")
    problem(delete_many(ada, ids=[f"bt_{n}" for n in range(101)]), 422, "validation")
    problem(delete_many(ada, sweep_id="sw_nonexistent"), 404, "not-found")


@pytest.mark.usefixtures("market")
def test_a_sweep_loses_a_deleted_backtest_and_goes_with_its_last(ada, bob) -> None:
    model_id = ada.runnable_model()
    started = ada.request("POST", "/api/v1/sweeps", json=sweep_body(model_id))
    assert started.status_code == 201, started.text
    sweep_id = started.json()["id"]
    first, second = (backtest["id"] for backtest in started.json()["backtests"])
    alone = ada.backtest(10)

    assert delete(ada, second).status_code == 204
    sweep = ada.request("GET", f"/api/v1/sweeps/{sweep_id}").json()
    problem(delete_many(bob, sweep_id=sweep_id), 404, "not-found")
    deleted = delete_many(ada, sweep_id=sweep_id)

    assert [backtest["id"] for backtest in sweep["backtests"]] == [first]
    assert sweep["count"] == 1
    assert deleted.json() == {"deleted": [first], "count": 1, "not_found": []}
    problem(ada.request("GET", f"/api/v1/sweeps/{sweep_id}"), 404, "not-found")
    assert ids(ada) == [alone]


# --- The limit ------------------------------------------------------------------------------


@pytest.mark.usefixtures("market")
def test_a_start_beyond_the_limit_is_refused_until_one_is_deleted(ada) -> None:
    model_id = ada.runnable_model()
    for number in range(1, LIMIT):
        ada.backtest(number, status="failed")
    assert ada.start(body(model_id)).status_code == 201

    refused = problem(ada.start(body(model_id)), 409, "backtest-limit")
    checked = problem(
        ada.request("POST", "/api/v1/backtests/check", json=body(model_id)), 409, "backtest-limit"
    )
    assert refused["limit"] == LIMIT
    assert refused["count"] == LIMIT
    assert "adding" not in refused
    assert "Delete old backtests" in refused["detail"]
    assert checked["count"] == LIMIT
    assert ada.backtests().json()["total"] == LIMIT

    assert delete(ada, ids(ada)[0]).status_code == 204
    assert ada.start(body(model_id)).status_code == 201


@pytest.mark.usefixtures("market")
def test_a_sweep_must_fit_the_limit(ada) -> None:
    model_id = ada.runnable_model()
    for number in range(1, LIMIT):
        ada.backtest(number)

    refused = problem(
        ada.request("POST", "/api/v1/sweeps", json=sweep_body(model_id)), 409, "backtest-limit"
    )
    dry_run = ada.request(
        "POST", "/api/v1/sweeps", json={**sweep_body(model_id), "validate_only": True}
    )

    assert refused["limit"] == LIMIT
    assert refused["count"] == LIMIT - 1
    assert refused["adding"] == 2
    assert "Delete at least 1 old backtests" in refused["detail"]
    problem(dry_run, 409, "backtest-limit")
    assert ada.backtests().json()["total"] == LIMIT - 1


# --- The MCP tool ---------------------------------------------------------------------------


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


def tool(**arguments: Any) -> Callable[[Client], Awaitable[Any]]:
    async def call(session: Client) -> Any:
        return await session.call_tool("delete_backtests", arguments)

    return call


def tool_problem(result: Any, slug: str) -> dict[str, Any]:
    assert result.is_error
    found = json.loads(result.content[0].text)
    assert found["type"] == PROBLEMS + slug
    return found


def test_an_agent_deletes_only_the_count_it_confirmed(ada, key, mcp) -> None:
    first, second = ada.backtest(1), ada.backtest(2)
    writer, reader = key("backtests"), key("read")

    wrong = mcp(tool(ids=[first, second, "bt_nonexistent"], confirm_count=3), writer)
    neither = mcp(tool(confirm_count=1), writer)
    without_scope = mcp(tool(ids=[first], confirm_count=1), reader)
    assert ids(ada) == [first, second]
    done = mcp(tool(ids=[first, second, "bt_nonexistent"], confirm_count=2), writer)

    assert tool_problem(wrong, "confirmation-mismatch")["count"] == 2
    tool_problem(neither, "validation")
    tool_problem(without_scope, "missing-scope")
    assert not done.is_error
    assert done.structured_content == {
        "deleted": [first, second],
        "count": 2,
        "not_found": ["bt_nonexistent"],
    }
    assert ids(ada) == []


@pytest.mark.usefixtures("market")
def test_an_agent_deletes_a_sweep(ada, key, mcp) -> None:
    model_id = ada.runnable_model()
    started = ada.request("POST", "/api/v1/sweeps", json=sweep_body(model_id)).json()

    done = mcp(tool(sweep_id=started["id"], confirm_count=2), key("backtests"))

    assert done.structured_content["count"] == 2
    assert ids(ada) == []
