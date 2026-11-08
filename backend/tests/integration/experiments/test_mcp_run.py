"""The MCP tools that run and read backtests end to end: the SDK's client over HTTP, signed in
with a real API key, on a real Postgres and the real job queue, with the market faked and the
worker faked or real (conftest.py); and ``POST /api/v1/backtests/check``, which
validate_backtest does the work of."""

import json
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
from candlestack.mcp import run

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # ten days after the fake market's first candle
END = START + 5 * DAY
KERAS = Path(__file__).parents[2] / "fixtures" / "keras"


def setup(model_id: str, **groups: Any) -> dict[str, Any]:
    """The arguments of a backtest of the model on stock:AAPL over five days."""
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "decision": {"rule": {"name": "threshold", "limit": 0.1}},
        "execution": {"stop_loss": 0.05, "fee_bps": 5.0},
        "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
        **groups,
    }


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Ada and Bob are on the beta list, which API keys need."""
    allowed = frozenset({"usr_adaaaaaaaa@example.com", "usr_bobbbbbbbb@example.com"})
    return settings.model_copy(update={"beta_allowlist": allowed})


@pytest.fixture(autouse=True)
def _quick_polls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run, "POLL_S", 0.05)


def key(user: Any, *scopes: str) -> str:
    """The secret of a new API key of the user with those scopes."""
    created = user.request(
        "POST", "/api/v1/me/api-keys", json={"name": "agent", "scopes": list(scopes)}
    )
    assert created.status_code == 201, created.text
    return created.json()["secret"]


@pytest.fixture
def mcp(app: FastAPI, client: TestClient) -> Callable[..., Any]:
    """``mcp(key, name, arguments)``: the result of the tool call, sent by the SDK's client
    signed in with `key`, in the TestClient's event loop (the app's lifespan runs there)."""

    async def send(key: str, fn: Callable[[Client], Awaitable[Any]]) -> Any:
        headers = {"Authorization": f"Bearer {key}"}
        async with (
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers) as http,
            Client(streamable_http_client("http://testserver/mcp", http_client=http)) as session,
        ):
            return await fn(session)

    def call(key: str, name: str, arguments: dict[str, Any]) -> Any:
        assert client.portal is not None
        return client.portal.call(send, key, lambda session: session.call_tool(name, arguments))

    return call


def answer(result: Any) -> dict[str, Any]:
    assert not result.is_error, result.content
    return result.structured_content


def problem(result: Any) -> dict[str, Any]:
    assert result.is_error
    return json.loads(result.content[0].text)


def ended(mcp: Callable[..., Any], secret: str, backtest_id: str) -> dict[str, Any]:
    """wait_for_backtest, called again until the backtest has ended (a few calls at most)."""
    for _ in range(5):
        status = answer(mcp(secret, "wait_for_backtest", {"backtest_id": backtest_id}))
        if status["ended"]:
            return status
    raise AssertionError("the backtest did not end")


@pytest.mark.usefixtures("worker")
def test_an_agent_checks_starts_waits_for_and_reads_a_backtest(ada, mcp, sql) -> None:
    model_id = ada.runnable_model()
    secret = key(ada, "read", "backtests")

    checked = answer(mcp(secret, "validate_backtest", setup(model_id)))

    assert checked["starts"] == "now"
    assert checked["queue_position"] is None
    assert checked["model"] == {"id": model_id, "name": "raw-candles.keras", "framework": "keras"}
    assert checked["warm_up"] == {"candles": 24, "kind": "exact", "load_start": START - 24 * 1800}
    assert sql("SELECT count(*) FROM backtests") == [(0,)]

    started = answer(mcp(secret, "start_backtest", setup(model_id)))

    assert started["status"] == "running"
    assert started["number"] == 1
    backtest_id = started["id"]
    assert started["url"] == f"http://testserver/backtests/{backtest_id}"

    status = ended(mcp, secret, backtest_id)

    assert status["status"] == "finished", status["error"]
    assert [step["state"] for step in status["steps"]] == ["done"] * 4

    read = answer(mcp(secret, "get_backtest", {"backtest_id": backtest_id, "trades_per_page": 2}))

    document = ada.document(backtest_id).json()
    results = document["results"]
    assert read["metrics"] == results["stats"]
    assert read["benchmark_returns"] == {"bh": results["benchmarks"]["bh"]["total_return"]}
    assert read["candles"] == document["data"]["rows"]["count"] == 240
    assert read["setup"]["model"]["id"] == model_id
    assert read["setup"]["data"]["period"] == {"start": START, "end": END}
    assert read["setup"]["execution"]["fee_bps"] == 5.0
    trades = read["trades"]
    assert trades["count"] == results["trades"]["count"]
    assert len(trades["items"]) == min(2, trades["count"])
    if trades["items"]:
        assert trades["items"][0]["open_ts"] == results["trades"]["columns"]["open_ts"][0]
    equity = read["equity"]
    assert 199 <= equity["points"] <= 200
    assert equity["rows"] == 240
    assert equity["ts"][0] == results["series"]["columns"]["ts"][0]
    assert equity["ts"][-1] == results["series"]["columns"]["ts"][-1]

    again = answer(mcp(secret, "start_backtest", read["setup"]))
    status = ended(mcp, secret, again["id"])
    compared = answer(mcp(secret, "compare_backtests", {"ids": [backtest_id, again["id"]]}))

    assert [item["id"] for item in compared["backtests"]] == [backtest_id, again["id"]]
    assert compared["differs"] == []
    total = next(row for row in compared["metrics"] if row["key"] == "total_return")
    assert total["values"][0] == total["values"][1] == results["stats"]["total_return"]


@pytest.mark.usefixtures("real_worker")
def test_a_run_through_the_real_worker(ada, mcp) -> None:
    model_id = ada.runnable_model(file=KERAS / "raw_candles.keras")
    secret = key(ada, "read", "backtests")

    started = answer(mcp(secret, "start_backtest", setup(model_id)))
    status = ended(mcp, secret, started["id"])

    assert status["status"] == "finished", status["error"]
    read = answer(mcp(secret, "get_backtest", {"backtest_id": started["id"]}))
    assert read["candles"] == 240
    assert read["metrics"]["num_trades"] == read["trades"]["count"]


def test_a_second_backtest_waits_in_the_queue(ada, mcp, worker) -> None:
    worker.gate.clear()  # the first one runs until the worker answers
    model_id = ada.runnable_model()
    secret = key(ada, "read", "backtests")
    first = answer(mcp(secret, "start_backtest", setup(model_id)))

    checked = answer(mcp(secret, "validate_backtest", setup(model_id)))
    second = answer(mcp(secret, "start_backtest", setup(model_id)))
    status = answer(mcp(secret, "get_backtest_status", {"backtest_id": second["id"]}))
    waited = answer(mcp(secret, "wait_for_backtest", {"backtest_id": second["id"], "timeout_s": 1}))

    assert (checked["starts"], checked["queue_position"]) == ("queued", 1)
    assert (second["status"], second["queue_position"]) == ("queued", 1)
    assert (status["status"], status["queue_position"]) == ("queued", 1)
    assert waited["ended"] is False
    worker.gate.set()
    assert ended(mcp, secret, first["id"])["status"] == "finished"
    assert ended(mcp, secret, second["id"])["status"] == "finished"


@pytest.mark.usefixtures("market")
def test_a_setup_that_fails_a_check_answers_the_api_s_problem(ada, mcp) -> None:
    secret = key(ada, "read", "backtests")
    without_manifest = ada.model("bare.keras")

    for name in ("validate_backtest", "start_backtest"):
        body = problem(mcp(secret, name, setup(without_manifest)))
        assert body["type"] == PROBLEMS + "model-needs-setup"
        assert body["status"] == 409


@pytest.mark.usefixtures("worker")
def test_another_account_reads_a_backtest_only_while_it_is_published(ada, bob, mcp) -> None:
    secret = key(ada, "read", "backtests")
    backtest_id = answer(mcp(secret, "start_backtest", setup(ada.runnable_model())))["id"]
    ended(mcp, secret, backtest_id)
    bobs = key(bob, "read")

    hidden = problem(mcp(bobs, "get_backtest", {"backtest_id": backtest_id}))
    assert hidden["type"] == PROBLEMS + "not-found"
    assert ada.publish(backtest_id).status_code == 200

    read = answer(mcp(bobs, "get_backtest", {"backtest_id": backtest_id}))
    status = answer(mcp(bobs, "get_backtest_status", {"backtest_id": backtest_id}))

    assert read["backtest"]["author"] == {"id": ada.id, "name": ada.id, "mine": False}
    assert read["setup"]["model"]["id"] is None
    assert status["status"] == "finished"


# --- POST /api/v1/backtests/check -------------------------------------------------------------


@pytest.mark.usefixtures("worker")
def test_the_check_answers_what_would_run_and_stores_nothing(ada, client, sql) -> None:
    model_id = ada.runnable_model()
    secret = key(ada, "read")

    response = client.post(
        "/api/v1/backtests/check",
        json=setup(
            model_id, benchmarks=[{"id": "spy", "kind": "buy_and_hold", "instrument": "stock:spy"}]
        ),
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    checked = response.json()
    assert checked["instrument"]["name"] == "Apple Inc."
    assert checked["timeframe"] == "30m"
    assert checked["session"] == "regular"
    assert checked["period"] == {"start": START, "end": END}
    assert checked["benchmarks"] == [
        {"id": "spy", "kind": "buy_and_hold", "instrument": "stock:SPY"}
    ]
    assert sql("SELECT count(*) FROM backtests") == [(0,)]


@pytest.mark.usefixtures("market")
def test_the_check_answers_the_problems_of_a_start(ada, client) -> None:
    secret = key(ada, "read")

    response = client.post(
        "/api/v1/backtests/check",
        json=setup("mdl_nonenonen"),
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "not-found"


def test_the_check_needs_a_key_with_read(ada, client) -> None:
    secret = key(ada, "backtests")

    response = client.post(
        "/api/v1/backtests/check",
        json=setup("mdl_nonenonen"),
        headers={"Authorization": f"Bearer {secret}"},
    )

    assert response.status_code == 403
    assert response.json()["scope"] == "read"


@pytest.mark.usefixtures("market")
def test_a_start_sent_again_with_its_key_answers_the_first_backtest(ada, mcp, sql) -> None:
    model_id = ada.runnable_model()
    secret = key(ada, "read", "backtests")
    arguments = {**setup(model_id), "idempotency_key": "retry-of-start-two"}

    first = answer(mcp(secret, "start_backtest", arguments))
    again = answer(mcp(secret, "start_backtest", arguments))
    changed = arguments | {"execution": {"fee_bps": 9.0}}
    other = problem(mcp(secret, "start_backtest", changed))
    new = answer(mcp(secret, "start_backtest", {**changed, "idempotency_key": "another"}))

    assert again == first
    assert other["type"] == PROBLEMS + "idempotency-key-reused"
    assert other["status"] == 422
    assert new["id"] != first["id"]
    assert sql("SELECT count(*) FROM backtests") == [(2,)]


@pytest.mark.usefixtures("worker")
def test_an_agent_waits_for_several_backtests_at_once(ada, mcp) -> None:
    model_id = ada.runnable_model()
    secret = key(ada, "read", "backtests")
    ids = [answer(mcp(secret, "start_backtest", setup(model_id)))["id"] for _ in range(2)]

    waited = None
    for _ in range(5):
        waited = answer(mcp(secret, "wait_for_backtest", {"ids": ids, "until": "all"}))
        if waited["done"]:
            break

    assert waited is not None
    assert waited["done"] is True
    assert [b["id"] for b in waited["backtests"]] == ids
    assert [b["status"] for b in waited["backtests"]] == ["finished", "finished"]
    assert all(b["eta_s"] is None for b in waited["backtests"])


@pytest.mark.usefixtures("market")
def test_a_wait_for_ids_names_one_nobody_can_read(ada, bob, mcp) -> None:
    secret = key(ada, "read", "backtests")
    bobs = answer(mcp(key(bob, "read", "backtests"), "start_backtest", setup(bob.runnable_model())))

    body = problem(mcp(secret, "wait_for_backtest", {"ids": [bobs["id"]], "timeout_s": 1}))

    assert body["type"] == PROBLEMS + "not-found"
    assert body["id"] == bobs["id"]
