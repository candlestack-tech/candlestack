"""The statistics of the trades of one backtest run (``trade_stats``).

They read the engine's output (``trades`` and ``series``, see the engine's ``Result``), as
``stats`` does, and describe the trades themselves where ``stats`` gives the run's totals.
Returns are fractions (0.05 is 5%).
"""

import itertools
from typing import Any

import polars as pl

from candlestack.metrics.summary import _defined

# The engine's exit reasons, in the order of its rules. A count is given for each, 0 included,
# so every run has the same names.
_EXIT_REASONS = ("signal", "stop", "gap", "end", "ruin", "drawdown")


def trade_stats(trades: pl.DataFrame, series: pl.DataFrame) -> dict[str, float | int | None]:
    """The statistics of a run's trades, by name, always in the order below.

    A trade is a win when its ``return`` is above 0 and a loss otherwise, as in ``stats``: a
    trade that returns exactly 0 is no win.

    - ``profit_factor = sum(return > 0) / -sum(return < 0)``: the gross profit over the gross
      loss, 0 when no trade wins.
    - ``expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss``: the return a trade is
      expected to bring, ``avg_win`` and ``avg_loss`` the mean return of the wins and of the
      losses. It is the mean return of all trades.
    - ``avg_holding_candles`` and ``median_holding_candles``: the mean and median of the rows of
      ``series`` from a trade's entry candle (``open_ts``) to its exit candle (``close_ts``).
      A trade opened and closed on the same candle holds 0.
    - ``longest_win_streak`` and ``longest_loss_streak``: the most wins and the most losses in
      a row, in the order of ``trades`` (ints, 0 without trades).
    - ``long_trades``, ``long_win_rate``, ``long_avg_return`` and the same for ``short``: the
      count (an int), the share of wins and the mean return of the trades of that side.
    - ``exits_<reason>`` for each ``exit_reason`` of the engine (``signal``, ``stop``, ``gap``,
      ``end``, ``ruin`` and ``drawdown``): the count of trades that left for it (ints).

    A value that is undefined is None, never NaN, so the result is valid JSON: everything but
    the counts and streaks without trades, ``profit_factor`` without a losing trade (there is
    no loss to divide by), the rates and averages of a side without trades of that side, and
    any value that would come out infinite or NaN.

    Args:
        trades: The engine's trades, in time order; ``open_ts``, ``close_ts``, ``side``,
            ``exit_reason`` and ``return`` are read.
        series: The engine's series of the same run, in time order; only ``ts`` is read, to
            count the candles a trade was held.

    Returns:
        ``profit_factor``, ``expectancy``, ``avg_holding_candles``, ``median_holding_candles``
        (floats or None), ``longest_win_streak``, ``longest_loss_streak``, ``long_trades`` (ints),
        ``long_win_rate``, ``long_avg_return`` (floats or None), ``short_trades`` (an int),
        ``short_win_rate``, ``short_avg_return`` (floats or None) and ``exits_signal``,
        ``exits_stop``, ``exits_gap``, ``exits_end``, ``exits_ruin``, ``exits_drawdown`` (ints).
    """
    returns = trades.get_column("return")
    win = pl.col("return") > 0
    totals = trades.select(
        profit=pl.col("return").filter(win).sum(),
        loss=-pl.col("return").filter(pl.col("return") < 0).sum(),
        losses=(pl.col("return") < 0).sum(),
        win_rate=win.mean(),
        avg_win=pl.col("return").filter(win).mean(),
        avg_loss=pl.col("return").filter(~win).mean(),
    ).row(0, named=True)
    holding = _holding_candles(trades, series)
    win_streak, loss_streak = _longest_streaks([value > 0 for value in returns])
    values: dict[str, Any] = {
        "profit_factor": totals["profit"] / totals["loss"] if totals["losses"] else None,
        "expectancy": _expectancy(totals["win_rate"], totals["avg_win"], totals["avg_loss"]),
        "avg_holding_candles": holding.mean(),
        "median_holding_candles": holding.median(),
        "longest_win_streak": win_streak,
        "longest_loss_streak": loss_streak,
    }
    for name, side in (("long", 1), ("short", -1)):
        of_side = trades.filter(pl.col("side") == side).select(
            count=pl.len(), win_rate=win.mean(), avg_return=pl.col("return").mean()
        )
        count, win_rate, avg_return = of_side.row(0)
        values |= {
            f"{name}_trades": count,
            f"{name}_win_rate": win_rate,
            f"{name}_avg_return": avg_return,
        }
    reasons = trades.get_column("exit_reason").to_list()
    values |= {f"exits_{reason}": reasons.count(reason) for reason in _EXIT_REASONS}
    return {name: _defined(value) for name, value in values.items()}


def _holding_candles(trades: pl.DataFrame, series: pl.DataFrame) -> pl.Series:
    """The rows of ``series`` from each trade's entry candle to its exit candle."""
    rows = series.select("ts").with_row_index("row").with_columns(pl.col("row").cast(pl.Int64))
    held = trades.select("open_ts", "close_ts").join(
        rows.rename({"ts": "open_ts", "row": "open_row"}), on="open_ts", how="left"
    )
    held = held.join(rows.rename({"ts": "close_ts", "row": "close_row"}), on="close_ts", how="left")
    return held.select(pl.col("close_row") - pl.col("open_row")).to_series()


def _expectancy(win_rate: float | None, avg_win: float | None, avg_loss: float | None) -> Any:
    """``win_rate * avg_win + (1 - win_rate) * avg_loss``; a side without trades adds 0."""
    if win_rate is None:
        return None
    return win_rate * (avg_win or 0.0) + (1 - win_rate) * (avg_loss or 0.0)


def _longest_streaks(wins: list[bool]) -> tuple[int, int]:
    """The longest run of True and the longest run of False in ``wins``, 0 for none."""
    longest = {True: 0, False: 0}
    for won, run in itertools.groupby(wins):
        longest[won] = max(longest[won], len(list(run)))
    return longest[True], longest[False]
