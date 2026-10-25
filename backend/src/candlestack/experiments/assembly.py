"""A backtest from its candles and stored predictions: ``signals``, ``engine`` and ``metrics``
in the order of docs/engine.md, and the results of the result document from what they give.

The engine is deterministic, so the run of a backtest and every later build of its document
call ``run`` with the same inputs and get the same numbers. The long tables are written with
orjson straight from the Polars columns (``dumps``), never as Python lists of every value.
"""

import hashlib
import io
from dataclasses import dataclass
from typing import Any

import numpy as np
import orjson
import polars as pl

from candlestack.engine import Result, backtest
from candlestack.experiments.document import (
    LIGHT_ROWS,
    LIGHT_TRADES,
    VERSION,
    BacktestHeader,
    BacktestModel,
    BenchmarkIn,
    BenchmarkOut,
    DataOut,
    Decision,
    Execution,
    MetricsArgs,
    Period,
    Software,
)
from candlestack.metrics import drawdown, stats
from candlestack.signals import AlignReport, align, make_signal

# Days a year scales Sharpe and Sortino by: stocks trade on about 252, crypto on every one.
PERIODS_PER_YEAR = {"stock": 252, "crypto": 365}
RISK_FREE_RATE = 0.0

# Epoch seconds have no leap seconds, so ts // _DAY is the UTC date of a candle.
_DAY = 86_400
_PRICES = ("ts", "open", "high", "low", "close")
_NUMPY = orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_UTC_Z


class NothingToTest(Exception):
    """The period has no candle with a prediction, so there is nothing to backtest. The
    message is for the user."""


@dataclass(frozen=True, eq=False)
class Run:
    """One run of the engine: ``rows`` are what it ran on (the candles of the period with
    ``prediction`` and ``signal``), ``stats`` what ``metrics.stats`` gives (``max_drawdown``
    positive, as the engine's convention is)."""

    rows: pl.DataFrame
    report: AlignReport
    result: Result
    stats: dict[str, float | int | None]


def run(
    candles: pl.DataFrame,
    predictions: pl.DataFrame,
    *,
    period: Period,
    decision: Decision,
    execution: Execution,
    periods_per_year: int,
) -> Run:
    """The backtest of ``predictions`` (``ts``, ``prediction``) on the ``candles`` of ``period``:
    ``align`` (not strict: a candle without a new prediction repeats the last one, as with
    rows that span several candles), the decision's steps, ``backtest``, ``stats``.

    Raises:
        NothingToTest: no candle of the period has a prediction.
        ValueError: a step refuses the predictions or the candles.
    """
    within = candles.select(_PRICES).filter(pl.col("ts").is_between(period.start, period.end - 1))
    own = predictions.select("ts", "prediction").filter(
        pl.col("ts").is_between(period.start, period.end - 1)
    )
    if own.join(within, on="ts", how="semi").is_empty():
        raise NothingToTest(
            "The model made no prediction on a candle of the period. Choose a longer period, "
            "or one with candles of the instrument."
        )
    rows, report = align(within, own, strict=False)
    rows = rows.with_columns(make_signal(rows["prediction"], decision.post_processing()))
    result = backtest(rows, execution)
    values = stats(result.trades, result.series, rows, periods_per_year)
    return Run(rows=rows, report=report, result=result, stats=values)


def stats_out(values: dict[str, float | int | None]) -> dict[str, float | int | None]:
    """The statistics as the app and the API show them: the drawdown as the fall it is,
    negative (0.0, never -0.0, for none)."""
    fall = values["max_drawdown"]
    return {**values, "max_drawdown": None if fall is None else 0.0 - fall}


def equity_points(
    result: Result, rows: pl.DataFrame, points: int
) -> tuple[list[float], list[float]] | None:
    """The short equity series of the Backtests list and the Dashboard: the cumulative return
    of the strategy and of buy and hold (0 at the start) at ``points`` evenly spaced times from
    the first row to the last (at most one per row), each the last row at or before its time.
    None with fewer than 2 rows."""
    ts = result.series["ts"].to_numpy()
    if ts.size < 2:
        return None
    times = np.linspace(ts[0], ts[-1], min(points, ts.size))
    picked = np.clip(np.searchsorted(ts, times, side="right") - 1, 0, ts.size - 1)
    equity = result.series["equity"].to_numpy()
    close = rows["close"].to_numpy()
    strategy = equity[picked] - 1.0
    benchmark = close[picked] / close[0] - 1.0
    return [float(value) for value in strategy], [float(value) for value in benchmark]


