"""Running backtests over HTTP against a real Postgres and the real job queue (conftest.py):
what ``POST /api/v1/backtests`` checks and stores, the run to its results through a worker
(the fake one, and once the real one with Keras), what fails it, the queue of a user's
backtests, the result document and its download, and what the list and the Dashboard show of
a run."""

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from candlestack.core import create_redis
from candlestack.data import (
    DataIntegrityError,
    PeriodOutOfRange,
    SourceUnavailable,
    WarmUpOutOfRange,
)
from candlestack.engine import ENGINE_VERSION
from candlestack.experiments import BacktestIn, BacktestRunner, backtest_runs
from candlestack.experiments import names as names_module
from candlestack.experiments import runner as runner_module
from candlestack.experiments import store as store_module
from candlestack.experiments.assembly import NothingToTest
from candlestack.experiments.names import ADJECTIVES, NOUNS
from candlestack.jobs import QUEUE, WORKER_STOPPED, JobClient, JobFiles, JobQueue
from candlestack.main import create_app

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
BH = {"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}
SPY = {"id": "spy", "kind": "buy_and_hold", "instrument": "stock:SPY"}
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


def states(document: dict[str, Any]) -> list[str]:
    """The states of the backtest's steps, in their order."""
    steps = document["backtest"]["steps"]
    assert [step["id"] for step in steps] == ["candles", "queue", "model", "trades"]
    return [step["state"] for step in steps]


def step(document: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(step for step in document["backtest"]["steps"] if step["id"] == step_id)


def at(document: dict[str, Any], field: str) -> datetime:
    """A time of the document's ``backtest``."""
    return datetime.fromisoformat(document["backtest"][field])


def manifest(**inputs: Any) -> dict[str, Any]:
    """The raw-candles test model's manifest with other ``input`` fields."""
    changed = json.loads((KERAS / "raw_candles.manifest.json").read_text())
    changed["input"].update(inputs)
    return changed


# --- A run to its results -------------------------------------------------------------------


def test_a_backtest_runs_and_its_document_has_the_results(ada, market, worker) -> None:
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
    assert states(running) == ["pending"] * 4

    document = ada.ended(backtest_id)

    assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
    assert states(document) == ["done"] * 4
    for done in document["backtest"]["steps"]:
        assert done["started_at"] is not None, done["id"]
        assert done["duration_s"] >= 0, done["id"]
    assert step(document, "candles")["candles"] == 24 + 240
    model = step(document, "model")
    assert (model["load_s"], model["input_s"], model["predict_s"]) == (1.2, 0.1, 0.5)
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
    # The manifest's notes for people are not copied into the backtest, nor sent to the worker.
    assert "about" not in document["model"]["manifest"]
    assert all("about" not in job.manifest for job in worker.jobs)
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
    # The light view is what the run stored: only the download compares the candles again.
    assert data["changed"] is None
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
    # The worker gets the stock candles with the mark of each session's last candle.
    assert worker.columns == [["ts", "open", "high", "low", "close", "volume", "session_last"]]
    # The run loads them in the background, waiting for the source's budget; the document is
    # what it stored, so it loads none.
    assert market.background == [True]


class NoData:
    """The data module of an app whose light view must not reach it: any use fails the test."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"The light view used the data module ({name}).")


@pytest.mark.usefixtures("worker")
def test_the_light_view_is_what_the_run_stored_and_loads_no_candles(ada, app: FastAPI) -> None:
    backtest_id = ada.start(body(ada.runnable_model(), benchmarks=[SPY])).json()["backtest"]["id"]
    ada.ended(backtest_id)
    download = ada.download(backtest_id).json()
    app.state.data_service = NoData()

    light = ada.document(backtest_id)
    summary = ada.request("GET", f"/api/v1/backtests/{backtest_id}", params={"view": "summary"})

    assert light.status_code == 200, light.text
    document = light.json()
    # Under 10,000 rows the light view is the whole document, as a build from the candles.
    assert download["data"]["changed"] is False
    assert document == {**download, "data": {**download["data"], "changed": None}}
    assert summary.status_code == 200, summary.text
    shown = summary.json()
    assert {**shown, "results": None} == {**document, "results": None}
    results = shown["results"]
    assert results["stats"] == document["results"]["stats"]
    assert results["benchmarks"] == document["results"]["benchmarks"]
    assert (results["series"]["count"], results["series"]["columns"]["equity"]) == (0, [])
    assert results["series"]["benchmarks"] == {"spy": []}
    trades = results["trades"]
    assert (trades["count"], trades["columns"]["open_ts"]) == (0, [])
    assert trades["total"] == document["results"]["trades"]["total"] > 0
    assert trades["complete"] is False


FORGET = (
    "UPDATE backtests SET series = NULL, trades = NULL, curves = NULL, "
    "report = report - 'benchmarks' WHERE id = :id"
)
KEPT = (
    "SELECT series IS NOT NULL, trades IS NOT NULL, curves IS NOT NULL, report -> 'benchmarks' "
    "FROM backtests WHERE id = :id"
)


@pytest.mark.usefixtures("worker")
def test_a_backtest_from_before_the_light_results_stores_them_on_its_first_read(
    ada, market, app: FastAPI
) -> None:
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    before = ada.ended(backtest_id)
    ada.sql(FORGET, id=backtest_id)
    loads = len(market.loads)

    first = ada.document(backtest_id).json()

    # Built from the candles once, as every read did before, and stored.
    assert len(market.loads) == loads + 1
    assert first == before
    returned = before["results"]["benchmarks"]["bh"]["total_return"]
    assert ada.sql(KEPT, id=backtest_id) == [(True, True, True, {"bh": pytest.approx(returned)})]
    app.state.data_service = NoData()
    assert ada.document(backtest_id).json() == before


@pytest.mark.usefixtures("worker")
def test_a_backtest_from_before_whose_build_counts_otherwise_stores_nothing(ada) -> None:
    """A build that gives other statistics than the run's (the engine changed since) is
    answered, never stored with the run's statistics."""
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    before = ada.ended(backtest_id)
    ada.sql(FORGET, id=backtest_id)
    ada.sql(
        "UPDATE backtests SET stats = jsonb_set(stats, '{num_trades}', '999') WHERE id = :id",
        id=backtest_id,
    )

    after = ada.document(backtest_id).json()

    assert after["results"]["stats"] == before["results"]["stats"]  # the build's
    assert ada.sql(KEPT, id=backtest_id) == [(False, False, False, None)]


@pytest.mark.usefixtures("worker")
def test_a_backtest_from_before_whose_candles_changed_stores_nothing(ada, market) -> None:
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    ada.ended(backtest_id)
    ada.sql(FORGET, id=backtest_id)
    market.scale = 2.0  # a split adjusted every price

    after = ada.document(backtest_id).json()

    assert after["data"]["changed"] is True
    assert ada.sql(KEPT, id=backtest_id) == [(False, False, False, None)]


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
    stored = ada.sql(
        "SELECT engine_version, stats, benchmark_return, input_shape FROM backtests WHERE id = :id",
        id=backtest_id,
    )
    assert stored[0][0] == ENGINE_VERSION
    assert stored[0][1] == stats
    # Buy and hold of the instrument is the benchmark, and the model reads 25 rows of 5.
    assert (stored[0][2], stored[0][3]) == (stats["buy_and_hold_return"], [25, 5])
    assert equity["benchmark"][-1] == pytest.approx(stats["buy_and_hold_return"])


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
    # Under 10,000 rows the light view is the whole; only the download compares the candles.
    assert download.json() == {**document, "data": {**document["data"], "changed": False}}


def test_a_crypto_backtest_annualizes_over_every_day(ada, worker) -> None:
    model_id = ada.runnable_model(manifest(session="24/7"))
    bh = {**BH, "instrument": "crypto:BTCUSDT"}

    started = ada.start(body(model_id, data=period(instrument="crypto:BTCUSDT"), benchmarks=[bh]))
    document = ada.ended(started.json()["backtest"]["id"])

    assert document["backtest"]["status"] == "finished"
    assert document["metrics"]["periods_per_year"] == 365
    assert document["data"]["instrument"]["currency"] == "USDT"
    # Around the clock there are no sessions to mark.
    assert worker.columns == [["ts", "open", "high", "low", "close", "volume"]]


@pytest.mark.usefixtures("worker")
def test_a_stock_backtest_compares_with_an_index_fund(ada, market) -> None:
    started = ada.start(body(ada.runnable_model(), benchmarks=[{**SPY, "instrument": "STOCK:spy"}]))
    backtest_id = started.json()["backtest"]["id"]
    document = ada.ended(backtest_id)
    (listed,) = ada.backtests().json()["backtests"]

    assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
    assert document["benchmarks"] == [SPY]  # as the catalog writes it
    assert step(document, "candles")["candles"] == 24 + 240  # the backtest's own
    results = document["results"]
    spy = results["series"]["benchmarks"]["spy"]
    assert len(spy) == results["series"]["count"] == 240
    assert spy[0] == pytest.approx(1.0)
    assert results["benchmarks"]["spy"]["total_return"] == pytest.approx(spy[-1] - 1)
    # The statistics and the list keep buy and hold of the backtest's own instrument.
    assert results["benchmarks"]["spy"]["total_return"] != pytest.approx(
        results["stats"]["buy_and_hold_return"]
    )
    assert listed["buy_and_hold_return"] == results["stats"]["buy_and_hold_return"]
    assert listed["setup"]["benchmark"] == "SPY"
    # What the run stores of the backtest's benchmark is the fund's: its return, and its
    # equity at the same times as the strategy's in the short series.
    stored = ada.sql("SELECT benchmark_return FROM backtests WHERE id = :id", id=backtest_id)
    assert stored == [(pytest.approx(results["benchmarks"]["spy"]["total_return"]),)]
    ts = np.array(results["series"]["columns"]["ts"])
    picked = np.searchsorted(ts, np.linspace(ts[0], ts[-1], 60), side="right") - 1
    assert listed["equity"]["benchmark"] == pytest.approx(list(np.array(spy)[picked] - 1))
    strategy = np.array(results["series"]["columns"]["equity"])[picked] - 1
    assert listed["equity"]["strategy"] == pytest.approx(list(strategy))
    # The run loads the fund's candles of the period in the background after its own; the
    # document is what it stored, and the download loads both again.
    assert ada.download(backtest_id).json()["results"] == results
    own = ("stock:AAPL", (START - 24 * 1800, END))
    fund = ("stock:SPY", (START, END))
    assert list(zip(market.instruments, market.loads, market.background, strict=True)) == [
        (*own, True),
        (*fund, True),
        (*own, False),
        (*fund, False),
    ]
    # Its errors are the download's, as those of the backtest's own candles.
    market.refuse_candles_of["SPY"] = SourceUnavailable("alpaca", "Alpaca is not answering.")
    refused = ada.download(backtest_id)
    assert (refused.status_code, refused.json()["type"]) == (503, PROBLEMS + "source-unavailable")


@pytest.mark.usefixtures("worker")
def test_the_download_says_when_the_candles_changed_since(ada, market) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]
    ada.ended(backtest_id)
    before = ada.download(backtest_id).json()

    market.scale = 2.0  # a split adjusted every price
    after = ada.download(backtest_id).json()
    light = ada.document(backtest_id).json()

    assert before["data"]["changed"] is False
    assert after["data"]["changed"] is True
    # The light view stays what the run stored.
    assert light["results"] == before["results"]
    assert after["data"]["loaded"] == before["data"]["loaded"]  # what the run loaded
    assert after["results"]["series"]["columns"]["close"][0] == pytest.approx(
        2 * before["results"]["series"]["columns"]["close"][0]
    )


def renko() -> dict[str, Any]:
    """The raw-candles test model's manifest on Renko triples (25 x 5, the volume replaced by
    each triple's direction): a warm-up of 267 candles, an estimate."""
    changed = manifest(
        representation={"id": "renko-triples@1", "params": {"atr_multiple": 0.6}},
        window={"length": 25, "last": "current"},
    )
    changed["input"]["features"][4] = {
        "name": "direction",
        "id": "renko-direction@1",
        "norm": {"id": "none@1"},
    }
    return changed


# A period after a market that stood still for three days: no brick formed there.
CALM_START = START + 20 * DAY
CALM_END = CALM_START + 5 * DAY


def test_two_representations_run_on_the_same_candles_of_the_period(ada, market, worker) -> None:
    market.calm = (CALM_START - 3 * DAY, CALM_START)
    worker.built = True
    data = period(CALM_START, CALM_END)
    windows = ada.start(body(ada.runnable_model(), data=data)).json()["backtest"]["id"]
    windows_document = ada.ended(windows)
    renko_model = ada.runnable_model(renko(), name="renko.keras")
    bricks = ada.start(body(renko_model, data=data)).json()["backtest"]["id"]
    bricks_document = ada.ended(bricks)

    for document in (windows_document, bricks_document):
        assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
        rows = document["data"]["rows"]
        assert rows == {"first_ts": CALM_START, "last_ts": CALM_END - 1800, "count": 240}
        assert document["data"]["align"]["candles_without_prediction"] == 0
    # Buy and hold of the same candles, whatever the model reads.
    held = windows_document["results"]["stats"]["buy_and_hold_return"]
    assert bricks_document["results"]["stats"]["buy_and_hold_return"] == held
    # The estimated warm-up of 267 candles began in the calm and made too few bricks before
    # the period, so the run loaded twice as many; the windows' exact one sufficed.
    assert windows_document["data"]["loaded"]["start"] == CALM_START - 24 * 1800
    bricks_data = bricks_document["data"]
    assert bricks_data["warm_up"]["candles"] == 267
    assert bricks_data["loaded"]["start"] == CALM_START - 2 * 267 * 1800
    assert step(bricks_document, "candles")["candles"] == 2 * 267 + 240
    # The document loads what the run loaded, so it shows the same results.
    assert market.loads[-1] == (CALM_START - 2 * 267 * 1800, CALM_END)


def test_a_warm_up_the_data_cannot_lengthen_starts_at_the_first_prediction(
    ada, market, worker, monkeypatch
) -> None:
    market.calm = (CALM_START - 3 * DAY, CALM_START)
    worker.built = True
    check = market.warm_up_start

    async def no_further(instrument, timeframe, start, end, candles):
        if candles > 267:
            raise WarmUpOutOfRange(instrument, timeframe, candles, None)
        return await check(instrument, timeframe, start, end, candles)

    monkeypatch.setattr(market, "warm_up_start", no_further)
    started = ada.start(body(ada.runnable_model(renko()), data=period(CALM_START, CALM_END)))
    document = ada.ended(started.json()["backtest"]["id"])

    assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
    data = document["data"]
    assert data["loaded"]["start"] == CALM_START - 267 * 1800
    assert data["rows"]["first_ts"] > CALM_START
    assert data["rows"]["count"] < 240


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


@pytest.mark.usefixtures("real_worker")
def test_a_run_of_an_onnx_model_with_the_real_worker(ada) -> None:
    onnx_file = Path(__file__).parents[2] / "fixtures" / "onnx" / "raw_candles.onnx"
    model_id = ada.runnable_model(file=onnx_file, name="raw-candles.onnx", framework="onnx")

    started = ada.start(body(model_id))
    document = ada.ended(started.json()["backtest"]["id"], timeout=120)

    assert document["backtest"]["status"] == "finished", document["backtest"]["error"]
    runner = document["software"]["runner"]
    assert (runner["format"], runner["keras"]) == ("onnx", None)
    assert runner["backend"].startswith("onnxruntime ")
    assert document["data"]["predictions"]["count"] == 240


@pytest.mark.usefixtures("worker")
def test_the_steps_show_how_far_the_candles_loaded(ada, market) -> None:
    market.chunks = 4
    market.load_gate.clear()  # the load waits for the source after half its chunks
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]

    def waiting() -> bool:
        return step(ada.document(backtest_id).json(), "candles")["waiting_until"] is not None

    soon(waiting)
    loading = ada.document(backtest_id).json()
    market.load_gate.set()
    document = ada.ended(backtest_id)

    assert states(loading) == ["running", "pending", "pending", "pending"]
    candles = step(loading, "candles")
    assert (candles["loaded"], candles["total"], candles["cached"]) == (2, 4, 2)
    assert candles["loaded_until"] is not None
    assert candles["duration_s"] is None
    goes_on = datetime.fromisoformat(candles["waiting_until"])
    assert 0 < (goes_on - datetime.now(UTC)).total_seconds() <= 60
    candles = step(document, "candles")
    assert (candles["loaded"], candles["total"], candles["cached"]) == (4, 4, 2)
    assert candles["loaded_until"] == "2024-01-15"  # the period's last day
    assert (candles["from_cache"], candles["waiting_until"]) == (False, None)


