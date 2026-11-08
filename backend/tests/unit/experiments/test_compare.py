"""The comparison of backtests: the marking rules against the cases of the Compare page's tests
(frontend/src/pages/ComparePage.test.tsx), so the app and the API agree, the setups and what
differs, and the curves on one time axis (chartLines.ts)."""

from collections.abc import Sequence
from typing import Any

import pytest

from candlestack.experiments.compare import (
    CURVE_POINTS,
    compared,
    differing,
    metric_rows,
    parse_ids,
    setup_of,
    split_setup,
    time_axis,
    value_at,
)
from candlestack.experiments.errors import InvalidBacktest

# The page's tests' statistics.
STATS: dict[str, Any] = {
    "total_return": 0.5,
    "max_drawdown": -0.1,
    "sharpe": 1,
    "sortino": 1.5,
    "num_trades": 10,
    "win_rate": 0.5,
    "avg_trade_return": 0.01,
    "best_trade_return": 0.1,
    "worst_trade_return": -0.07,
    "exposure": 0.4,
    "fees_paid": 0.02,
    "buy_and_hold_return": 0.3,
}
BETTER = {
    **STATS,
    "total_return": 0.9,
    "max_drawdown": -0.05,
    "sharpe": None,
    "fees_paid": 0.01,
    "num_trades": 99,
}
WORSE = {
    **STATS,
    "total_return": 0.1,
    "max_drawdown": -0.3,
    "sharpe": None,
    "fees_paid": 0.05,
    "num_trades": 1,
}
MIDDLE = STATS


def marks(stats: list[dict[str, Any] | None], key: str) -> list[Any]:
    return next(row.marks for row in metric_rows(stats) if row.key == key)


# --- The best and the worst -------------------------------------------------------------------


def test_the_best_and_the_worst_of_a_row_are_marked_the_rest_plain() -> None:
    stats: list[dict[str, Any] | None] = [BETTER, WORSE, MIDDLE]

    assert marks(stats, "total_return") == ["best", "worst", None]
    # A fall is better the smaller it is, however it is signed; so are the fees.
    assert marks(stats, "max_drawdown") == ["best", "worst", None]
    assert marks(stats, "fees_paid") == ["best", "worst", None]


def test_nothing_is_marked_when_equal_or_none_is_better_or_one_is_known() -> None:
    rows = {row.key: row for row in metric_rows([BETTER, WORSE, MIDDLE])}

    assert rows["win_rate"].marks == [None, None, None]
    assert rows["num_trades"].marks == [None, None, None]
    assert rows["num_trades"].better == "none"
    assert rows["sharpe"].values == [None, None, 1]
    assert rows["sharpe"].marks == [None, None, None]


def test_a_single_backtest_marks_nothing() -> None:
    assert marks([BETTER], "total_return") == [None]


def test_a_small_average_counts_to_two_decimals() -> None:
    small = {**STATS, "avg_trade_return": 0.0041, "max_drawdown": 0}
    close = {**STATS, "avg_trade_return": 0.0044, "max_drawdown": -0.1}

    assert marks([small, close], "avg_trade_return") == ["worst", "best"]
    assert marks([small, close], "max_drawdown") == ["best", "worst"]


def test_values_that_read_the_same_count_as_equal() -> None:
    # +12.3 % both: marked alike, never told apart.
    stats: list[dict[str, Any] | None] = [
        {**STATS, "total_return": 0.1234},
        {**STATS, "total_return": 0.1226},
        {**STATS, "total_return": 0.05},
    ]

    assert marks(stats, "total_return") == ["best", "best", "worst"]
    assert marks(stats[:2], "total_return") == [None, None]
    # A loss of 0.004 % reads as +0.0 %, as a gain of 0.00004 % does.
    assert marks(
        [{**STATS, "total_return": -0.00004}, {**STATS, "total_return": 0.0000004}], "total_return"
    ) == [None, None]
    # A Sharpe ratio to two decimals, half away from zero as the page rounds: 1.345 is 1.35.
    assert marks([{**STATS, "sharpe": 1.345}, {**STATS, "sharpe": 1.35}], "sharpe") == [None, None]


