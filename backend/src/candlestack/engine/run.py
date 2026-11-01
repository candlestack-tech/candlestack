"""``backtest``: candles and a signal in, trades and an equity series out."""

from dataclasses import dataclass

import polars as pl

from candlestack.engine.inputs import read
from candlestack.engine.loop import Loop
from candlestack.engine.settings import Settings
from candlestack.engine.version import ENGINE_VERSION

TRADES_SCHEMA = pl.Schema(
    {
        "signal_ts": pl.Int64,  # the candle whose close made the decision
        "open_ts": pl.Int64,
        "close_ts": pl.Int64,
        "side": pl.Int8,  # +1 long, -1 short
        "open_price": pl.Float64,  # fill prices, slippage included
        "close_price": pl.Float64,
        "stop_price": pl.Float64,  # the stop level; null without a stop
        "exit_reason": pl.String,  # signal, stop, gap, end, ruin or drawdown
        "return": pl.Float64,  # capital after / capital before - 1, costs and size included
        "fees": pl.Float64,  # the fees of both legs, as a fraction of the capital before
        "slippage_cost": pl.Float64,  # what slippage cost, as a fraction of the capital before
    }
)

SERIES_SCHEMA = pl.Schema(
    {
        "ts": pl.Int64,
        "signal": pl.Float64,
        "position": pl.Float64,  # -1, 0 or +1 after the candle's fills
        "equity": pl.Float64,  # capital after the candle's fills, position marked at its close
    }
)


@dataclass(frozen=True)
class Result:
    """One backtest run: ``trades`` (``TRADES_SCHEMA``, one row per closed trade, by open
    time), ``series`` (``SERIES_SCHEMA``, one row per input candle) and the
    ``engine_version`` of the rules that produced them."""

    trades: pl.DataFrame
    series: pl.DataFrame
    engine_version: str


def backtest(frame: pl.DataFrame, settings: Settings) -> Result:
    """Runs the engine's rules (see ``loop``) over ``frame``: one row per candle, sorted by
    ``ts`` (the open time, UTC epoch seconds), with ``open``, ``high``, ``low``, ``close`` and
    ``signal`` (-1, 0 or +1). Other columns are ignored.

    Raises:
        InvalidInput: ``frame`` breaks this contract; the message says how.
    """
    candles = read(frame)
    loop = Loop(candles, settings)
    loop.run()
    trades = pl.DataFrame(loop.trades, schema=TRADES_SCHEMA, orient="row")
    series = candles.frame.select(
        "ts",
        "signal",
        pl.Series("position", loop.position, dtype=pl.Float64),
        pl.Series("equity", loop.equity, dtype=pl.Float64),
    )
    return Result(trades=trades, series=series, engine_version=ENGINE_VERSION)