@pytest.mark.usefixtures("worker")
def test_a_run_from_before_the_steps_shows_them_from_its_timings(ada) -> None:
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    ada.ended(backtest_id)
    ada.sql("UPDATE backtests SET steps = NULL WHERE id = :id", id=backtest_id)

    document = ada.document(backtest_id).json()

    assert states(document) == ["done"] * 4
    assert all(done["started_at"] is None for done in document["backtest"]["steps"])
    assert step(document, "model")["load_s"] == 1.2
    assert step(document, "queue")["duration_s"] is not None


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
    # The worker took the job: the model's step failed, and the trades were not simulated.
    assert states(document) == ["done", "done", "failed", "pending"]
    assert step(document, "model")["duration_s"] is not None
    assert document["backtest"]["finished_at"] is not None
    assert document["results"] is None
    assert download.status_code == 409
    assert download.json()["type"] == PROBLEMS + "backtest-not-finished"
    assert "failed" in download.json()["detail"]
    assert (listed["status"], listed["total_return"], listed["equity"]) == ("failed", None, None)


@pytest.mark.usefixtures("worker")
def test_candles_the_source_refuses_fail_the_backtest_with_its_detail(ada, market) -> None:
    market.refuse_candles = SourceUnavailable(
        "alpaca", "Alpaca failed with HTTP 503. Try again in a minute."
    )
    model_id = ada.runnable_model()

    document = ada.ended(ada.start(body(model_id)).json()["backtest"]["id"])

    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == (
        "The market data source (Alpaca) is unavailable, so the candles could not be loaded. "
        "Alpaca failed with HTTP 503. Try again in a minute."
    )
    assert states(document) == ["failed", "pending", "pending", "pending"]
    assert step(document, "candles")["duration_s"] >= 0


