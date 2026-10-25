"""The result document (docs/engine.md, "The result document"): the worked example built as a
document must be the one the document prints, the light view of long runs, the trades'
``candles_held``, the decision as ``run.json`` records it, the short equity series, the stored
predictions and the JSON Schema snapshot."""

import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import orjson
import polars as pl
import pytest
from pydantic import ValidationError

from candlestack.experiments.assembly import (
    NothingToTest,
    document,
    dumps,
    equity_points,
    predictions_parquet,
    read_predictions,
    results,
    run,
    stats_out,
)
from candlestack.experiments.document import (
    LIGHT_ROWS,
    LIGHT_TRADES,
    BacktestDocument,
    BacktestHeader,
    BacktestIn,
    BacktestModel,
    BenchmarkIn,
    DataOut,
    Decision,
    Execution,
    MetricsArgs,
    Period,
    Software,
)
from candlestack.experiments.json_schema import render
from candlestack.signals import describe

DOCS = Path(__file__).parents[4] / "docs"
PRINTED = 5e-7  # the document prints at least 6 decimals
START = 1_717_421_400  # 2024-06-03 13:30 UTC, the 09:30 open in New York
TS = [START + 1800 * row for row in range(5)]
CANDLES = pl.DataFrame(
    {
        "ts": TS,
        "open": [100.0, 100.0, 102.0, 99.0, 97.0],
        "high": [101.0, 102.0, 104.0, 99.5, 98.0],
        "low": [99.0, 99.5, 101.0, 96.0, 96.5],
        "close": [100.0, 101.0, 103.0, 97.0, 98.0],
        "volume": [1.0] * 5,
    }
)
PREDICTIONS = pl.DataFrame({"ts": TS, "prediction": [0.01, 0.3, 0.2, 0.1, 0.4]})
PERIOD = Period(start=1_717_372_800, end=1_717_459_200)
DECISION = Decision.model_validate({"rule": {"name": "threshold", "limit": 0.05}})
EXECUTION = Execution(fill="next_open", stop_loss=0.05, fee_bps=10)
BH = BenchmarkIn(id="bh", kind="buy_and_hold", instrument="stock:AAPL")


def printed_document() -> dict[str, Any]:
    """The JSON of docs/engine.md under "The shape", without its // notes."""
    text = (DOCS / "engine.md").read_text(encoding="utf-8")
    block = text.split("### The shape", 1)[1].split("```jsonc\n", 1)[1].split("```", 1)[0]
    return json.loads(re.sub(r"\s//[^\n]*", "", block))


