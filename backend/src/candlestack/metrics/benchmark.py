"""A run against a benchmark (``benchmark_stats``) and its returns by month (``monthly_returns``).

Both read equity series like the engine's ``series``: ``ts`` and ``equity``, in time order, the
capital starting at 1.0. A benchmark is any such series, for example holding SPY over the
candles of the run. Returns are fractions (0.05 is 5%).
"""

import math
import statistics
from typing import Any

import polars as pl

from candlestack.metrics.summary import _DAY, _defined


def benchmark_stats(
    strategy: pl.DataFrame,
    benchmark: pl.DataFrame,
    periods_per_year: float = 252,
) -> dict[str, float | None]:
    """The run against a benchmark on the same timestamps, by name, always in the order below.

    Daily returns are taken as ``stats`` takes them: the day of a row is the UTC date of its
    ``ts``, ``E[d]`` is the equity of the last row of day ``d`` and ``r[d] = E[d] / E[d-1] - 1``,
    the first day's from the starting capital 1.0. ``s`` are the strategy's daily returns, ``b``
    the benchmark's, ``a = s - b`` the active returns; ``var``, ``cov`` and ``std`` take ddof 1,
    and the risk-free rate is 0.

    - ``excess_return = equity[last] - benchmark_equity[last]``: the strategy's total return
      minus the benchmark's.
    - ``beta = cov(s, b) / var(b)``: how much the strategy moves with the benchmark.
    - ``alpha = (mean(s) - beta * mean(b)) * periods_per_year``: the return a year the strategy
      makes beyond what its beta explains.
    - ``correlation = cov(s, b) / (std(s) * std(b))``.
    - ``tracking_error = std(a) * sqrt(periods_per_year)``: how far the strategy strays from the
      benchmark in a year.
    - ``information_ratio = mean(a) / std(a) * sqrt(periods_per_year)``: the excess return for
      each unit of tracking error.
    - ``positive_months``: the share of the strategy's months with a return above 0 (see
      ``monthly_returns``).

    A value that is undefined is None, never NaN: ``excess_return`` without rows; the daily
    values with fewer than 2 days or once either equity has reached 0 (a day that starts from 0
    has no return); ``beta`` and ``alpha`` when the benchmark does not move (``var(b)`` is 0),
    ``correlation`` when either does not move, ``information_ratio`` when the strategy follows
    the benchmark exactly (``std(a)`` is 0); ``positive_months`` without a month that has a
    return; and any value that would come out infinite or NaN.

    Args:
        strategy: The run's series, in time order; ``ts`` and ``equity`` are read.
        benchmark: The benchmark's equity, in time order, with the same ``ts`` as ``strategy``;
            ``ts`` and ``equity`` are read.
        periods_per_year: Days per year that scale the daily values to a year: 252 trading days
            for stocks, 365 for crypto.

    Returns:
        ``excess_return``, ``beta``, ``alpha``, ``correlation``, ``tracking_error``,
        ``information_ratio`` and ``positive_months`` (floats or None).

    Raises:
        ValueError: The two series do not have the same ``ts``.
    """
    if not strategy.get_column("ts").equals(benchmark.get_column("ts")):
        raise ValueError("The strategy and the benchmark must have the same ts, in the same order")
    ends = pl.DataFrame(
        {
            "ts": strategy.get_column("ts"),
            "s": strategy.get_column("equity"),
            "b": benchmark.get_column("equity"),
        }
    )
    pairs = _daily_returns(ends)
    months = monthly_returns(strategy).get_column("return").drop_nulls()
    values: dict[str, Any] = {
        "excess_return": ends.select(pl.col("s").last() - pl.col("b").last()).item(),
        **_daily_values(pairs, periods_per_year),
        "positive_months": (months > 0).mean() if len(months) else None,
    }
    return {name: _defined(value) for name, value in values.items()}


def monthly_returns(series: pl.DataFrame) -> pl.DataFrame:
    """The return of each UTC calendar month that has rows.

    ``r[m] = E[m] / E[m-1] - 1``, ``E[m]`` the equity of the last row of month ``m``, the first
    month's from the starting capital 1.0. Months without rows are skipped. A month that starts
    from an equity of 0 has no return (null).

    Args:
        series: An equity series in time order; ``ts`` and ``equity`` are read.

    Returns:
        A frame with ``month`` (String, ``YYYY-MM``) and ``return`` (Float64), one row per month.
    """
    ends = series.group_by(
        pl.from_epoch("ts").dt.strftime("%Y-%m").alias("month"), maintain_order=True
    ).agg(pl.col("equity").last())
    start = pl.col("equity").shift(1, fill_value=1.0)
    return ends.select(
        "month",
        pl.when(start != 0).then(pl.col("equity") / start - 1).alias("return"),
    )


def _daily_returns(ends: pl.DataFrame) -> list[tuple[float, float]]:
    """``(s[d], b[d])`` for each UTC date that has rows, from the equities ``s`` and ``b``.
    Empty once either equity has reached 0: no later day has a return."""
    days = ends.group_by(pl.col("ts") // _DAY, maintain_order=True).agg(
        pl.col("s").last(), pl.col("b").last()
    )
    s = [1.0, *days.get_column("s").to_list()]
    b = [1.0, *days.get_column("b").to_list()]
    if 0 in s[:-1] or 0 in b[:-1]:
        return []
    return [(s[i + 1] / s[i] - 1, b[i + 1] / b[i] - 1) for i in range(len(s) - 1)]


def _daily_values(pairs: list[tuple[float, float]], periods_per_year: float) -> dict[str, Any]:
    """Beta, alpha, correlation, tracking error and information ratio of the daily returns."""
    values: dict[str, Any] = dict.fromkeys(
        ["beta", "alpha", "correlation", "tracking_error", "information_ratio"]
    )
    if len(pairs) < 2:
        return values
    s = [pair[0] for pair in pairs]
    b = [pair[1] for pair in pairs]
    active = [x - y for x, y in pairs]
    covariance = statistics.covariance(s, b)
    spread_s, spread_b = statistics.stdev(s), statistics.stdev(b)
    tracking = statistics.stdev(active)
    if spread_b != 0:
        values["beta"] = covariance / spread_b**2
        values["alpha"] = (
            statistics.fmean(s) - values["beta"] * statistics.fmean(b)
        ) * periods_per_year
    if spread_s != 0 and spread_b != 0:
        values["correlation"] = covariance / (spread_s * spread_b)
    values["tracking_error"] = tracking * math.sqrt(periods_per_year)
    if tracking != 0:
        values["information_ratio"] = (
            statistics.fmean(active) / tracking * math.sqrt(periods_per_year)
        )
    return values