@pytest.mark.usefixtures("worker")
def test_candles_that_fail_their_checks_fail_the_backtest_with_their_detail(ada, market) -> None:
    market.refuse_candles = DataIntegrityError("Alpaca bars cannot be read: bad", source="alpaca")
    model_id = ada.runnable_model()

    document = ada.ended(ada.start(body(model_id)).json()["backtest"]["id"])

    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == "Alpaca bars cannot be read: bad"


@pytest.mark.usefixtures("worker")
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            SourceUnavailable("alpaca", "Alpaca failed with HTTP 503. Try again in a minute."),
            (
                "The candles of the benchmark SPY could not be loaded. The market data source "
                "(Alpaca) is unavailable. Alpaca failed with HTTP 503. Try again in a minute."
            ),
        ),
        (
            DataIntegrityError("Alpaca bars cannot be read: bad", source="alpaca"),
            "The candles of the benchmark SPY could not be loaded. Alpaca bars cannot be read: bad",
        ),
    ],
)
def test_candles_of_the_index_fund_that_fail_fail_the_backtest_naming_it(
    ada, market, error: Exception, message: str
) -> None:
    market.refuse_candles_of["SPY"] = error

    document = ada.ended(
        ada.start(body(ada.runnable_model(), benchmarks=[SPY])).json()["backtest"]["id"]
    )

    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == message
    assert states(document) == ["failed", "pending", "pending", "pending"]


