"""Running backtests over HTTP against a real Postgres and the real job queue (conftest.py):
what ``POST /api/v1/backtests`` checks and stores, the run to its results through a worker
(the fake one, and once the real one with Keras), what fails it, the result document and its
download, and what the list and the Dashboard show of a run."""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from candlestack.core import create_redis
from candlestack.data import PeriodOutOfRange, SourceUnavailable, WarmUpOutOfRange
from candlestack.engine import ENGINE_VERSION
from candlestack.jobs import JobClient, JobFiles, JobQueue
from candlestack.main import create_app

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
BH = {"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}
KERAS = Path(__file__).parents[2] / "fixtures" / "keras"


def body(model_id: str, **groups: Any) -> dict[str, Any]:
    """A new backtest of the model on stock:AAPL over five days."""
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "decision": {"rule": {"name": "threshold", "limit": 0.1}},
        "execution": {"fill": "next_open", "stop_loss": 0.05, "fee_bps": 5.0},
        "benchmarks": [BH],
        **groups,
    }


def soon(condition: Callable[[], object], timeout: float = 10) -> None:
    """Waits until ``condition`` holds."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "it did not happen in time"
        time.sleep(0.02)


def manifest(**inputs: Any) -> dict[str, Any]:
    """The raw-candles test model's manifest with other ``input`` fields."""
    changed = json.loads((KERAS / "raw_candles.manifest.json").read_text())
    changed["input"].update(inputs)
    return changed


# --- A run to its results -------------------------------------------------------------------


@pytest.mark.usefixtures("worker")
def test_a_backtest_runs_and_its_document_has_the_results(ada, market) -> None:
    model_id = ada.runnable_model()

    started = ada.start(body(model_id))

    assert started.status_code == 201
    assert started.headers["cache-control"] == "no-store"
    running = started.json()
    backtest_id = running["backtest"]["id"]
    assert started.headers["location"] == f"/api/v1/backtests/{backtest_id}"
    assert running["backtest"]["number"] == 1
    assert running["backtest"]["status"] == "running"
    assert running["results"] is None
    assert running["data"]["loaded"] is None
    assert running["data"]["warm_up"] == {"candles": 24, "kind": "exact", "dropped": None}

    document = ada.ended(backtest_id)

    assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
    assert document["version"] == 1
    assert document["software"] == {
        "engine_version": ENGINE_VERSION,
        "app_version": "dev",
        "runner": {"format": "keras", "keras": "3.15.1", "backend": "jax 0.7.2"},
    }
    assert document["model"]["id"] == model_id
    assert document["model"]["name"] == "raw-candles.keras"
    assert document["model"]["sha256"] == "0" * 64
    assert document["model"]["manifest"]["input"]["window"] == {"length": 25}  # as given
    data = document["data"]
    assert data["instrument"] == {
        "id": "stock:AAPL",
        "market": "stock",
        "symbol": "AAPL",
        "name": "Apple Inc.",
        "exchange": "NASDAQ",
        "source": "alpaca",
        "feed": "sip",
        "currency": "USD",
    }
    assert (data["timeframe"], data["timeframe_seconds"], data["session"]) == (
        "30m",
        1800,
        "regular",
    )
    assert data["period"] == {"start": START, "end": END}
    # The warm-up of 24 candles is loaded before the period, and the model drops just those.
    assert data["loaded"]["start"] == START - 24 * 1800
    assert data["loaded"]["count"] == 24 + 240
    assert data["loaded"]["fingerprint"].startswith("sha256:")
    assert data["changed"] is False
    assert data["warm_up"] == {"candles": 24, "kind": "exact", "dropped": 24}
    assert data["rows"] == {"first_ts": START, "last_ts": END - 1800, "count": 240}
    assert data["predictions"]["count"] == 240
    assert data["align"] == {"predictions_without_candle": 0, "candles_without_prediction": 0}
    assert document["decision"]["rule"] == {"name": "threshold", "limit": 0.1}
    assert document["execution"]["stop_loss"] == 0.05
    assert document["benchmarks"] == [BH]
    assert document["metrics"] == {"periods_per_year": 252, "risk_free_rate": 0.0}
    results = document["results"]
    assert results["stats"]["num_trades"] > 0
    assert results["stats"]["max_drawdown"] <= 0
    assert results["benchmarks"]["bh"]["total_return"] == results["stats"]["buy_and_hold_return"]
    assert (results["series"]["resolution"], results["series"]["count"]) == ("candle", 240)
    assert results["trades"]["complete"] is True


