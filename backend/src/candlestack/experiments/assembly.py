"""A backtest from its candles and stored predictions: ``signals``, ``engine`` and ``metrics``
in the order of docs/engine.md, and the results of the result document from what they give.

The engine is deterministic, so the run of a backtest and every later build of its document
call ``run`` with the same inputs and get the same numbers. The long tables are written with
orjson straight from the Polars columns (``dumps``), never as Python lists of every value.
"""

import hashlib
import io
from collections.abc import Mapping
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
    AlignOut,
    BacktestHeader,
    BacktestModel,
    BenchmarkIn,
    BenchmarkOut,
    DataOut,
    Decision,
    Execution,
    MetricsArgs,
    Period,
    Rows,
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
# The columns of the light series a backtest stores for the comparison (``curves_parquet``).
CURVES = ("ts", "close", "equity", "drawdown")
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


def rows_of(done: Run) -> Rows:
    """The rows the engine ran on, as the document's ``data.rows`` says them."""
    ts = done.rows["ts"]
    first, last = ts.first(), ts.last()
    # A run has rows (``run`` raises NothingToTest without any).
    assert isinstance(first, int)
    assert isinstance(last, int)
    return Rows(first_ts=first, last_ts=last, count=ts.len())


def align_of(done: Run) -> AlignOut:
    report = done.report
    return AlignOut(
        predictions_without_candle=report.predictions_without_candle,
        candles_without_prediction=report.candles_without_prediction,
    )


def stats_out(values: dict[str, float | int | None]) -> dict[str, float | int | None]:
    """The statistics as the app and the API show them: the drawdown as the fall it is,
    negative (0.0, never -0.0, for none)."""
    fall = values["max_drawdown"]
    return {**values, "max_drawdown": None if fall is None else 0.0 - fall}


def equity_points(
    result: Result, rows: pl.DataFrame, points: int, benchmark: pl.Series | None = None
) -> tuple[list[float], list[float]] | None:
    """The short equity series of the Backtests list, the Dashboard and Experiments: the
    cumulative return of the strategy and of the ``benchmark`` equity on the rows (buy and hold
    of the rows' closes without one), 0 at the start, at ``points`` evenly spaced times from
    the first row to the last (at most one per row), each the last row at or before its time.
    None with fewer than 2 rows."""
    ts = result.series["ts"].to_numpy()
    if ts.size < 2:
        return None
    times = np.linspace(ts[0], ts[-1], min(points, ts.size))
    picked = np.clip(np.searchsorted(ts, times, side="right") - 1, 0, ts.size - 1)
    equity = result.series["equity"].to_numpy()
    if benchmark is None:
        close = rows["close"]
        benchmark = close / close.first()
    strategy = equity[picked] - 1.0
    held = benchmark.to_numpy()[picked] - 1.0
    return [float(value) for value in strategy], [float(value) for value in held]


def main_benchmark(
    done: Run, held: Mapping[str, pl.DataFrame] = {}
) -> tuple[pl.Series, float | None]:
    """The equity on the run's rows and the total return of the backtest's benchmark, the one
    the list shows (``setup.benchmark``): the index fund of the first benchmark that holds one
    (``held``, its candles by the benchmark's ID, as ``results`` takes them), else buy and hold
    of the run's own instrument (also without a benchmark)."""
    prices = next(iter(held.values()), None)
    if prices is not None:
        return benchmark_equity(done.rows["ts"], prices)
    close = done.rows["close"]
    own = done.stats["buy_and_hold_return"]
    return close / close.first(), None if own is None else float(own)


def benchmark_equity(ts: pl.Series, prices: pl.DataFrame) -> tuple[pl.Series, float | None]:
    """The equity of buying another instrument at a run's first row and holding it, on the
    run's rows ``ts``, and its total return.

    On each row it is the instrument's last close at or before the row over the first such
    close: an as-of join, so a candle the instrument lacks repeats the one before. Rows before
    its first price are 1.0. Without a price at or before the last row the equity is 1.0
    throughout and the return None.

    Args:
        ts: The rows' ``ts``, in time order.
        prices: The instrument's candles in time order (``ts`` and ``close`` are read), of the
            run's timeframe and session, so their ``ts`` are normally the rows'.
    """
    closes = (
        ts.cast(pl.Int64)
        .to_frame("ts")
        .join_asof(
            prices.select(pl.col("ts").cast(pl.Int64), pl.col("close").cast(pl.Float64)),
            on="ts",
            strategy="backward",
        )
        .get_column("close")
    )
    first = closes.drop_nulls().first()
    if first is None:
        return pl.repeat(1.0, ts.len(), dtype=pl.Float64, eager=True), None
    equity = (closes / first).fill_null(1.0)
    last = equity.last()
    assert isinstance(last, float)  # the last row has a price, as the first does
    return equity, last - 1.0


