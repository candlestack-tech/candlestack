import json
import math
from datetime import UTC, datetime

import polars as pl
import pytest

from candlestack.metrics import benchmark_stats, monthly_returns

# The engine's series as the data contract defines it; the benchmark is a series of the same shape.
SERIES_SCHEMA = {"ts": pl.Int64, "signal": pl.Float64, "position": pl.Float64, "equity": pl.Float64}
NAMES = [
    "excess_return",
    "beta",
    "alpha",
    "correlation",
    "tracking_error",
    "information_ratio",
    "positive_months",
]
DAILY_NAMES = ["beta", "alpha", "correlation", "tracking_error", "information_ratio"]


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def make_series(*rows: tuple[str, float]) -> pl.DataFrame:
    """A series from ``(UTC time, equity)``; the metrics read only ``ts`` and ``equity``."""
    return pl.DataFrame(
        [(utc(time), 0.0, 0.0, equity) for time, equity in rows],
        schema=SERIES_SCHEMA,
        orient="row",
    )


def assert_valid_json(result: dict[str, float | None]) -> None:
    json.dumps(result, allow_nan=False)  # raises on NaN and infinity


# Three days, each in its own month, so the daily and the monthly returns are the same.
# Strategy: 0.1, -0.1, 0.1 (mean 1/30, var 1/75). Benchmark: 0.05, -0.05, 0 (mean 0, var 0.0025).
# cov = (0.2/3 * 0.05 + 0.4/3 * 0.05 + 0) / 2 = 0.005. Active returns 0.05, -0.05, 0.1:
# mean 1/30, var 7/1200.
TIMES = ["2026-01-30 20:00", "2026-02-02 20:00", "2026-03-02 20:00"]
STRATEGY = make_series(*zip(TIMES, [1.1, 0.99, 1.089], strict=True))
BENCHMARK = make_series(*zip(TIMES, [1.05, 0.9975, 0.9975], strict=True))


def test_benchmark_stats_match_the_hand_computed_values() -> None:
    result = benchmark_stats(STRATEGY, BENCHMARK)

    assert list(result) == NAMES
    assert result == {
        "excess_return": pytest.approx(1.089 - 0.9975),
        # cov over var(b): 0.005 / 0.0025.
        "beta": pytest.approx(2.0),
        # The mean strategy return beyond beta times the mean benchmark return, a year: 1/30 * 252.
        "alpha": pytest.approx(8.4),
        # cov over both deviations: 0.005 / (0.05 / sqrt(75)) is sqrt(3) / 2.
        "correlation": pytest.approx(math.sqrt(3) / 2),
        # The active deviation a year: the root of 7/1200 times 252.
        "tracking_error": pytest.approx(math.sqrt(1.47)),
        # The mean active return over its deviation, a year: the root of 48.
        "information_ratio": pytest.approx(math.sqrt(48)),
        "positive_months": pytest.approx(2 / 3),
    }


def test_periods_per_year_scales_the_yearly_values() -> None:
    result = benchmark_stats(STRATEGY, BENCHMARK, periods_per_year=365)

    assert result["alpha"] == pytest.approx(365 / 30)
    assert result["tracking_error"] == pytest.approx(math.sqrt(7 / 1200 * 365))
    assert result["information_ratio"] == pytest.approx(math.sqrt(48 * 365 / 252))
    assert result["beta"] == pytest.approx(2.0)


def test_a_benchmark_equal_to_the_strategy() -> None:
    result = benchmark_stats(STRATEGY, STRATEGY)

    assert result == {
        "excess_return": 0.0,
        "beta": pytest.approx(1.0),
        "alpha": pytest.approx(0.0),
        "correlation": pytest.approx(1.0),
        "tracking_error": 0.0,
        "information_ratio": None,  # no tracking error to divide by
        "positive_months": pytest.approx(2 / 3),
    }
    assert_valid_json(result)