@pytest.mark.usefixtures("worker")
def test_the_list_and_the_dashboard_show_a_finished_run(ada) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]
    stats = ada.ended(backtest_id)["results"]["stats"]

    (listed,) = ada.backtests().json()["backtests"]
    dashboard = ada.dashboard().json()
    models = ada.request("GET", "/api/v1/models").json()["models"]

    assert listed["status"] == "finished"
    assert listed["instruments"] == [{"market": "stock", "symbol": "AAPL"}]
    assert listed["representation"] == "windows"
    assert listed["setup"] == {
        "timeframe": "30m",
        "period_start": "2024-01-11",
        "period_end": "2024-01-15",
        "direction": "both",
        "threshold": 0.1,
        "stop_loss_pct": 5,
        "fees_pct": 0.05,
        "slippage_pct": 0.0,
        "benchmark": "bh",
    }
    for name in ("total_return", "buy_and_hold_return", "sharpe", "max_drawdown", "win_rate"):
        assert listed[name] == stats[name], name
    assert listed["num_trades"] == stats["num_trades"]
    assert listed["error"] is None
    assert listed["duration_s"] > 0
    equity = listed["equity"]
    assert len(equity["strategy"]) == len(equity["benchmark"]) == 60
    assert equity["strategy"][-1] == pytest.approx(stats["total_return"])
    assert [backtest["id"] for backtest in dashboard["last"]] == [backtest_id]
    assert models[0]["backtest_count"] == 1
    stored = ada.sql("SELECT engine_version, stats FROM backtests WHERE id = :id", id=backtest_id)
    assert stored[0][0] == ENGINE_VERSION
    assert stored[0][1] == stats


@pytest.mark.usefixtures("worker")
def test_the_download_has_every_row_and_names_the_file(ada) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]
    document = ada.ended(backtest_id)

    download = ada.download(backtest_id)

    assert download.status_code == 200
    assert download.headers["content-type"] == "application/json"
    assert download.headers["content-disposition"] == (
        'attachment; filename="candlestack-backtest-1.json"'
    )
    assert download.json() == document  # under 10,000 rows the light view is the whole


@pytest.mark.usefixtures("worker")
def test_a_crypto_backtest_annualizes_over_every_day(ada) -> None:
    model_id = ada.runnable_model(manifest(session="24/7"))
    bh = {**BH, "instrument": "crypto:BTCUSDT"}

    started = ada.start(body(model_id, data=period(instrument="crypto:BTCUSDT"), benchmarks=[bh]))
    document = ada.ended(started.json()["backtest"]["id"])

    assert document["backtest"]["status"] == "finished"
    assert document["metrics"]["periods_per_year"] == 365
    assert document["data"]["instrument"]["currency"] == "USDT"


@pytest.mark.usefixtures("worker")
def test_the_document_says_when_the_candles_changed_since(ada, market) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]
    before = ada.ended(backtest_id)

    market.scale = 2.0  # a split adjusted every price
    after = ada.document(backtest_id).json()

    assert before["data"]["changed"] is False
    assert after["data"]["changed"] is True
    assert after["data"]["loaded"] == before["data"]["loaded"]  # what the run loaded
    assert after["results"]["series"]["columns"]["close"][0] == pytest.approx(
        2 * before["results"]["series"]["columns"]["close"][0]
    )


@pytest.mark.usefixtures("real_worker")
def test_a_run_with_the_real_worker(ada) -> None:
    model_id = ada.runnable_model(file=KERAS / "raw_candles.keras")

    started = ada.start(body(model_id))
    document = ada.ended(started.json()["backtest"]["id"], timeout=120)

    assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
    assert document["software"]["runner"]["keras"] == "3.15.1"
    assert document["data"]["warm_up"]["dropped"] == 24
    assert document["data"]["rows"]["count"] == 240
    assert document["data"]["predictions"]["count"] == 240