def assert_close(actual: Any, expected: Any, where: str = "") -> None:
    """Equal, floats within what the document prints."""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), where
        assert list(actual) == list(expected), where
        for key in expected:
            assert_close(actual[key], expected[key], f"{where}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), where
        assert len(actual) == len(expected), where
        for i, (got, want) in enumerate(zip(actual, expected, strict=True)):
            assert_close(got, want, f"{where}[{i}]")
    elif isinstance(expected, float):
        assert actual == pytest.approx(expected, abs=PRINTED), where
    else:
        assert actual == expected, where


def example_document(light: bool = True) -> dict[str, Any]:
    printed = printed_document()
    done = run(
        CANDLES,
        PREDICTIONS,
        period=PERIOD,
        decision=DECISION,
        execution=EXECUTION,
        periods_per_year=252,
    )
    built = document(
        backtest=BacktestHeader.model_validate(printed["backtest"]),
        software=Software.model_validate(printed["software"]),
        model=BacktestModel.model_validate(printed["model"]),
        data=DataOut.model_validate(printed["data"]),
        decision=DECISION,
        execution=EXECUTION,
        benchmarks=[BH],
        metrics=MetricsArgs(periods_per_year=252, risk_free_rate=0.0),
        results=results(done, [BH], light=light),
    )
    return orjson.loads(dumps(built))


# --- The worked example -------------------------------------------------------------------


def test_the_worked_example_is_the_document_the_doc_prints() -> None:
    assert_close(example_document(), printed_document())


def test_the_download_of_a_short_run_is_the_light_view() -> None:
    assert example_document(light=False) == example_document(light=True)


def test_the_document_reads_as_its_model() -> None:
    parsed = BacktestDocument.model_validate(example_document())

    assert parsed.results is not None
    assert parsed.results.trades.columns.return_ == [pytest.approx(-0.05195)]


def test_the_groups_of_a_document_start_a_new_backtest() -> None:
    """Sending the setup groups of a document back runs the backtest again."""
    printed = printed_document()

    again = BacktestIn.model_validate(printed)

    assert again.model.id == printed["model"]["id"]
    assert again.data.instrument.id == "stock:AAPL"
    assert again.data.period == PERIOD
    assert again.decision == DECISION
    assert again.execution == EXECUTION
    assert again.benchmarks == [BH]


# --- The decision -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "decision",
    [
        {},
        {"rule": {"name": "threshold", "limit": 0.05}, "long_only": True},
        {
            "calibrate": {"name": "zscore", "window": 500},
            "ema": {"span": 5},
            "rule": {"name": "hysteresis", "enter": 1.0, "exit": 0.5},
            "long_only": True,
            "confirm": {"candles": 2},
            "min_hold": {"candles": 10},
        },
        {
            "calibrate": {"name": "rank", "window": 20},
            "rule": {"name": "kmeans", "k": 3, "window": 500, "refit_every": 100},
        },
    ],
)
def test_the_decision_is_what_run_json_records(decision: dict[str, Any]) -> None:
    parsed = Decision.model_validate(decision)

    assert parsed.model_dump() == describe(parsed.post_processing())