def results(done: Run, benchmarks: list[BenchmarkIn], *, light: bool) -> dict[str, Any]:
    """The ``results`` group: the statistics, each benchmark's return, the series and the
    trades, the long columns as NumPy arrays for ``dumps``. ``light`` is the view of the page
    and the API (docs/engine.md, "The light view"); the download has every row and trade."""
    ids = [benchmark.id for benchmark in benchmarks]
    return {
        "stats": stats_out(done.stats),
        "benchmarks": {
            benchmark: {"total_return": done.stats["buy_and_hold_return"]} for benchmark in ids
        },
        "series": _series(done, ids, light=light),
        "trades": _trades(done, light=light),
    }


def _series(done: Run, benchmarks: list[str], *, light: bool) -> dict[str, Any]:
    rows, series = done.rows, done.result.series
    frame = pl.DataFrame(
        {
            "ts": series["ts"],
            "close": rows["close"],
            "prediction": rows["prediction"].cast(pl.Float64),
            "signal": series["signal"],
            "position": series["position"],
            "equity": series["equity"],
            "drawdown": drawdown(series),
            "benchmark": rows["close"] / rows["close"].first(),
        }
    )
    resolution = "candle"
    if light and frame.height > LIGHT_ROWS:
        # One point per UTC day: the days Sharpe and Sortino come from, and the lowest
        # drawdown of each, so the chart and the statistics agree.
        resolution = "day"
        frame = frame.group_by(pl.col("ts") // _DAY, maintain_order=True).agg(
            pl.col("ts").last().alias("last_ts"),
            *(pl.col(name).last() for name in ("close", "position", "equity", "benchmark")),
            pl.col("drawdown").min(),
        )
        frame = frame.drop("ts").rename({"last_ts": "ts"})
    shown = ["ts", "close", "position", "equity", "drawdown"]
    if resolution == "candle":
        shown[2:2] = ["prediction", "signal"]
    return {
        "resolution": resolution,
        "count": frame.height,
        "columns": {name: frame[name].to_numpy() for name in shown},
        "benchmarks": {benchmark: frame["benchmark"].to_numpy() for benchmark in benchmarks},
    }


def _trades(done: Run, *, light: bool) -> dict[str, Any]:
    trades = done.result.trades
    total = trades.height
    if light and total > LIGHT_TRADES:
        trades = trades.tail(LIGHT_TRADES)
    index = done.result.series.select("ts").with_row_index("row")
    held = (
        trades.select("open_ts", "close_ts")
        .join(index.rename({"ts": "open_ts", "row": "opened"}), on="open_ts", how="left")
        .join(index.rename({"ts": "close_ts", "row": "closed"}), on="close_ts", how="left")
        .select(pl.col("closed").cast(pl.Int64) - pl.col("opened").cast(pl.Int64))
        .to_series()
    )
    columns: dict[str, Any] = {
        name: trades[name].to_numpy() for name in trades.columns if name != "exit_reason"
    }
    # Text cannot go to orjson as an array; a few thousand trades are cheap as a list.
    columns["exit_reason"] = trades["exit_reason"].to_list()
    columns["candles_held"] = held.to_numpy()
    order = [*trades.columns, "candles_held"]
    return {
        "count": trades.height,
        "total": total,
        "complete": trades.height == total,
        "columns": {name: columns[name] for name in order},
    }


def document(  # noqa: PLR0913 (keyword-only: one argument per group of the document)
    *,
    backtest: BacktestHeader,
    software: Software,
    model: BacktestModel,
    data: DataOut,
    decision: Decision,
    execution: Execution,
    benchmarks: list[BenchmarkIn],
    metrics: MetricsArgs,
    results: dict[str, Any] | None,
) -> dict[str, Any]:
    """The result document, its groups in their order; ``results`` as ``results`` gives it."""
    groups = {
        "backtest": backtest,
        "software": software,
        "model": model,
        "data": data,
        "decision": decision,
        "execution": execution,
    }
    return {
        "version": VERSION,
        **{name: group.model_dump(mode="json") for name, group in groups.items()},
        "benchmarks": [
            BenchmarkOut.model_validate(benchmark.model_dump()).model_dump()
            for benchmark in benchmarks
        ],
        "metrics": metrics.model_dump(),
        "results": results,
    }


def dumps(value: dict[str, Any]) -> bytes:
    """The document as JSON: NumPy arrays written as they are, a value that is not finite as
    null, times in UTC with ``Z``."""
    return orjson.dumps(value, option=_NUMPY)


def predictions_parquet(predictions: pl.DataFrame) -> tuple[bytes, str]:
    """The stored form of a run's predictions (``ts``, ``prediction``): zstd Parquet, and its
    SHA-256 (hex)."""
    buffer = io.BytesIO()
    predictions.select(
        pl.col("ts").cast(pl.Int64), pl.col("prediction").cast(pl.Float64)
    ).write_parquet(buffer, compression="zstd")
    blob = buffer.getvalue()
    return blob, hashlib.sha256(blob).hexdigest()


def read_predictions(blob: bytes) -> pl.DataFrame:
    return pl.read_parquet(io.BytesIO(blob))