@pytest.mark.usefixtures("market")
def test_a_worker_that_does_not_answer_fails_the_backtest(ada, app) -> None:
    app.state.backtest_runner._queue_wait = 1
    model_id = ada.runnable_model()

    document = ada.ended(ada.start(body(model_id)).json()["backtest"]["id"])

    assert document["backtest"]["status"] == "failed"
    assert "did not answer in time" in document["backtest"]["error"]
    # No worker took the job.
    assert states(document) == ["done", "failed", "pending", "pending"]


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
    assert states(document) == ["done", "failed", "pending", "pending"]


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
    assert states(document) == ["done", "done", "failed", "pending"]


@pytest.mark.usefixtures("worker")
def test_a_run_without_predictions_in_its_period_fails_simulating_the_trades(
    ada, monkeypatch
) -> None:
    def nothing(*_args: Any, **_kwargs: Any) -> Any:
        raise NothingToTest("No candle of the period has a prediction.")

    monkeypatch.setattr(runner_module, "run", nothing)

    document = ada.ended(ada.start(body(ada.runnable_model())).json()["backtest"]["id"])

    assert document["backtest"]["error"] == "No candle of the period has a prediction."
    assert states(document) == ["done", "done", "done", "failed"]


@pytest.mark.usefixtures("market")
def test_a_run_the_server_stopped_fails_and_a_new_start_fails_those_left(
    ada, app, client, settings
) -> None:
    model_id = ada.runnable_model()
    backtest_id = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers

    soon(lambda: states(ada.document(backtest_id).json())[1] == "running")
    client.portal.call(app.state.backtest_runner.close)
    stopped = ada.document(backtest_id).json()["backtest"]
    ada.sql("UPDATE backtests SET status = 'running', error = NULL WHERE id = :id", id=backtest_id)
    with TestClient(create_app(settings)):  # the start of another process
        pass
    recovered = ada.document(backtest_id).json()["backtest"]

    assert stopped["status"] == "failed"
    assert "restarted" in stopped["error"]
    assert [s["state"] for s in stopped["steps"]] == ["done", "failed", "pending", "pending"]
    assert recovered["status"] == "failed"
    assert "restarted" in recovered["error"]


def test_a_start_of_the_server_fails_the_step_a_run_was_at(ada, worker, settings) -> None:
    worker.gate.clear()  # the worker takes the job and holds it
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    soon(lambda: states(ada.document(backtest_id).json())[2] == "running")

    with TestClient(create_app(settings)):  # the start of another process
        pass
    recovered = ada.document(backtest_id).json()
    worker.gate.set()

    assert recovered["backtest"]["status"] == "failed"
    assert "restarted" in recovered["backtest"]["error"]
    assert states(recovered) == ["done", "done", "failed", "pending"]
    assert step(recovered, "model")["duration_s"] >= 0


# --- What starting one checks -----------------------------------------------------------------


@pytest.mark.usefixtures("market")
def test_one_backtest_runs_at_a_time_per_user(ada, bob) -> None:
    first = ada.start(body(ada.runnable_model()))  # no worker answers: it keeps running

    second = ada.start(body(ada.runnable_model(name="other.keras")))
    other_user = bob.start(body(bob.runnable_model()))

    assert first.status_code == 201
    assert first.json()["backtest"]["status"] == "running"
    assert second.status_code == 201
    assert second.json()["backtest"]["status"] == "queued"
    assert other_user.json()["backtest"]["status"] == "running"


@pytest.mark.usefixtures("market")
def test_two_starts_at_once_run_one_backtest(ada, market) -> None:
    # Both pass the first check before either has stored its backtest.
    market.slow = 0.5
    model_id = ada.runnable_model()

    with ThreadPoolExecutor(2) as pool:
        answers = list(pool.map(lambda _: ada.start(body(model_id)), range(2)))

    assert [answer.status_code for answer in answers] == [201, 201]
    assert sorted(answer.json()["backtest"]["status"] for answer in answers) == [
        "queued",
        "running",
    ]
    assert ada.sql("SELECT number, status FROM backtests ORDER BY number") == [
        (1, "running"),
        (2, "queued"),
    ]


# --- The queue ---------------------------------------------------------------------------------