def test_a_backtest_without_results_has_no_values_and_no_marks() -> None:
    rows = metric_rows([BETTER, None, WORSE])

    for row in rows:
        assert row.values[1] is None, row.key
        assert row.marks[1] is None, row.key
    # In the order of the page's table.
    assert [row.key for row in rows] == [
        "total_return",
        "max_drawdown",
        "sharpe",
        "sortino",
        "win_rate",
        "avg_trade_return",
        "best_trade_return",
        "worst_trade_return",
        "fees_paid",
        "num_trades",
        "exposure",
        "buy_and_hold_return",
    ]
    total = rows[0]
    assert (total.label, total.better, total.marks) == (
        "Total return",
        "higher",
        ["best", None, "worst"],
    )


# --- The IDs ----------------------------------------------------------------------------------


def test_the_ids_are_separated_by_commas() -> None:
    assert parse_ids("bt_a, bt_b") == ["bt_a", "bt_b"]
    assert parse_ids("bt_a") == ["bt_a"]
    assert len(parse_ids(",".join(f"bt_{n}" for n in range(20)))) == 20


@pytest.mark.parametrize(
    ("ids", "says"),
    [
        (",".join(f"bt_{n}" for n in range(21)), "has 21"),
        ("bt_a,,bt_b", "empty"),
        ("bt_a,bt_a", "twice"),
    ],
)
def test_ids_out_of_bounds_are_refused(ids: str, says: str) -> None:
    with pytest.raises(InvalidBacktest, match=says):
        parse_ids(ids)


# --- The setups -------------------------------------------------------------------------------