@pytest.mark.parametrize(
    "decision",
    [
        {"rule": {"name": "threshold", "limit": -0.1}},
        {"rule": {"name": "threshold", "limit": "0.1"}},
        {"rule": {"name": "hysteresis", "enter": 0.2, "exit": 0.3}},
        {"rule": {"name": "kmeans", "k": 3, "window": 5, "refit_every": 10}},
        {"rule": {"name": "kmeans", "k": 6, "window": 50, "refit_every": 10}},
        {"rule": {"name": "magic"}},
        {"calibrate": {"name": "zscore", "window": 1}},
        # Windows that would keep the API busy for seconds at each build of the document.
        {"calibrate": {"name": "rank", "window": 1001}},
        {"rule": {"name": "kmeans", "k": 3, "window": 5001, "refit_every": 1000}},
        {"rule": {"name": "kmeans", "k": 3, "window": 2000, "refit_every": 99}},
        {"ema": {"span": 0}},
        {"confirm": {"candles": 0}},
        {"min_hold": {"candles": 1.5}},
        {"long_only": "yes"},
        {"unknown": True},
    ],
)
def test_a_decision_out_of_range_is_refused(decision: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Decision.model_validate(decision)


def test_a_period_moves_forward() -> None:
    with pytest.raises(ValidationError, match="start must be before end"):
        Period(start=10, end=10)


# --- A longer run: the light view ---------------------------------------------------------


def long_run(rows: int, *, flips: bool = False) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Hourly candles of a random walk and predictions that flip the side every row (with
    ``flips``) or wander."""
    rng = np.random.default_rng(7)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, rows)))
    ts = 1_700_000_000 - 1_700_000_000 % 86_400 + np.arange(rows) * 3600
    candles = pl.DataFrame(
        {
            "ts": ts,
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.ones(rows),
        }
    )
    values = np.where(np.arange(rows) % 2 == 0, 1.0, -1.0) if flips else rng.normal(0, 1, rows)
    return candles, pl.DataFrame({"ts": ts, "prediction": values})


def long_results(rows: int, *, light: bool, flips: bool = False) -> dict[str, Any]:
    candles, predictions = long_run(rows, flips=flips)
    done = run(
        candles,
        predictions,
        period=Period(start=0, end=2**40),
        decision=Decision(),
        execution=Execution(),
        periods_per_year=365,
    )
    return orjson.loads(dumps(results(done, [BH], light=light)))


def test_a_long_series_becomes_one_point_per_utc_day() -> None:
    rows = LIGHT_ROWS + 1
    full = long_results(rows, light=False)["series"]
    light = long_results(rows, light=True)["series"]

    assert (full["resolution"], full["count"]) == ("candle", rows)
    assert light["resolution"] == "day"
    assert light["count"] == -(-rows // 24)  # hourly candles from a UTC midnight
    assert set(light["columns"]) == {"ts", "close", "position", "equity", "drawdown"}
    days = (
        pl.DataFrame(full["columns"])
        .with_columns(bh=pl.Series(full["benchmarks"]["bh"]))
        .group_by(pl.col("ts") // 86_400, maintain_order=True)
    )
    by_day = days.agg(
        pl.col("ts").last().alias("last"),
        pl.col("equity").last(),
        pl.col("drawdown").min(),
        pl.col("bh").last(),
    )
    assert light["columns"]["ts"] == by_day["last"].to_list()
    assert light["columns"]["equity"] == by_day["equity"].to_list()
    assert light["columns"]["drawdown"] == by_day["drawdown"].to_list()
    assert light["benchmarks"]["bh"] == by_day["bh"].to_list()


def test_the_lowest_point_of_the_days_is_the_max_drawdown() -> None:
    light = long_results(LIGHT_ROWS + 1, light=True)

    assert min(light["series"]["columns"]["drawdown"]) == pytest.approx(
        light["stats"]["max_drawdown"]
    )


def test_a_series_of_10000_rows_keeps_every_row() -> None:
    assert long_results(LIGHT_ROWS, light=True)["series"]["resolution"] == "candle"


def test_more_trades_than_the_light_view_keeps_the_newest() -> None:
    rows = 2 * LIGHT_TRADES + 10
    full = long_results(rows, light=False, flips=True)["trades"]
    light = long_results(rows, light=True, flips=True)["trades"]

    assert (full["count"], full["complete"]) == (full["total"], True)
    assert full["total"] > LIGHT_TRADES
    assert (light["count"], light["total"], light["complete"]) == (
        LIGHT_TRADES,
        full["total"],
        False,
    )
    for name, column in light["columns"].items():
        assert column == full["columns"][name][-LIGHT_TRADES:], name


def test_candles_held_counts_the_rows_from_entry_to_exit() -> None:
    signal_close = Execution(fill="signal_close", stop_loss=0.05, fee_bps=10)
    done = run(
        CANDLES,
        PREDICTIONS,
        period=PERIOD,
        decision=DECISION,
        execution=signal_close,
        periods_per_year=252,
    )

    trades = orjson.loads(dumps(results(done, [], light=True)))["trades"]

    assert trades["columns"]["candles_held"] == [3]  # in on row 1, out on row 4 (end)


def test_no_benchmark_no_benchmark_series() -> None:
    done = run(
        CANDLES,
        PREDICTIONS,
        period=PERIOD,
        decision=DECISION,
        execution=EXECUTION,
        periods_per_year=252,
    )

    shown = orjson.loads(dumps(results(done, [], light=True)))

    assert shown["benchmarks"] == {}
    assert shown["series"]["benchmarks"] == {}


# --- The rows the engine runs on ----------------------------------------------------------


def test_only_the_candles_and_predictions_of_the_period_run() -> None:
    period = Period(start=TS[1], end=TS[4])

    done = run(
        CANDLES,
        PREDICTIONS,
        period=period,
        decision=DECISION,
        execution=EXECUTION,
        periods_per_year=252,
    )

    assert done.rows["ts"].to_list() == TS[1:4]


def test_a_candle_without_a_prediction_repeats_the_last_one() -> None:
    gappy = PREDICTIONS.filter(pl.col("ts") != TS[2])

    done = run(
        CANDLES,
        gappy,
        period=PERIOD,
        decision=DECISION,
        execution=EXECUTION,
        periods_per_year=252,
    )

    assert done.rows["prediction"].to_list() == [0.01, 0.3, 0.3, 0.1, 0.4]
    assert done.report.candles_without_prediction == 1


@pytest.mark.parametrize(
    "predictions",
    [
        PREDICTIONS.clear(),
        PREDICTIONS.with_columns(pl.col("ts") + 60),
        PREDICTIONS.with_columns(pl.col("ts") + 86_400 * 10),
    ],
)
def test_no_prediction_on_a_candle_of_the_period_is_nothing_to_test(
    predictions: pl.DataFrame,
) -> None:
    with pytest.raises(NothingToTest, match="no prediction on a candle of the period"):
        run(
            CANDLES,
            predictions,
            period=PERIOD,
            decision=DECISION,
            execution=EXECUTION,
            periods_per_year=252,
        )


def test_stats_show_the_drawdown_negative() -> None:
    assert stats_out({"max_drawdown": 0.2, "sharpe": 1.0}) == {"max_drawdown": -0.2, "sharpe": 1.0}
    assert str(stats_out({"max_drawdown": 0.0})["max_drawdown"]) == "0.0"
    assert stats_out({"max_drawdown": None}) == {"max_drawdown": None}


# --- What a backtest stores ---------------------------------------------------------------


def test_the_equity_points_of_the_worked_example() -> None:
    done = run(
        CANDLES,
        PREDICTIONS,
        period=PERIOD,
        decision=DECISION,
        execution=EXECUTION,
        periods_per_year=252,
    )

    points = equity_points(done.result, done.rows, 60)

    assert points is not None
    strategy, benchmark = points
    assert strategy == pytest.approx([0.0, 0.0, 0.008804, -0.05195, -0.05195], abs=PRINTED)
    assert benchmark == pytest.approx([0.0, 0.01, 0.03, -0.03, -0.02])


def test_a_long_run_has_60_evenly_spaced_equity_points_ending_at_its_returns() -> None:
    candles, predictions = long_run(3000)
    done = run(
        candles,
        predictions,
        period=Period(start=0, end=2**40),
        decision=Decision(),
        execution=Execution(),
        periods_per_year=365,
    )

    points = equity_points(done.result, done.rows, 60)

    assert points is not None
    strategy, benchmark = points
    assert len(strategy) == len(benchmark) == 60
    assert strategy[0] == benchmark[0] == 0.0
    assert strategy[-1] == pytest.approx(done.stats["total_return"])
    assert benchmark[-1] == pytest.approx(done.stats["buy_and_hold_return"])


def test_one_row_has_no_equity_points() -> None:
    done = run(
        CANDLES,
        PREDICTIONS,
        period=Period(start=TS[0], end=TS[1]),
        decision=DECISION,
        execution=EXECUTION,
        periods_per_year=252,
    )

    assert equity_points(done.result, done.rows, 60) is None


def test_the_predictions_are_stored_as_parquet_and_read_back() -> None:
    blob, digest = predictions_parquet(PREDICTIONS.with_columns(pl.col("ts").cast(pl.Int32)))

    assert blob[:4] == b"PAR1"
    assert len(digest) == 64
    read = read_predictions(blob)
    assert read.schema == pl.Schema({"ts": pl.Int64, "prediction": pl.Float64})
    assert read.equals(PREDICTIONS)
    assert predictions_parquet(PREDICTIONS) == (blob, digest)


# --- The JSON Schema ----------------------------------------------------------------------


def test_snapshot_is_current() -> None:
    """docs/backtest.schema.json is the result document's contract: it changes with the code."""
    assert (DOCS / "backtest.schema.json").read_text(encoding="utf-8") == render(), (
        "docs/backtest.schema.json is stale. Regenerate it from backend/: "
        "uv run python -m candlestack.experiments.json_schema > ../docs/backtest.schema.json"
    )


def test_the_schema_names_its_dialect_and_the_version() -> None:
    schema = json.loads(render())

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["version"]["const"] == 1
    assert "return" in schema["$defs"]["TradeColumns"]["properties"]