# --- What fails a run -----------------------------------------------------------------------


def test_a_failed_job_fails_the_backtest_with_its_error(ada, worker) -> None:
    worker.failure = ("load", "Keras could not load the model file.")
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]

    document = ada.ended(backtest_id)
    download = ada.download(backtest_id)
    (listed,) = ada.backtests().json()["backtests"]

    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == "Keras could not load the model file."
    assert document["backtest"]["finished_at"] is not None
    assert document["results"] is None
    assert download.status_code == 409
    assert download.json()["type"] == PROBLEMS + "backtest-not-finished"
    assert "failed" in download.json()["detail"]
    assert (listed["status"], listed["total_return"], listed["equity"]) == ("failed", None, None)


@pytest.mark.usefixtures("worker")
def test_candles_the_source_refuses_fail_the_backtest_with_its_detail(ada, market) -> None:
    market.refuse_candles = SourceUnavailable("alpaca", "Alpaca is not answering. Try later.")
    model_id = ada.runnable_model()

    document = ada.ended(ada.start(body(model_id)).json()["backtest"]["id"])

    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == "Alpaca is not answering. Try later."


@pytest.mark.usefixtures("market")
def test_a_worker_that_does_not_answer_fails_the_backtest(ada, app) -> None:
    app.state.backtest_runner._wait = 1
    model_id = ada.runnable_model()

    document = ada.ended(ada.start(body(model_id)).json()["backtest"]["id"])

    assert document["backtest"]["status"] == "failed"
    assert "did not answer in time" in document["backtest"]["error"]


@pytest.mark.usefixtures("market")
def test_a_worker_out_of_reach_fails_the_backtest(ada, app, monkeypatch) -> None:
    async def unreachable(**_job: Any) -> Any:
        raise RedisConnectionError("Connection refused")

    # The queue answers the start's ping and is gone when the run hands it the job.
    monkeypatch.setattr(app.state.backtest_runner._jobs, "predict", unreachable)
    model_id = ada.runnable_model()

    document = ada.ended(ada.start(body(model_id)).json()["backtest"]["id"])

    assert document["backtest"]["status"] == "failed"
    assert "cannot be reached" in document["backtest"]["error"]


def test_a_model_deleted_while_it_runs_fails_the_backtest_with_why(ada, worker) -> None:
    worker.gate.clear()
    # The worker finds no file: the deletion took it along.
    worker.failure = ("model-missing", "The model's file is gone. Upload the model again.")
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]
    soon(lambda: worker.jobs)

    deleted = ada.request("DELETE", f"/api/v1/models/{model_id}")
    worker.gate.set()
    document = ada.ended(backtest_id)

    assert deleted.status_code == 204
    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == (
        "The model was deleted before the backtest could run it."
    )
    assert document["model"]["id"] is None


@pytest.mark.usefixtures("market")
def test_a_run_the_server_stopped_fails_and_a_new_start_fails_those_left(
    ada, app, client, settings
) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers

    client.portal.call(app.state.backtest_runner.close)
    stopped = ada.document(backtest_id).json()["backtest"]
    ada.sql("UPDATE backtests SET status = 'running', error = NULL WHERE id = :id", id=backtest_id)
    with TestClient(create_app(settings)):  # the start of another process
        pass
    recovered = ada.document(backtest_id).json()["backtest"]

    assert stopped["status"] == "failed"
    assert "restarted" in stopped["error"]
    assert recovered["status"] == "failed"
    assert "restarted" in recovered["error"]


# --- What starting one checks -----------------------------------------------------------------


@pytest.mark.usefixtures("market")
def test_one_backtest_runs_at_a_time_per_user(ada, bob) -> None:
    first = ada.start(body(ada.runnable_model()))  # no worker answers: it keeps running

    second = ada.start(body(ada.runnable_model(name="other.keras")))
    other_user = bob.start(body(bob.runnable_model()))

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["type"] == PROBLEMS + "backtest-running"
    assert other_user.status_code == 201