def test_a_start_while_one_runs_waits_and_runs_after_it_in_order(ada, worker) -> None:
    worker.gate.clear()  # the first run holds the worker
    model_id = ada.runnable_model()

    first = ada.start(body(model_id)).json()
    second = ada.start(body(model_id))
    third = ada.start(body(model_id)).json()

    queued = second.json()
    ids = [document["backtest"]["id"] for document in (first, queued, third)]
    assert (first["backtest"]["status"], first["backtest"]["queue_position"]) == ("running", None)
    assert second.status_code == 201
    assert second.headers["location"] == f"/api/v1/backtests/{ids[1]}"
    assert queued["backtest"]["status"] == "queued"
    assert queued["backtest"]["queue_position"] == 1
    assert queued["backtest"]["started_at"] is None
    assert states(queued) == ["pending"] * 4
    assert third["backtest"]["queue_position"] == 2
    listed = ada.backtests().json()["backtests"]
    assert [(b["number"], b["status"], b["queue_position"]) for b in listed] == [
        (3, "queued", 2),
        (2, "queued", 1),
        (1, "running", None),
    ]
    assert ada.document(ids[2]).json()["backtest"]["queue_position"] == 2
    download = ada.download(ids[1])
    assert download.status_code == 409
    assert "waiting in the queue" in download.json()["detail"]
    # Nothing ended yet.
    assert ada.dashboard().json()["last"] == []

    soon(lambda: worker.jobs)
    worker.gate.set()
    ended = [ada.ended(backtest_id) for backtest_id in ids]

    assert [document["backtest"]["status"] for document in ended] == ["finished"] * 3
    assert [document["backtest"]["queue_position"] for document in ended] == [None] * 3
    assert len(worker.jobs) == 3
    # Each began once the one before it had ended, and its time leaves out its wait.
    for before, after in pairwise(ended):
        assert at(after, "started_at") >= at(before, "finished_at")
    last = ended[2]
    ran = (at(last, "finished_at") - at(last, "started_at")).total_seconds()
    assert last["backtest"]["duration_s"] == pytest.approx(ran, abs=0.01)
    assert at(last, "started_at") > at(last, "created_at")


@pytest.mark.usefixtures("market")
def test_twenty_backtests_wait_at_most(ada) -> None:
    model_id = ada.runnable_model()
    # No worker answers: the first keeps running.
    answers = [ada.start(body(model_id)) for _ in range(21)]

    refused = ada.start(body(model_id))

    assert [answer.status_code for answer in answers] == [201] * 21
    assert answers[-1].json()["backtest"]["queue_position"] == 20
    assert refused.status_code == 409
    assert refused.json()["type"] == PROBLEMS + "queue-full"
    assert refused.json()["limit"] == 20
    # No run of the user finished yet: a minute a run.
    assert refused.json()["retry_after_s"] == 60
    assert refused.headers["retry-after"] == "60"
    assert ada.backtests().json()["total"] == 21


@pytest.mark.usefixtures("market")
def test_a_full_queue_says_when_to_try_again_from_the_users_runs(ada) -> None:
    for number, seconds in enumerate([100.0, 20.0, 30.0], start=1):
        backtest_id = ada.backtest(number)
        ada.sql("UPDATE backtests SET duration_s = :s WHERE id = :id", s=seconds, id=backtest_id)
    model_id = ada.runnable_model()
    for _ in range(21):
        ada.start(body(model_id))  # no worker answers: the first keeps running

    refused = ada.start(body(model_id)).json()

    assert refused["retry_after_s"] == 30
    assert "in about 30 s" in refused["detail"]


@pytest.mark.usefixtures("market")
def test_a_start_sent_again_with_its_idempotency_key_answers_the_first(ada, bob, sql) -> None:
    model_id = ada.runnable_model()
    headers = {"Idempotency-Key": "retry-of-start-one"}

    first = ada.start(body(model_id), headers=headers)
    again = ada.start(body(model_id), headers=headers)
    other = ada.start(body(model_id, execution={"fee_bps": 9.0}), headers=headers)
    bobs = bob.start(body(bob.runnable_model()), headers=headers)
    bad = ada.start(body(model_id), headers={"Idempotency-Key": "with space"})

    assert (first.status_code, again.status_code) == (201, 201)
    assert again.json() == first.json()
    assert again.headers["location"] == first.headers["location"]
    assert again.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert other.status_code == 422
    assert other.json()["type"] == PROBLEMS + "idempotency-key-reused"
    # Another user's key is their own.
    assert bobs.status_code == 201
    assert bad.status_code == 422
    assert bad.json()["type"] == PROBLEMS + "validation"
    assert sql("SELECT user_id, count(*) FROM backtests GROUP BY user_id ORDER BY 1") == [
        (ada.id, 1),
        (bob.id, 1),
    ]


@pytest.mark.usefixtures("market")
def test_a_refused_start_keeps_no_idempotency_key(ada) -> None:
    model_id = ada.runnable_model()
    headers = {"Idempotency-Key": "k-full"}
    for _ in range(21):
        ada.start(body(model_id))

    refused = ada.start(body(model_id), headers=headers)
    ada.cancel(ada.backtests().json()["backtests"][0]["id"])
    started = ada.start(body(model_id), headers=headers)

    assert refused.status_code == 409
    assert started.status_code == 201
    assert "idempotent-replayed" not in started.headers


@pytest.mark.usefixtures("market")
def test_a_queued_backtest_can_be_cancelled_and_never_runs(ada, bob) -> None:
    model_id = ada.runnable_model()
    running = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    first = ada.start(body(model_id)).json()["backtest"]["id"]
    second = ada.start(body(model_id)).json()["backtest"]["id"]

    of_another = bob.cancel(first)
    cancelled = ada.cancel(first)
    again = ada.cancel(first)
    of_running = ada.cancel(running)

    assert of_another.status_code == 404
    assert cancelled.status_code == 200
    assert cancelled.headers["cache-control"] == "no-store"
    document = cancelled.json()
    assert document["backtest"]["status"] == "failed"
    assert document["backtest"]["error"] == "You cancelled the backtest before it ran."
    assert document["backtest"]["queue_position"] is None
    assert document["backtest"]["started_at"] is None
    assert document["backtest"]["duration_s"] is None
    assert document["backtest"]["finished_at"] is not None
    assert states(document) == ["pending"] * 4
    assert again.status_code == 409
    assert again.json()["type"] == PROBLEMS + "backtest-not-queued"
    assert "has ended" in again.json()["detail"]
    assert of_running.status_code == 409
    assert of_running.json()["type"] == PROBLEMS + "backtest-not-queued"
    assert "running already" in of_running.json()["detail"]
    # The one behind it moved up, and the running one runs on.
    assert ada.document(second).json()["backtest"]["queue_position"] == 1
    assert ada.document(running).json()["backtest"]["status"] == "running"


