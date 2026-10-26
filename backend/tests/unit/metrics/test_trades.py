import json
from datetime import UTC, datetime

import polars as pl
import pytest

from candlestack.metrics import trade_stats

# The engine's output, as the data contract defines it.
TRADES_SCHEMA = {
    "signal_ts": pl.Int64,
    "open_ts": pl.Int64,
    "close_ts": pl.Int64,
    "side": pl.Int8,
    "open_price": pl.Float64,
    "close_price": pl.Float64,
    "stop_price": pl.Float64,
    "exit_reason": pl.String,
    "return": pl.Float64,
    "fees": pl.Float64,
}
SERIES_SCHEMA = {"ts": pl.Int64, "signal": pl.Float64, "position": pl.Float64, "equity": pl.Float64}
HOUR = 3600
NAMES = [
    "profit_factor",
    "expectancy",
    "avg_holding_candles",
    "median_holding_candles",
    "longest_win_streak",
    "longest_loss_streak",
    "long_trades",
    "long_win_rate",
    "long_avg_return",
    "short_trades",
    "short_win_rate",
    "short_avg_return",
    "exits_signal",
    "exits_stop",
    "exits_gap",
    "exits_end",
    "exits_ruin",
    "exits_drawdown",
]
COUNTS = [
    "longest_win_streak",
    "longest_loss_streak",
    "long_trades",
    "short_trades",
    *(name for name in NAMES if name.startswith("exits_")),
]


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


START = utc("2026-01-05 14:00")


def make_series(rows: int, start: int = START, step: int = HOUR) -> pl.DataFrame:
    """``rows`` candles ``step`` seconds apart; trade_stats reads only ``ts``."""
    return pl.DataFrame(
        [(start + i * step, 0.0, 0.0, 1.0) for i in range(rows)],
        schema=SERIES_SCHEMA,
        orient="row",
    )


def make_trades(series: pl.DataFrame, *rows: tuple[int, int, int, str, float]) -> pl.DataFrame:
    """Closed trades from ``(entry row, exit row, side, exit_reason, return)``, the rows of
    ``series``; the prices and fees are filler."""
    ts = series.get_column("ts").to_list()
    return pl.DataFrame(
        [
            (ts[entry], ts[entry], ts[exit_], side, 100.0, 100.0, None, reason, result, 0.001)
            for entry, exit_, side, reason, result in rows
        ],
        schema=TRADES_SCHEMA,
        orient="row",
    )


def assert_valid_json(result: dict[str, float | int | None]) -> None:
    json.dumps(result, allow_nan=False)  # raises on NaN and infinity


# Five trades over eight hourly candles: a flip on row 2 (the long closes and the short opens
# on the same candle), a gap exit opened and closed on row 3, a trade that returns exactly 0
# (no win) and the end trade. Wins: yes, no, no, no, yes.
SERIES = make_series(8)
TRADES = make_trades(
    SERIES,
    (0, 2, 1, "signal", 0.10),
    (2, 3, -1, "stop", -0.05),
    (3, 3, 1, "gap", -0.02),
    (4, 6, 1, "signal", 0.0),
    (6, 7, -1, "end", 0.06),
)


def test_trade_stats_of_a_run_match_the_hand_computed_values() -> None:
    result = trade_stats(TRADES, SERIES)

    assert list(result) == NAMES
    assert result == {
        # Gross profit 0.10 + 0.06 over gross loss 0.05 + 0.02.
        "profit_factor": pytest.approx(16 / 7),
        # Wins 2 of 5 averaging 0.08, the others (-0.05 - 0.02 + 0) / 3:
        # 0.4 * 0.08 + 0.6 * (-0.07 / 3) = 0.032 - 0.014, the mean return 0.09 / 5.
        "expectancy": pytest.approx(0.018),
        # Held 2, 1, 0, 2 and 1 candles.
        "avg_holding_candles": pytest.approx(1.2),
        "median_holding_candles": 1.0,
        "longest_win_streak": 1,
        # The stop, the gap and the trade that returns 0.
        "longest_loss_streak": 3,
        "long_trades": 3,
        "long_win_rate": pytest.approx(1 / 3),
        "long_avg_return": pytest.approx(0.08 / 3),
        "short_trades": 2,
        "short_win_rate": 0.5,
        "short_avg_return": pytest.approx(0.005),
        "exits_signal": 2,
        "exits_stop": 1,
        "exits_gap": 1,
        "exits_end": 1,
        "exits_ruin": 0,
        "exits_drawdown": 0,
    }
    assert all(isinstance(result[name], int) for name in COUNTS)