@pytest.mark.usefixtures("market")
def test_two_starts_at_once_run_one_backtest(ada, market) -> None:
    # Both pass the first check before either has stored its backtest.
    market.slow = 0.5
    model_id = ada.runnable_model()

    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: ada.start(body(model_id)), range(2)))

    assert sorted(answer.status_code for answer in answers) == [201, 409]
    assert ada.sql("SELECT count(*) FROM backtests") == [(1,)]


@pytest.mark.usefixtures("market")
def test_a_model_deleted_while_the_start_checks_it_is_not_found(ada, market) -> None:
    market.slow = 0.5
    model_id = ada.runnable_model()

    with ThreadPoolExecutor(1) as pool:
        started = pool.submit(ada.start, body(model_id))
        soon(lambda: market.held)  # the start is asking the data module
        deleted = ada.request("DELETE", f"/api/v1/models/{model_id}")
        answer = started.result()

    assert deleted.status_code == 204
    assert (answer.status_code, answer.json()["type"]) == (404, PROBLEMS + "not-found")
    assert ada.sql("SELECT count(*) FROM backtests") == [(0,)]


@pytest.mark.usefixtures("worker")
def test_no_connection_waits_on_the_data_module(ada, market) -> None:
    """The start and the document end their reads before they ask for market data."""
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    at_start = market.held[0]
    ada.ended(backtest_id)
    market.held.clear()

    ada.document(backtest_id)

    assert at_start == 0
    assert market.held == [0]


@pytest.mark.usefixtures("market")
def test_the_backtest_routes_slide_the_session_on(ada) -> None:
    ada.sql("UPDATE sessions SET last_seen_at = now() - interval '2 days'")
    started = ada.start(body(ada.runnable_model()))
    ada.sql("UPDATE sessions SET last_seen_at = now() - interval '2 days'")
    document = ada.document(started.json()["backtest"]["id"])

    assert "Max-Age=2592000" in started.headers["set-cookie"]
    assert "Max-Age=2592000" in document.headers["set-cookie"]


@pytest.mark.usefixtures("market")
def test_the_model_must_be_the_users_and_have_a_manifest(ada, bob) -> None:
    bobs = bob.runnable_model()
    without_manifest = ada.model("lstm.keras")

    unknown = ada.start(body("mdl_zzzzzzzzzz"))
    others = ada.start(body(bobs))
    needs_setup = ada.start(body(without_manifest))

    assert (unknown.status_code, unknown.json()["type"]) == (404, PROBLEMS + "not-found")
    assert "no model" in unknown.json()["detail"]
    assert others.status_code == 404
    assert (needs_setup.status_code, needs_setup.json()["type"]) == (
        409,
        PROBLEMS + "model-needs-setup",
    )
    assert ada.backtests().json()["total"] == 0


@pytest.mark.usefixtures("market")
def test_the_model_must_fit_the_instrument(ada) -> None:
    stock_model = ada.runnable_model(name="stock.keras")
    on_crypto = ada.start(
        body(stock_model, data=period(instrument="crypto:BTCUSDT"), benchmarks=[])
    )

    assert on_crypto.status_code == 422
    assert on_crypto.json()["type"] == PROBLEMS + "instrument-not-for-model"
    assert "stocks" in on_crypto.json()["detail"]


def period(start: int = START, end: int = END, instrument: str = "stock:AAPL") -> dict[str, Any]:
    return {"instrument": {"id": instrument}, "period": {"start": start, "end": end}}