def test_the_next_backtest_runs_when_the_running_one_fails(ada, worker) -> None:
    # A worker that stops, as in a restart, fails the job it ran.
    worker.failure = ("worker-stopped", WORKER_STOPPED)
    worker.gate.clear()
    model_id = ada.runnable_model()
    first = ada.start(body(model_id)).json()["backtest"]["id"]
    second = ada.start(body(model_id)).json()["backtest"]["id"]
    soon(lambda: worker.jobs)

    worker.gate.set()
    failed = ada.ended(first)
    after = ada.ended(second)

    assert failed["backtest"]["status"] == "failed"
    assert failed["backtest"]["error"] == WORKER_STOPPED
    # It ran: its job reached the worker, which failed it too.
    assert after["backtest"]["error"] == WORKER_STOPPED
    assert states(after) == ["done", "done", "failed", "pending"]
    assert len(worker.jobs) == 2
    assert at(after, "started_at") >= at(failed, "finished_at")


@pytest.mark.usefixtures("market")
def test_queued_backtests_outlive_a_restart_of_the_server(ada, app, client, market) -> None:
    model_id = ada.runnable_model()
    first = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    second = ada.start(body(model_id)).json()["backtest"]["id"]
    soon(lambda: states(ada.document(first).json())[1] == "running")

    stopping = app.state.backtest_runner
    client.portal.call(stopping.close)
    stopped = ada.document(second).json()["backtest"]
    # The start of the next process.
    restarted = BacktestRunner(app.state.db, market, stopping._jobs)
    app.state.backtest_runner = restarted
    try:
        client.portal.call(restarted.recover)
        resumed = ada.document(second).json()["backtest"]
    finally:
        client.portal.call(restarted.close)

    assert ada.document(first).json()["backtest"]["status"] == "failed"
    assert stopped["status"] == "queued"
    assert resumed["status"] == "running"
    assert resumed["started_at"] is not None


@pytest.mark.usefixtures("market")
def test_a_queue_that_stood_still_moves_on_at_the_next_check(ada, app, client) -> None:
    model_id = ada.runnable_model()
    first = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    second = ada.start(body(model_id)).json()["backtest"]["id"]
    # The run ended, and its queue could not be moved on then.
    ada.sql("UPDATE backtests SET status = 'failed' WHERE id = :id", id=first)

    client.portal.call(app.state.backtest_runner.resume)

    assert ada.document(second).json()["backtest"]["status"] == "running"


@pytest.mark.usefixtures("market")
def test_a_start_moves_on_a_queue_that_stood_still(ada) -> None:
    model_id = ada.runnable_model()
    first = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    second = ada.start(body(model_id)).json()["backtest"]["id"]
    ada.sql("UPDATE backtests SET status = 'failed' WHERE id = :id", id=first)

    third = ada.start(body(model_id)).json()["backtest"]

    assert ada.document(second).json()["backtest"]["status"] == "running"
    assert (third["status"], third["queue_position"]) == ("queued", 1)


@pytest.mark.usefixtures("market")
def test_a_late_recovery_fails_only_the_runs_of_a_stopped_process(ada, app, client) -> None:
    model_id = ada.runnable_model()
    left = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    ours = ada.start(body(model_id)).json()["backtest"]["id"]
    # ``left`` began before this process (as one a stopped process left), ``ours`` after.
    ada.sql(
        "UPDATE backtests SET started_at = started_at - interval '1 day' WHERE id = :id", id=left
    )
    ada.sql("UPDATE backtests SET status = 'running', started_at = now() WHERE id = :id", id=ours)
    runner = app.state.backtest_runner
    runner._recovered = False  # Postgres was out of reach at the start

    client.portal.call(runner._check)

    assert ada.document(left).json()["backtest"]["status"] == "failed"
    assert ada.document(ours).json()["backtest"]["status"] == "running"
    assert runner._recovered


@pytest.mark.usefixtures("market")
def test_a_backtest_handed_to_a_closed_runner_goes_back_to_its_queue(ada, app, client) -> None:
    model_id = ada.runnable_model()
    first = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    second = ada.start(body(model_id)).json()["backtest"]["id"]
    runner = app.state.backtest_runner
    # Taken from its queue, as ``_next`` does, when the shutdown began.
    ada.sql("UPDATE backtests SET status = 'running', started_at = now() WHERE id = :id", id=second)

    async def hand_over_after_close() -> None:
        await runner.close()
        runner.start(second)
        await runner.close()  # waits for it to be put back

    client.portal.call(hand_over_after_close)
    requeued = ada.document(second).json()["backtest"]

    assert ada.document(first).json()["backtest"]["status"] == "failed"
    assert (requeued["status"], requeued["queue_position"]) == ("queued", 1)
    assert requeued["started_at"] is None


def test_one_users_queue_does_not_hold_back_another_users_backtest(
    ada, bob, worker, settings
) -> None:
    worker.gate.clear()  # ada's first run holds the worker
    adas_model = ada.runnable_model()
    for _ in range(21):
        ada.start(body(adas_model))
    soon(lambda: worker.jobs)

    bobs = bob.start(body(bob.runnable_model())).json()["backtest"]
    # Its job waits for the worker, behind no other of ada's.
    with redis.Redis.from_url(settings.jobs_redis_url.get_secret_value()) as jobs:
        soon(lambda: jobs.llen(QUEUE) == 1)
    worker.gate.set()
    ended = bob.ended(bobs["id"])

    assert bobs["status"] == "running"
    assert ended["backtest"]["status"] == "finished"
    owners = [job.model.split("/", 1)[0] for job in worker.jobs]
    assert owners[:2] == [ada.id, bob.id]


def test_backtests_run_in_as_many_slots_as_the_runner_has(ada, bob, app, worker) -> None:
    worker.gate.clear()  # every run holds its slot
    app.state.backtest_runner._slots = 1

    adas = ada.start(body(ada.runnable_model())).json()["backtest"]
    bobs = bob.start(body(bob.runnable_model())).json()["backtest"]
    check = bob.request(
        "POST", "/api/v1/backtests/check", json=body(bob.runnable_model(name="b.keras"))
    )

    assert adas["status"] == "running"
    # Bob has none running, yet waits for the one slot.
    assert (bobs["status"], bobs["queue_position"], bobs["started_at"]) == ("queued", 1, None)
    # Another of Bob's would wait behind his first.
    assert (check.json()["starts"], check.json()["queue_position"]) == ("queued", 2)
    worker.gate.set()
    assert ada.ended(adas["id"])["backtest"]["status"] == "finished"
    ended = bob.ended(bobs["id"])
    assert ended["backtest"]["status"] == "finished"
    assert at(ended, "started_at") >= at(ada.document(adas["id"]).json(), "finished_at")