def document(
    backtest_id: str = "bt_a",
    *,
    status: str = "finished",
    stats: dict[str, Any] | None = None,
    ts: list[int] | None = None,
    equity: Sequence[float | None] | None = None,
    close: list[float] | None = None,
    drawdown: Sequence[float | None] | None = None,
    resolution: str = "candle",
    limit: float = 0.1,
    calibrate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A result document with what the comparison reads of it; buy and hold is its closes'
    unless ``stats`` says otherwise."""
    ts = ts or [10, 20, 30]
    close = close or [100.0, 50.0, 200.0][: len(ts)]
    stats = stats or {**STATS, "buy_and_hold_return": close[-1] / close[0] - 1}
    finished = status == "finished"
    return {
        "backtest": {
            "id": backtest_id,
            "number": 1,
            "name": "Silent Orion",
            "author": {"id": "usr_a", "name": "Ada", "mine": True},
            "public": False,
            "status": status,
            "error": "It failed." if status == "failed" else None,
        },
        "model": {
            "name": "lstm.keras",
            "manifest": {
                "input": {"representation": {"id": "candles@1"}},
                "signature": {"input": {"shape": [25, 5]}},
            },
        },
        "data": {
            "instrument": {"id": "stock:AAPL"},
            "timeframe": "30m",
            "period": {"start": 0, "end": 100},
            "rows": {"first_ts": ts[0], "last_ts": ts[-1], "count": len(ts)},
        },
        "decision": {"calibrate": calibrate, "rule": {"name": "threshold", "limit": limit}},
        "execution": {"fee_bps": 5.0},
        "benchmarks": [{"id": "bh", "instrument": "stock:AAPL"}],
        "results": {
            "stats": stats,
            "series": {
                "resolution": resolution,
                "columns": {
                    "ts": ts,
                    "equity": equity or [1.0, 1.1, 1.2][: len(ts)],
                    "drawdown": drawdown or [0.0] * len(ts),
                    "close": close,
                },
            },
        }
        if finished
        else None,
    }


def test_the_setup_of_a_backtest() -> None:
    assert setup_of(document()) == {
        "instrument": "stock:AAPL",
        "model": "lstm.keras",
        "reads": "candles@1",
        "input": [25, 5],
        "timeframe": "30m",
        "period": {"start": 0, "end": 100},
        "decision": {"calibrate": None, "rule": {"name": "threshold", "limit": 0.1}},
        "execution": {"fee_bps": 5.0},
        "benchmarks": ["stock:AAPL"],
    }


def test_the_fields_that_differ_by_their_paths_one_only_some_have_included() -> None:
    setups = [
        setup_of(document()),
        setup_of(document(limit=0.2, calibrate={"name": "zscore", "window": 50})),
        setup_of(document()),
    ]

    assert differing(setups) == [
        "decision.calibrate",
        "decision.rule.limit",
        "decision.calibrate.name",
        "decision.calibrate.window",
    ]
    assert differing(setups[::2]) == []


# --- The curves -------------------------------------------------------------------------------


POINTS = [(10, 1.0), (20, 2.0), (30, 3.0)]


def test_a_value_on_a_line_is_its_last_point_up_to_then_none_outside_its_period() -> None:
    assert value_at(POINTS, 5) is None
    assert value_at(POINTS, 10) == 1
    assert value_at(POINTS, 25) == 2
    assert value_at(POINTS, 30) == 3
    assert value_at(POINTS, 31) is None
    assert value_at([], 10) is None


def test_the_time_axis_has_each_lines_ends_and_even_times_up_to_its_points() -> None:
    long = [(time, 0.0) for time in range(0, 10_000, 10)]
    short = [(5_003, 0.0), (5_007, 0.0)]

    axis = time_axis([long, short])

    assert len(axis) == CURVE_POINTS
    assert (axis[0], axis[-1]) == (0, 9_990)
    assert {5_003, 5_007} <= set(axis)
    assert axis == sorted(set(axis))
    assert time_axis([]) == []
    assert time_axis([[(10, 0.0)]]) == [10]


def test_a_comparison_lists_each_with_its_curves_on_one_axis() -> None:
    early = document("bt_a", ts=[10, 20, 30], equity=[1.0, 1.1, 1.25], drawdown=[0, 0.2, -0.1])
    late = document("bt_b", ts=[20, 40], equity=[1.0, None], close=[10.0, 12.5], limit=0.2)
    failed = document("bt_c", status="failed")

    shown = compared([early, late, failed])

    assert [backtest.id for backtest in shown.backtests] == ["bt_a", "bt_b", "bt_c"]
    assert shown.times == list(range(10, 41))
    first, second, third = shown.backtests

    def at(curve: list[float | None] | None) -> list[float | None]:
        assert curve is not None
        values = dict(zip(shown.times, curve, strict=True))
        return [values[time] for time in (10, 20, 25, 30, 35, 40)]

    assert at(first.equity) == [0.0, 0.1, 0.1, 0.25, None, None]
    assert at(first.buy_and_hold) == [0.0, -0.5, -0.5, 1.0, None, None]
    # A fall however it is stored, as the page draws it.
    assert at(first.drawdown) == [0.0, -0.2, -0.2, -0.1, None, None]
    # A row without a value is left out: the line ends at its last value.
    assert at(second.equity) == [None, 0.0, None, None, None, None]
    assert at(second.buy_and_hold) == [None, 0.0, 0.0, 0.0, 0.0, 0.25]
    assert (third.status, third.error, third.equity, third.drawdown, third.buy_and_hold) == (
        "failed",
        "It failed.",
        None,
        None,
        None,
    )
    assert shown.metrics[0].values == [0.5, 0.5, None]
    assert [backtest.candles for backtest in shown.backtests] == [3, 2, None]
    assert shown.differs == ["decision.rule.limit"]


def test_a_comparison_without_curve_points_has_no_curves() -> None:
    shown = compared([document("bt_a"), document("bt_b", limit=0.2)], points=0)

    assert shown.times == []
    assert [backtest.equity for backtest in shown.backtests] == [None, None]
    assert [backtest.buy_and_hold for backtest in shown.backtests] == [None, None]
    assert shown.metrics[0].values == [0.5, 0.5]
    assert [backtest.candles for backtest in shown.backtests] == [3, 3]


def test_a_comparison_has_at_most_its_curve_points() -> None:
    ts = list(range(0, 1000, 10))
    long = document("bt_a", ts=ts, equity=[1.0] * 100, close=[100.0] * 100, drawdown=[0] * 100)

    shown = compared([long, document("bt_b")], points=12)

    assert len(shown.times) <= 12
    assert len(shown.backtests[0].equity or []) == len(shown.times)


def test_the_setups_in_diff_mode_are_shared_once_and_each_its_own() -> None:
    docs = [
        document("bt_a"),
        document("bt_b", limit=0.2, calibrate={"name": "zscore", "window": 50}),
    ]

    shown = compared(docs, setup_mode="diff")
    full = compared(docs)

    assert shown.differs == full.differs
    assert [backtest.setup for backtest in shown.backtests] == [
        {"decision": {"calibrate": None, "rule": {"limit": 0.1}}},
        {"decision": {"calibrate": {"name": "zscore", "window": 50}, "rule": {"limit": 0.2}}},
    ]
    assert shown.shared_setup is not None
    assert shown.shared_setup["decision"]["rule"] == {"name": "threshold"}
    assert shown.shared_setup["instrument"] == "stock:AAPL"
    assert full.shared_setup is None
    # Together they are each whole setup again.
    for backtest, whole in zip(shown.backtests, full.backtests, strict=True):
        merged = {**shown.shared_setup, **backtest.setup}
        merged["decision"] = {**shown.shared_setup["decision"], **backtest.setup["decision"]}
        merged["decision"]["rule"] = {
            **shown.shared_setup["decision"]["rule"],
            **backtest.setup["decision"]["rule"],
        }
        assert merged == whole.setup


def test_a_setup_splits_by_the_differing_paths() -> None:
    setup = {"a": 1, "b": {"c": 2, "d": {"e": 3}}, "f": {}, "g": [1, 2]}

    assert split_setup(setup, ["b.d.e", "g"]) == (
        {"a": 1, "b": {"c": 2}, "f": {}},
        {
            "b": {"d": {"e": 3}},
            "g": [1, 2],
        },
    )
    assert split_setup(setup, []) == (setup, {})
    # A field that is a value in one setup and a group in another differs as a whole.
    assert split_setup(setup, ["b"]) == ({"a": 1, "f": {}, "g": [1, 2]}, {"b": setup["b"]})


def test_one_backtest_in_diff_mode_shares_its_whole_setup() -> None:
    shown = compared([document("bt_a")], setup_mode="diff")

    assert shown.backtests[0].setup == {}
    assert shown.shared_setup == setup_of(document("bt_a"))


def test_buy_and_hold_of_a_series_per_day_ends_at_its_statistic() -> None:
    # A day's point is its last close: the first is not the price held from, 100.
    daily = document(
        "bt_a",
        ts=[86_399, 172_799, 259_199],
        close=[110.0, 99.0, 132.0],
        stats={**STATS, "buy_and_hold_return": 0.32},
        resolution="day",
    )

    held = compared([daily, document("bt_b", ts=[86_399, 259_199])]).backtests[0].buy_and_hold

    assert held is not None
    assert held[0] == 0.1
    assert held[-1] == 0.32


def test_buy_and_hold_without_its_statistic_is_of_the_first_close() -> None:
    unknown = document(
        "bt_a", close=[100.0, 50.0, 200.0], stats={**STATS, "buy_and_hold_return": None}
    )

    held = compared([unknown, document("bt_b")]).backtests[0].buy_and_hold

    assert held is not None
    assert (held[0], held[-1]) == (0.0, 1.0)


def test_four_backtests_answer_in_about_twenty_kilobytes() -> None:
    # Three curves of 200 times each, where the page loads four whole documents today.
    ts = list(range(0, 50_000 * 1800, 1800))
    wealth = [1 + index / 7_919 for index in range(len(ts))]
    fall = [-(index % 997) / 7_919 for index in range(len(ts))]
    docs = [
        document(
            f"bt_{n}",
            ts=ts,
            equity=wealth,
            drawdown=fall,
            close=[100 + value for value in wealth],
        )
        for n in range(4)
    ]

    size = len(compared(docs).model_dump_json())

    assert size < 22_000