def test_streaks_are_the_longest_runs_in_trade_order() -> None:
    series = make_series(9)
    returns = [0.01, 0.02, -0.01, 0.03, 0.04, 0.05, -0.02, -0.03]
    trades = make_trades(
        series, *((i, i + 1, 1, "signal", result) for i, result in enumerate(returns))
    )

    result = trade_stats(trades, series)

    assert (result["longest_win_streak"], result["longest_loss_streak"]) == (3, 2)


def test_holding_time_counts_candles_not_time() -> None:
    # Two candles a session apart: an overnight gap still is one candle.
    series = pl.concat([make_series(2), make_series(2, start=utc("2026-01-06 14:00"))])
    trades = make_trades(series, (0, 3, 1, "signal", 0.01))

    result = trade_stats(trades, series)

    assert result["avg_holding_candles"] == 3.0
    assert result["median_holding_candles"] == 3.0


def test_the_median_of_an_even_count_is_the_mean_of_the_middle_two() -> None:
    series = make_series(12)
    trades = make_trades(
        series,
        (0, 1, 1, "signal", 0.01),
        (1, 3, 1, "signal", 0.01),
        (3, 6, 1, "signal", 0.01),
        (6, 11, 1, "signal", 0.01),
    )

    result = trade_stats(trades, series)

    # Held 1, 2, 3 and 5 candles.
    assert result["median_holding_candles"] == 2.5
    assert result["avg_holding_candles"] == pytest.approx(2.75)


def test_without_trades_only_the_counts_are_defined() -> None:
    result = trade_stats(make_trades(SERIES), SERIES)

    assert list(result) == NAMES
    assert {name: value for name, value in result.items() if name in COUNTS} == dict.fromkeys(
        COUNTS, 0
    )
    assert [name for name, value in result.items() if value is not None] == COUNTS
    assert_valid_json(result)


def test_one_winning_trade_has_no_profit_factor() -> None:
    trades = make_trades(SERIES, (1, 4, 1, "end", 0.05))

    result = trade_stats(trades, SERIES)

    assert result == {
        "profit_factor": None,  # no loss to divide by
        "expectancy": 0.05,
        "avg_holding_candles": 3.0,
        "median_holding_candles": 3.0,
        "longest_win_streak": 1,
        "longest_loss_streak": 0,
        "long_trades": 1,
        "long_win_rate": 1.0,
        "long_avg_return": 0.05,
        "short_trades": 0,
        "short_win_rate": None,
        "short_avg_return": None,
        "exits_signal": 0,
        "exits_stop": 0,
        "exits_gap": 0,
        "exits_end": 1,
        "exits_ruin": 0,
        "exits_drawdown": 0,
    }
    assert_valid_json(result)


def test_one_losing_trade_has_a_profit_factor_of_zero() -> None:
    trades = make_trades(SERIES, (2, 5, -1, "ruin", -1.2))

    result = trade_stats(trades, SERIES)

    assert result["profit_factor"] == 0.0
    assert result["expectancy"] == -1.2
    assert (result["longest_win_streak"], result["longest_loss_streak"]) == (0, 1)
    assert (result["short_trades"], result["short_win_rate"]) == (1, 0.0)
    assert (result["long_win_rate"], result["long_avg_return"]) == (None, None)
    assert result["exits_ruin"] == 1


def test_trades_that_return_zero_have_no_profit_factor() -> None:
    # No win and no loss: 0 / 0 has no meaning.
    trades = make_trades(SERIES, (0, 1, 1, "drawdown", 0.0), (1, 2, 1, "signal", 0.0))

    result = trade_stats(trades, SERIES)

    assert result["profit_factor"] is None
    assert result["expectancy"] == 0.0
    assert (result["longest_win_streak"], result["longest_loss_streak"]) == (0, 2)
    assert result["exits_drawdown"] == 1