def test_the_users_take_turns_at_the_slots(ada, bob, app, worker) -> None:
    worker.gate.clear()
    app.state.backtest_runner._slots = 1
    adas_model, bobs_model = ada.runnable_model(), bob.runnable_model()
    adas = [ada.start(body(adas_model)).json()["backtest"]["id"] for _ in range(3)]
    bobs = [bob.start(body(bobs_model)).json()["backtest"]["id"] for _ in range(2)]

    worker.gate.set()
    ended = [ada.ended(backtest_id) for backtest_id in adas] + [
        bob.ended(backtest_id) for backtest_id in bobs
    ]

    starts = sorted(
        zip((at(document, "started_at") for document in ended), adas + bobs, strict=True)
    )
    # Ada's first held the slot when Bob came; then one each in turn, Ada's last at the end.
    assert [backtest_id for _, backtest_id in starts] == [
        adas[0],
        bobs[0],
        adas[1],
        bobs[1],
        adas[2],
    ]


@pytest.mark.usefixtures("market")
def test_a_start_whose_request_goes_away_after_its_commit_still_gets_its_run(
    ada, app, client
) -> None:
    setup = BacktestIn.model_validate(body(ada.runnable_model()))
    stored: list[str] = []

    async def start_and_go_away() -> None:
        async with app.state.db.sessions() as db, anyio.create_task_group() as group:

            async def committed(backtest_id: str) -> None:
                stored.append(backtest_id)
                group.cancel_scope.cancel()  # the client went away

            group.start_soon(backtest_runs(app.state, db).start, ada.id, setup, committed)

    client.portal.call(start_and_go_away)
    (backtest_id,) = stored

    assert ada.document(backtest_id).json()["backtest"]["status"] == "running"
    assert backtest_id in app.state.backtest_runner._tasks.values()


@pytest.mark.usefixtures("market")
def test_a_backtest_running_without_a_run_is_queued_again_and_run(ada, app, client) -> None:
    model_id = ada.runnable_model()
    orphan = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    runner = app.state.backtest_runner
    # As a turn whose run was never started: running in the table, no task here.
    client.portal.call(runner.close)
    restarted = BacktestRunner(app.state.db, runner._data, runner._jobs)
    app.state.backtest_runner = restarted
    ada.sql(
        "UPDATE backtests SET status = 'running', error = NULL, finished_at = NULL, "
        "started_at = now() - interval '2 minutes' WHERE id = :id",
        id=orphan,
    )
    restarted._recovered = True  # its start found nothing left by a stopped process
    try:
        client.portal.call(restarted._check)
        again = ada.document(orphan).json()["backtest"]
        ours = set(restarted._tasks.values())
    finally:
        client.portal.call(restarted.close)

    assert again["status"] == "running"
    assert at({"backtest": again}, "started_at") > datetime.now(UTC) - timedelta(minutes=1)
    assert orphan in ours


@pytest.mark.usefixtures("market")
def test_queued_backtests_of_several_users_fill_the_slots_after_a_restart(
    ada, bob, app, client, market
) -> None:
    adas_model, bobs_model = ada.runnable_model(), bob.runnable_model()
    adas = [ada.start(body(adas_model)).json()["backtest"]["id"] for _ in range(2)]
    bobs = [bob.start(body(bobs_model)).json()["backtest"]["id"] for _ in range(2)]
    stopping = app.state.backtest_runner
    client.portal.call(stopping.close)
    restarted = BacktestRunner(app.state.db, market, stopping._jobs, slots=2)
    app.state.backtest_runner = restarted
    try:
        client.portal.call(restarted.recover)
        statuses = {
            backtest_id: ada.document(backtest_id).json()["backtest"]["status"]
            for backtest_id in adas[1:]
        } | {
            backtest_id: bob.document(backtest_id).json()["backtest"]["status"]
            for backtest_id in bobs[1:]
        }
    finally:
        client.portal.call(restarted.close)

    # The runs the stop ended failed; the next of each user took the two slots.
    assert statuses == {adas[1]: "running", bobs[1]: "running"}


# --- The name it gets --------------------------------------------------------------------------


@pytest.mark.usefixtures("market")
def test_a_new_backtest_gets_a_name_of_the_word_lists(ada, sql) -> None:
    started = ada.start(body(ada.runnable_model())).json()["backtest"]

    adjective, noun = started["name"].split(" ")
    assert (adjective in ADJECTIVES, noun in NOUNS) == (True, True)
    assert started["generated_name"] == started["name"]
    assert sql("SELECT name, generated_name FROM backtests") == [(None, started["name"])]
    (listed,) = ada.backtests().json()["backtests"]
    assert (listed["name"], listed["generated_name"]) == (started["name"], started["name"])


@pytest.mark.usefixtures("market")
def test_a_new_name_is_none_the_account_has(ada, bob, monkeypatch) -> None:
    ada.backtest(1, generated_name="Silent Orion", name="Half Size", status="failed")
    bob.backtest(1, generated_name="Bright Vega", status="failed")
    # The account's own and generated names are taken in any case; never another account's.
    tried = ["SILENT ORION", "half size", "Bright Vega", "Quiet Vega"]
    monkeypatch.setattr(store_module, "candidates", lambda count: tried[:count])

    started = ada.start(body(ada.runnable_model())).json()["backtest"]

    assert started["number"] == 2
    assert started["name"] == "Bright Vega"


