"""Sweeps over HTTP against a real Postgres and the real job queue (conftest.py): the dry run,
a sweep's backtests in the queue as ordinary backtests with its ID, its progress, the ranking
once all ended, and what refuses a sweep before anything is stored."""

from typing import Any

import pytest

from candlestack.data import PeriodOutOfRange

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
BH = {"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}
SWEEPS = "/api/v1/sweeps"


def base(model_id: str) -> dict[str, Any]:
    """A new backtest of the model on stock:AAPL over five days."""
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "decision": {"rule": {"name": "threshold", "limit": 0.1}},
        "execution": {"fill": "next_open", "fee_bps": 5.0},
        "benchmarks": [BH],
    }


def threshold(limit: float) -> dict[str, Any]:
    return {"name": "threshold", "limit": limit}


def start(user: Any, body: dict[str, Any]) -> Any:
    return user.request("POST", SWEEPS, json=body)


def sweep_of(user: Any, sweep_id: str, **params: Any) -> Any:
    return user.request("GET", f"{SWEEPS}/{sweep_id}", params=params)


def problem(response: Any, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["type"] == PROBLEMS + slug
    return body


@pytest.mark.usefixtures("market")
def test_a_dry_run_counts_and_checks_every_setup_and_stores_nothing(ada) -> None:
    first, second = ada.runnable_model(), ada.runnable_model(name="other.keras")
    body = {
        "base": base(first),
        "vary": {"models": [first, second], "rules": [threshold(0.1), threshold(0.2)]},
        "validate_only": True,
    }

    planned = start(ada, body)

    assert planned.status_code == 200
    assert planned.headers["cache-control"] == "no-store"
    plan = planned.json()
    assert (plan["count"], plan["limit"], plan["queue_room"]) == (4, 20, 20)
    assert [(setup["model"], setup["rule"]["limit"]) for setup in plan["setups"]] == [
        (first, 0.1),
        (first, 0.2),
        (second, 0.1),
        (second, 0.2),
    ]
    assert plan["setups"][2] == {
        "model": second,
        "model_name": "other.keras",
        "representation": "windows",
        "timeframe": "30m",
        "instrument": "stock:AAPL",
        "period": {"start": START, "end": END},
        "rule": threshold(0.1),
    }
    assert ada.sql("SELECT count(*) FROM backtests") == [(0,)]
    assert ada.sql("SELECT count(*) FROM sweeps") == [(0,)]


def test_a_sweep_queues_ordinary_backtests_and_ranks_them_once_all_ended(ada, bob, worker) -> None:
    worker.gate.clear()  # the first run holds the worker
    model_id = ada.runnable_model()
    limits = [0.05, 0.1, 0.3]
    body = {
        "base": base(model_id),
        "vary": {"rules": [threshold(limit) for limit in limits]},
        "metric": "total_return",
    }

    started = start(ada, body)

    assert started.status_code == 201
    sweep = started.json()
    assert started.headers["location"] == f"{SWEEPS}/{sweep['id']}"
    assert sweep["id"].startswith("sw_")
    assert (sweep["count"], sweep["metric"], sweep["done"]) == (3, "total_return", False)
    assert sweep["progress"] == {"queued": 2, "running": 1, "finished": 0, "failed": 0}
    assert sweep["ranking"] is None
    backtests = sweep["backtests"]
    assert [(b["number"], b["status"], b["queue_position"]) for b in backtests] == [
        (1, "running", None),
        (2, "queued", 1),
        (3, "queued", 2),
    ]
    assert [b["setup"]["rule"]["limit"] for b in backtests] == limits
    ids = [b["id"] for b in backtests]
    # Ordinary backtests with the sweep's ID: in the list, with their documents.
    listed = ada.backtests().json()["backtests"]
    assert sorted(b["id"] for b in listed) == sorted(ids)
    assert {b["sweep_id"] for b in listed} == {sweep["id"]}
    assert ada.document(ids[1]).json()["backtest"]["sweep_id"] == sweep["id"]

    worker.gate.set()
    ended = [ada.ended(backtest_id) for backtest_id in ids]
    done = sweep_of(ada, sweep["id"])

    assert done.status_code == 200
    assert done.headers["cache-control"] == "no-store"
    finished = done.json()
    assert finished["done"] is True
    assert finished["progress"] == {"queued": 0, "running": 0, "finished": 3, "failed": 0}
    returns = {doc["backtest"]["id"]: doc["results"]["stats"]["total_return"] for doc in ended}
    ranking = finished["ranking"]
    assert (ranking["metric"], ranking["label"], ranking["better"]) == (
        "total_return",
        "Total return",
        "higher",
    )
    ranked = ranking["backtests"]
    assert [each["id"] for each in ranked] == sorted(ids, key=returns.__getitem__, reverse=True)
    assert [each["value"] for each in ranked] == sorted(returns.values(), reverse=True)
    assert ranked[0]["rank"] == 1
    # The ranking marks as the comparison does.
    compared = ada.request("GET", "/api/v1/compare", params={"ids": ",".join(ids)}).json()
    row = next(metric for metric in compared["metrics"] if metric["key"] == "total_return")
    marks = dict(zip(ids, row["marks"], strict=True))
    assert [each["mark"] for each in ranked] == [marks[each["id"]] for each in ranked]
    # Another statistic on request; the sweep keeps its own.
    by_fees = sweep_of(ada, sweep["id"], metric="fees_paid").json()
    assert (by_fees["metric"], by_fees["ranking"]["better"]) == ("fees_paid", "lower")
    assert sweep_of(ada, sweep["id"]).json()["metric"] == "total_return"
    # A sweep's backtest is publishable as any other.
    assert ada.publish(ids[0]).json()["public"] is True
    # Others read it without the sweep, which only its author can open.
    assert bob.document(ids[0]).json()["backtest"]["sweep_id"] is None
    assert ada.document(ids[0]).json()["backtest"]["sweep_id"] == sweep["id"]


@pytest.mark.usefixtures("worker")
def test_a_failed_backtest_ends_the_sweep_and_is_ranked_last(ada, worker) -> None:
    model_id = ada.runnable_model()
    worker.failure = ("load", "Keras could not load the model file.")
    body = {"base": base(model_id), "vary": {"rules": [threshold(0.1), threshold(0.2)]}}

    sweep = start(ada, body).json()
    for each in sweep["backtests"]:
        ada.ended(each["id"])
    done = sweep_of(ada, sweep["id"]).json()

    assert done["done"] is True
    assert done["progress"]["failed"] == 2
    assert [b["error"] is not None for b in done["backtests"]] == [True, True]
    assert [each["rank"] for each in done["ranking"]["backtests"]] == [None, None]
    assert done["metric"] == "sharpe"


@pytest.mark.usefixtures("market")
def test_more_than_twenty_backtests_are_refused(ada) -> None:
    model_id = ada.runnable_model()
    body = {
        "base": base(model_id),
        "vary": {
            "periods": [{"start": START, "end": END + n * DAY} for n in range(3)],
            "rules": [threshold(n / 10) for n in range(7)],
        },
    }

    refused = problem(start(ada, body), 422, "validation")

    assert (refused["count"], refused["limit"]) == (21, 20)
    assert ada.sql("SELECT count(*) FROM backtests") == [(0,)]


@pytest.mark.usefixtures("market")
def test_a_sweep_needs_room_for_all_its_backtests_in_the_queue(ada) -> None:
    model_id = ada.runnable_model()
    # No worker answers: the first keeps running, the rest wait.
    for _ in range(17):
        assert ada.start(base(model_id) | {"benchmarks": []}).status_code == 201
    body = {"base": base(model_id), "vary": {"rules": [threshold(n / 10) for n in range(5)]}}

    refused = problem(start(ada, body), 409, "queue-full")
    planned = start(ada, body | {"validate_only": True})

    assert (refused["count"], refused["room"], refused["limit"]) == (5, 4, 20)
    assert "room for 4" in refused["detail"]
    assert planned.status_code == 409
    assert ada.sql("SELECT count(*) FROM backtests") == [(17,)]


@pytest.mark.usefixtures("market")
def test_a_setup_that_fails_a_check_names_itself_and_nothing_starts(ada, market) -> None:
    model_id = ada.runnable_model()
    body = {"base": base(model_id), "vary": {"instruments": ["stock:AAPL", "stock:NOPE"]}}
    crypto = ada.runnable_model(name="crypto.keras")
    ada.sql(
        "UPDATE models SET manifest = jsonb_set(manifest, '{input,session}', '\"24/7\"') "
        "WHERE id = :id",
        id=crypto,
    )

    missing = problem(start(ada, body), 404, "instrument-not-found")
    other_market = problem(
        start(ada, {"base": base(model_id), "vary": {"models": [model_id, crypto]}}),
        422,
        "instrument-not-for-model",
    )
    unknown = problem(
        start(ada, {"base": base(model_id), "vary": {"models": [model_id, "mdl_nothere00"]}}),
        404,
        "not-found",
    )
    market.refuse_of["AAPL"] = PeriodOutOfRange("stock:AAPL", "30m", START, END, start=START - DAY)
    early = problem(
        start(ada, {"base": base(model_id), "vary": {"rules": [threshold(0.1), threshold(0.2)]}}),
        422,
        "period-out-of-range",
    )

    assert missing["setup"] == 2
    assert missing["detail"].startswith("Setup 2 of the sweep: ")
    assert other_market["setup"] == 2
    assert unknown["setup"] == 2
    assert early["setup"] == 1
    assert ada.sql("SELECT count(*) FROM backtests") == [(0,)]
    assert ada.sql("SELECT count(*) FROM sweeps") == [(0,)]


@pytest.mark.usefixtures("market")
def test_another_users_sweep_is_not_found(ada, bob) -> None:
    model_id = ada.runnable_model()
    body = {"base": base(model_id), "vary": {"rules": [threshold(0.1), threshold(0.2)]}}
    sweep_id = start(ada, body).json()["id"]

    problem(sweep_of(bob, sweep_id), 404, "not-found")
    problem(sweep_of(ada, "sw_nothere000"), 404, "not-found")
    assert sweep_of(ada, sweep_id).status_code == 200