@pytest.mark.usefixtures("market")
@pytest.mark.parametrize(
    ("groups", "slug", "status"),
    [
        ({"benchmarks": [{**BH, "instrument": "stock:MSFT"}]}, "validation", 422),
        ({"benchmarks": [BH, BH]}, "validation", 422),
        ({"benchmarks": [{**BH, "kind": "spy"}]}, "validation", 422),
        ({"execution": {"stop_loss": 1.5}}, "validation", 422),
        (
            {"decision": {"rule": {"name": "hysteresis", "enter": 0.1, "exit": 0.2}}},
            "validation",
            422,
        ),
        ({"data": period(instrument="stock")}, "validation", 422),
        ({"data": period(instrument="stock:NOPE"), "benchmarks": []}, "instrument-not-found", 404),
        ({"data": period(END, START)}, "validation", 422),
    ],
)
def test_a_setup_that_does_not_hold_together_is_refused(
    ada, groups: dict[str, Any], slug: str, status: int
) -> None:
    response = ada.start(body(ada.runnable_model(), **groups))

    assert response.status_code == status, response.json()
    assert response.json()["type"] == PROBLEMS + slug


@pytest.mark.parametrize(
    ("error", "slug"),
    [
        (WarmUpOutOfRange("stock:AAPL", "30m", 24, START + DAY), "warm-up-out-of-range"),
        (
            PeriodOutOfRange("stock:AAPL", "30m", START, END, start=START - DAY),
            "period-out-of-range",
        ),
        (SourceUnavailable("alpaca", "Alpaca is not answering."), "source-unavailable"),
    ],
)
def test_the_period_must_fit_the_data(ada, market, error: Exception, slug: str) -> None:
    market.refuse = error

    response = ada.start(body(ada.runnable_model()))

    assert response.json()["type"] == PROBLEMS + slug
    assert ada.backtests().json()["total"] == 0


@pytest.mark.usefixtures("market")
def test_without_the_workers_queue_no_backtest_starts(ada, app, settings) -> None:
    app.state.backtest_runner._jobs = JobClient(
        JobQueue(create_redis("redis://127.0.0.1:1/0")), JobFiles(settings.job_files_dir)
    )

    response = ada.start(body(ada.runnable_model()))

    assert response.status_code == 503
    assert response.json()["type"] == PROBLEMS + "backtests-unavailable"
    assert ada.backtests().json()["total"] == 0


@pytest.mark.usefixtures("market")
def test_starting_needs_the_apps_own_origin(ada) -> None:
    response = ada.start(body(ada.runnable_model()), headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


# --- The document of other backtests ----------------------------------------------------------


@pytest.mark.usefixtures("market")
def test_a_running_backtest_has_no_download_and_is_not_on_the_dashboard_yet(ada) -> None:
    ada.backtest(1)  # finished, made straight in the table
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]

    download = ada.download(backtest_id)
    dashboard = ada.dashboard().json()

    assert download.status_code == 409
    assert download.json()["type"] == PROBLEMS + "backtest-not-finished"
    assert "still running" in download.json()["detail"]
    assert [backtest["number"] for backtest in dashboard["last"]] == [1]
    assert dashboard["counts"]["all"] == 2


@pytest.mark.usefixtures("market")
def test_test_backtests_have_no_document_and_others_are_not_found(ada, bob) -> None:
    seeded = ada.backtest(1)
    bobs = bob.backtest(1)

    document = ada.document(seeded)
    download = ada.download(seeded)
    not_mine = ada.document(bobs)
    unknown = ada.download("bt_zzzzzzzzzz")

    assert (document.status_code, document.json()["type"]) == (409, PROBLEMS + "no-document")
    assert download.json()["type"] == PROBLEMS + "no-document"
    assert (not_mine.status_code, not_mine.json()["type"]) == (404, PROBLEMS + "not-found")
    assert unknown.status_code == 404


@pytest.mark.usefixtures("worker")
def test_a_backtest_whose_model_was_deleted_keeps_its_document(ada) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]
    ada.ended(backtest_id)

    ada.request("DELETE", f"/api/v1/models/{model_id}")
    document = ada.document(backtest_id).json()

    assert document["model"]["id"] is None
    assert document["model"]["name"] == "raw-candles.keras"
    assert document["results"] is not None


@pytest.mark.usefixtures("market")
def test_the_document_times_are_utc(ada) -> None:
    started = ada.start(body(ada.runnable_model())).json()

    created = datetime.fromisoformat(started["backtest"]["created_at"])

    assert started["backtest"]["created_at"].endswith("Z")
    assert abs((datetime.now(UTC) - created).total_seconds()) < 60