@pytest.mark.usefixtures("market")
def test_a_new_name_reads_every_name_only_when_every_one_tried_is_taken(ada, monkeypatch) -> None:
    ada.backtest(1, generated_name="Silent Orion", status="failed")
    monkeypatch.setattr(store_module, "candidates", lambda _count: ["Silent Orion"])
    seen: list[set[str]] = []

    def generated_name(taken: set[str]) -> str:
        seen.append(set(taken))
        return names_module.generated_name(taken)

    monkeypatch.setattr(store_module, "generated_name", generated_name)

    started = ada.start(body(ada.runnable_model())).json()["backtest"]

    assert seen == [{"silent orion"}]
    assert started["name"] != "Silent Orion"


async def _drawn(names: Any, _store: Any, _user_id: str) -> str:
    return next(names)


@pytest.mark.usefixtures("market")
def test_a_name_the_index_refuses_is_drawn_again(ada, sql, monkeypatch) -> None:
    ada.backtest(1, generated_name="Silent Orion", status="failed")
    # As a writer without the lock could: a name the account has, then a free one.
    drawn = iter(["SILENT ORION", "Bright Vega"])
    monkeypatch.setattr(
        store_module.BacktestStore, "free_name", lambda store, user: _drawn(drawn, store, user)
    )

    started = ada.start(body(ada.runnable_model()))

    assert started.status_code == 201
    assert started.json()["backtest"]["name"] == "Bright Vega"
    assert sql("SELECT number, generated_name FROM backtests ORDER BY number") == [
        (1, "Silent Orion"),
        (2, "Bright Vega"),
    ]


@pytest.mark.usefixtures("market")
def test_no_backtest_is_stored_when_every_draw_is_refused(ada, sql, monkeypatch) -> None:
    ada.backtest(1, generated_name="Silent Orion", status="failed")
    drawn: list[str] = []

    async def taken_name(_store: Any, user_id: str) -> str:
        drawn.append(user_id)
        return "Silent Orion"

    monkeypatch.setattr(store_module.BacktestStore, "free_name", taken_name)

    started = ada.start(body(ada.runnable_model()))

    assert started.status_code == 500
    assert len(drawn) == store_module.NAME_ATTEMPTS
    assert sql("SELECT count(*) FROM backtests") == [(1,)]


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
    """The start and the download end their reads before they ask for market data."""
    backtest_id = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]
    at_start = market.held[0]
    ada.ended(backtest_id)
    market.held.clear()

    ada.download(backtest_id)

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
        ({"benchmarks": [{**SPY, "instrument": "stock:VOO"}]}, "validation", 422),
        (
            {"data": period(instrument="crypto:BTCUSDT"), "benchmarks": [SPY]},
            "validation",
            422,
        ),
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


def test_the_period_must_fit_the_index_funds_data_too(ada, market) -> None:
    market.refuse_of["SPY"] = PeriodOutOfRange("stock:SPY", "30m", START + DAY, END, start=START)

    response = ada.start(body(ada.runnable_model(), benchmarks=[SPY]))

    assert (response.status_code, response.json()["type"]) == (
        422,
        PROBLEMS + "period-out-of-range",
    )
    assert response.json()["detail"].startswith("stock:SPY has 30m candles from ")
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


@pytest.mark.usefixtures("worker")
def test_a_published_backtest_is_shown_to_others_read_only(ada, bob) -> None:
    bob.sql("UPDATE users SET display_name = 'Bob Stone' WHERE id = :id", id=bob.id)
    model_id = bob.runnable_model()
    started = bob.start(body(model_id)).json()
    backtest_id = started["backtest"]["id"]
    own = bob.ended(backtest_id)
    before = ada.document(backtest_id)

    bob.publish(backtest_id)
    shown = ada.document(backtest_id)
    download = ada.download(backtest_id)
    published = bob.document(backtest_id).json()

    author = {"id": bob.id, "name": "Bob Stone", "mine": True}
    assert started["backtest"]["author"] == author
    assert (started["backtest"]["public"], started["backtest"]["published_at"]) == (False, None)
    assert (own["backtest"]["author"], own["model"]["id"]) == (author, model_id)
    assert (before.status_code, before.json()["type"]) == (404, PROBLEMS + "not-found")
    assert shown.status_code == 200
    document = shown.json()
    assert document["backtest"]["author"] == {**author, "mine": False}
    assert document["backtest"]["public"] is True
    assert document["backtest"]["published_at"] == published["backtest"]["published_at"]
    assert document["model"]["id"] is None
    assert document["model"]["manifest"] == own["model"]["manifest"]  # what the model reads
    assert document["results"] == own["results"]
    assert bob.email not in shown.text
    assert (published["model"]["id"], published["backtest"]["author"]["mine"]) == (model_id, True)
    assert download.status_code == 200
    assert download.json()["backtest"]["author"]["mine"] is False
    assert bob.email not in download.text

    bob.publish(backtest_id, public=False)

    assert ada.document(backtest_id).status_code == 404
    assert ada.download(backtest_id).status_code == 404
    assert bob.document(backtest_id).status_code == 200


@pytest.mark.usefixtures("worker")
def test_the_document_has_the_name_its_author_gives_it(ada, bob) -> None:
    backtest_id = bob.start(body(bob.runnable_model())).json()["backtest"]["id"]
    generated = bob.ended(backtest_id)["backtest"]["generated_name"]
    bob.rename(backtest_id, "half size")
    bob.publish(backtest_id)

    own = bob.document(backtest_id).json()["backtest"]
    shown = ada.document(backtest_id).json()["backtest"]
    download = ada.download(backtest_id).json()["backtest"]

    assert (own["name"], own["generated_name"]) == ("half size", generated)
    assert (shown["name"], shown["generated_name"]) == ("half size", generated)
    assert (download["name"], download["generated_name"]) == ("half size", generated)


@pytest.mark.usefixtures("market")
def test_a_running_backtest_of_another_account_is_not_found(ada, bob) -> None:
    # No worker answers: it keeps running.
    backtest_id = bob.start(body(bob.runnable_model())).json()["backtest"]["id"]
    bob.sql("UPDATE backtests SET published_at = now()")  # cannot happen through the API

    assert ada.document(backtest_id).status_code == 404


@pytest.mark.usefixtures("market")
def test_the_document_times_are_utc(ada) -> None:
    started = ada.start(body(ada.runnable_model())).json()

    created = datetime.fromisoformat(started["backtest"]["created_at"])

    assert started["backtest"]["created_at"].endswith("Z")
    assert abs((datetime.now(UTC) - created).total_seconds()) < 60