def test_a_flat_benchmark_has_no_beta_alpha_or_correlation() -> None:
    flat = make_series(*((time, 1.0) for time in TIMES))

    result = benchmark_stats(STRATEGY, flat)

    assert (result["beta"], result["alpha"], result["correlation"]) == (None, None, None)
    # The active returns are the strategy's: std sqrt(1/75), mean 1/30.
    assert result["tracking_error"] == pytest.approx(math.sqrt(252 / 75))
    assert result["information_ratio"] == pytest.approx(math.sqrt(21))
    assert result["excess_return"] == pytest.approx(0.089)


def test_a_day_is_a_utc_date_and_its_last_row_ends_it() -> None:
    # The day of 5 January ends at its last row, so the returns are 0.2 and -0.1 for the
    # strategy and 0.1 and 0 for the benchmark: cov 0.015 over var(b) 0.005 is a beta of 3.
    strategy = make_series(
        ("2026-01-05 14:00", 0.9), ("2026-01-05 20:00", 1.2), ("2026-01-06 20:00", 1.08)
    )
    benchmark = make_series(
        ("2026-01-05 14:00", 1.3), ("2026-01-05 20:00", 1.1), ("2026-01-06 20:00", 1.1)
    )

    result = benchmark_stats(strategy, benchmark)

    assert result["beta"] == pytest.approx(3.0)
    assert result["correlation"] == pytest.approx(1.0)


def test_one_day_has_only_the_excess_return() -> None:
    strategy = make_series(("2026-01-05 14:00", 1.1), ("2026-01-05 20:00", 1.2))
    benchmark = make_series(("2026-01-05 14:00", 1.0), ("2026-01-05 20:00", 1.05))

    result = benchmark_stats(strategy, benchmark)

    assert result["excess_return"] == pytest.approx(0.15)
    assert [result[name] for name in DAILY_NAMES] == [None] * 5
    assert result["positive_months"] == 1.0


def test_after_equity_reaches_zero_there_are_no_daily_values() -> None:
    ruined = make_series(*zip(TIMES, [0.5, 0.0, 0.0], strict=True))

    result = benchmark_stats(ruined, BENCHMARK)

    assert [result[name] for name in DAILY_NAMES] == [None] * 5
    # January lost, then no month has a return: 0 of 1.
    assert result["positive_months"] == 0.0
    assert result["excess_return"] == pytest.approx(-0.9975)


def test_empty_series_have_no_values() -> None:
    empty = make_series()

    result = benchmark_stats(empty, empty)

    assert result == dict.fromkeys(NAMES)
    assert_valid_json(result)


def test_series_on_other_timestamps_are_refused() -> None:
    shifted = make_series(*zip(TIMES[:2], [1.0, 1.0], strict=True))

    with pytest.raises(ValueError, match="same ts"):
        benchmark_stats(STRATEGY, shifted)


# Monthly returns


def test_monthly_returns_run_from_the_last_row_of_each_month() -> None:
    series = make_series(
        ("2026-01-05 14:00", 1.3),
        ("2026-01-30 20:00", 1.1),
        ("2026-03-02 20:00", 0.99),
        ("2026-03-31 23:59", 1.21),
    )

    result = monthly_returns(series)

    # January from the starting 1.0; February has no rows; March from January's 1.1.
    assert result.get_column("month").to_list() == ["2026-01", "2026-03"]
    assert result.get_column("return").to_list() == pytest.approx([0.1, 0.1])
    assert result.schema == pl.Schema({"month": pl.String, "return": pl.Float64})


def test_a_month_that_starts_from_zero_has_no_return() -> None:
    series = make_series(*zip(TIMES, [0.0, 0.0, 0.0], strict=True))

    assert monthly_returns(series).get_column("return").to_list() == [-1.0, None, None]


def test_monthly_returns_of_an_empty_series_are_empty() -> None:
    assert monthly_returns(make_series()).is_empty()