def results(
    done: Run,
    benchmarks: list[BenchmarkIn],
    *,
    light: bool,
    held: Mapping[str, pl.DataFrame] = {},
) -> dict[str, Any]:
    """The ``results`` group: the statistics, each benchmark's return, the series and the
    trades, the long columns as NumPy arrays for ``dumps``. ``light`` is the view of the page
    and the API (docs/engine.md, "The light view"); the download has every row and trade.

    ``held`` has the candles of each benchmark that holds an index fund, by its ID
    (``benchmark_equity``); the others are buy and hold of the run's own instrument."""
    equities: dict[str, pl.Series] = {}
    returns: dict[str, float | int | None] = {}
    for benchmark in benchmarks:
        prices = held.get(benchmark.id)
        if prices is None:
            close = done.rows["close"]
            equities[benchmark.id] = close / close.first()
            returns[benchmark.id] = done.stats["buy_and_hold_return"]
        else:
            equities[benchmark.id], returns[benchmark.id] = benchmark_equity(
                done.rows["ts"], prices
            )
    return {
        "stats": stats_out(done.stats),
        "benchmarks": {benchmark: {"total_return": value} for benchmark, value in returns.items()},
        "series": _series(done, equities, light=light),
        "trades": _trades(done, light=light),
    }


def _series(done: Run, benchmarks: Mapping[str, pl.Series], *, light: bool) -> dict[str, Any]:
    rows, series = done.rows, done.result.series
    # A benchmark's ID may be the name of a column: its equity goes by another.
    named = {f"benchmark:{benchmark}": equity for benchmark, equity in benchmarks.items()}
    frame = pl.DataFrame(
        {
            "ts": series["ts"],
            "close": rows["close"],
            "prediction": rows["prediction"].cast(pl.Float64),
            "signal": series["signal"],
            "position": series["position"],
            "equity": series["equity"],
            "drawdown": drawdown(series),
            **named,
        }
    )
    resolution = "candle"
    if light and frame.height > LIGHT_ROWS:
        # One point per UTC day: the days Sharpe and Sortino come from, and the lowest
        # drawdown of each, so the chart and the statistics agree.
        resolution = "day"
        frame = frame.group_by(pl.col("ts") // _DAY, maintain_order=True).agg(
            pl.col("ts").last().alias("last_ts"),
            *(pl.col(name).last() for name in ("close", "position", "equity", *named)),
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
        "benchmarks": {
            benchmark: frame[f"benchmark:{benchmark}"].to_numpy() for benchmark in benchmarks
        },
    }


def _trades(done: Run, *, light: bool) -> dict[str, Any]:
    trades = done.result.trades
    total = trades.height
    if light and total > LIGHT_TRADES:
        trades = trades.tail(LIGHT_TRADES)
    index = done.result.series.select("ts").with_row_index("row")
    # Polars keeps no row order in a join unless asked: the trades stay in theirs.
    held = (
        trades.select("open_ts", "close_ts")
        .join(
            index.rename({"ts": "open_ts", "row": "opened"}),
            on="open_ts",
            how="left",
            maintain_order="left",
        )
        .join(
            index.rename({"ts": "close_ts", "row": "closed"}),
            on="close_ts",
            how="left",
            maintain_order="left",
        )
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


def curves_parquet(done: Run) -> bytes:
    """The stored form of a run's curves: the ``CURVES`` columns of its light series, as its
    document has them (a point per UTC day past ``LIGHT_ROWS`` rows), as zstd Parquet."""
    columns = _series(done, {}, light=True)["columns"]
    buffer = io.BytesIO()
    pl.DataFrame({name: columns[name] for name in CURVES}).write_parquet(buffer, compression="zstd")
    return buffer.getvalue()


def read_curves(blob: bytes) -> dict[str, list[Any]]:
    """The stored curves by column, a value that is not a number as None, as the document's
    JSON has it."""
    frame = pl.read_parquet(io.BytesIO(blob))
    frame = frame.with_columns(pl.col(pl.Float32, pl.Float64).fill_nan(None))
    return {name: frame[name].to_list() for name in CURVES}
