"""Comparing backtests over HTTP (``GET /api/v1/compare``) against a real Postgres and the job
queue (conftest.py): the user's own and others' published backtests, from their documents; the
marking rules themselves are unit tests (tests/unit/experiments/test_compare.py)."""

from typing import Any

import pytest

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
BH = {"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}


def body(model_id: str, limit: float = 0.1) -> dict[str, Any]:
    """A new backtest of the model on stock:AAPL over five days."""
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "decision": {"rule": {"name": "threshold", "limit": limit}},
        "execution": {"fill": "next_open", "fee_bps": 5.0},
        "benchmarks": [BH],
    }


def compare(user: Any, *ids: str) -> Any:
    return user.request("GET", "/api/v1/compare", params={"ids": ",".join(ids)})


def test_the_users_own_and_others_published_backtests_side_by_side(ada, bob, worker) -> None:
    bob.sql("UPDATE users SET display_name = 'Bob Stone' WHERE id = :id", id=bob.id)
    own = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    own_document = ada.ended(own)
    theirs = bob.start(body(bob.runnable_model(), limit=0.3)).json()["backtest"]["id"]
    their_document = bob.ended(theirs)
    bob.publish(theirs)
    worker.failure = ("load", "Keras could not load the model file.")
    failed = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    ada.ended(failed)

    response = compare(ada, theirs, own, failed)

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    shown = response.json()
    assert [backtest["id"] for backtest in shown["backtests"]] == [theirs, own, failed]
    first, second, third = shown["backtests"]
    assert first["author"] == {"id": bob.id, "name": "Bob Stone", "mine": False}
    assert (first["public"], second["author"]["mine"]) == (True, True)
    assert (third["status"], third["equity"], third["drawdown"], third["buy_and_hold"]) == (
        "failed",
        None,
        None,
        None,
    )
    assert third["error"]
    stats = [their_document["results"]["stats"], own_document["results"]["stats"]]
    for row in shown["metrics"]:
        assert row["values"] == [stats[0][row["key"]], stats[1][row["key"]], None], row["key"]
        assert row["marks"][2] is None, row["key"]
    assert second["setup"]["decision"]["rule"] == {"name": "threshold", "limit": 0.1}
    assert second["setup"]["instrument"] == "stock:AAPL"
    assert shown["differs"] == ["decision.rule.limit"]
    times = shown["times"]
    assert 2 <= len(times) <= 200
    series = own_document["results"]["series"]["columns"]
    assert (times[0], times[-1]) == (series["ts"][0], series["ts"][-1])
    assert second["equity"][0] == 0.0
    assert second["equity"][-1] == pytest.approx(stats[1]["total_return"], abs=1e-3)
    assert second["buy_and_hold"][-1] == pytest.approx(stats[1]["buy_and_hold_return"], abs=1e-3)
    falls = [value for value in second["drawdown"] if value is not None]
    assert max(falls) <= 0
    # Sampled, a fall never goes below the largest.
    assert min(falls) >= stats[1]["max_drawdown"] - 1e-3
    assert bob.email not in response.text


def test_a_running_backtest_is_listed_without_values(ada, worker) -> None:
    finished = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    ada.ended(finished)
    worker.gate.clear()  # the next one waits for the worker: it keeps running
    running = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]

    shown = compare(ada, finished, running).json()

    second = shown["backtests"][1]
    assert (second["id"], second["status"], second["error"]) == (running, "running", None)
    assert (second["equity"], second["drawdown"], second["buy_and_hold"]) == (None, None, None)
    assert second["setup"]["model"] == "raw-candles.keras"
    assert all(
        row["values"][1] is None and row["marks"] == [None, None] for row in shown["metrics"]
    )
    assert shown["differs"] == []
    assert shown["backtests"][0]["equity"][-1] is not None


def test_an_unknown_unpublished_or_test_backtest_is_named(ada, bob, worker) -> None:
    own = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    ada.ended(own)
    unpublished = bob.start(body(bob.runnable_model())).json()["backtest"]["id"]
    bob.ended(unpublished)
    seeded = ada.backtest(9)

    hidden = compare(ada, own, unpublished)
    unknown = compare(ada, "bt_zzzzzzzzzz", own)
    test_data = compare(ada, own, seeded)

    assert (hidden.status_code, hidden.json()["type"]) == (404, PROBLEMS + "not-found")
    assert hidden.json()["id"] == unpublished
    assert unpublished in hidden.json()["detail"]
    assert (unknown.status_code, unknown.json()["id"]) == (404, "bt_zzzzzzzzzz")
    assert (test_data.status_code, test_data.json()["type"]) == (409, PROBLEMS + "no-document")
    assert test_data.json()["id"] == seeded


@pytest.mark.parametrize("ids", [["bt_a"], [f"bt_{n}" for n in range(21)], ["bt_a", "bt_a"]])
def test_two_to_twenty_different_backtests(ada, ids: list[str]) -> None:
    response = compare(ada, *ids)

    assert (response.status_code, response.json()["type"]) == (422, PROBLEMS + "validation")
