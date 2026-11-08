"""Sweeps without Postgres: how the lists to vary expand into a grid of new backtests, the
limits of a sweep, and the ranking by a statistic with the marking rules of the comparison."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from candlestack.experiments.compare import RANKABLE, ranks
from candlestack.experiments.errors import InvalidBacktest, SweepTooLarge
from candlestack.experiments.models import Backtest, Sweep
from candlestack.experiments.store import Listed
from candlestack.experiments.sweeps import (
    SWEEP_MAX,
    SweepIn,
    SweepMetric,
    SweepVary,
    _listed,
    grid,
    grid_size,
    ranking,
)

DAY = 86_400
BASE: dict[str, Any] = {
    "model": {"id": "mdl_a"},
    "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 0, "end": DAY}},
    "decision": {"rule": {"name": "threshold", "limit": 0.1}, "long_only": True},
    "execution": {"fee_bps": 5.0},
    "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
}


def sweep(vary: dict[str, Any], **base: Any) -> SweepIn:
    return SweepIn.model_validate({"base": {**BASE, **base}, "vary": vary})


def threshold(limit: float) -> dict[str, Any]:
    return {"name": "threshold", "limit": limit}


# --- The grid -------------------------------------------------------------------------------


def test_every_combination_is_a_backtest_models_first() -> None:
    body = sweep(
        {
            "models": ["mdl_a", "mdl_b"],
            "periods": [{"start": 0, "end": DAY}, {"start": DAY, "end": 2 * DAY}],
            "rules": [
                threshold(0.1),
                threshold(0.2),
                {"name": "hysteresis", "enter": 0.3, "exit": 0.1},
            ],
        }
    )

    setups = grid(body.base, body.vary)

    assert grid_size(body.vary) == len(setups) == 12
    assert [
        (setup.model.id, setup.data.period.start, setup.decision.rule.name) for setup in setups[:4]
    ] == [
        ("mdl_a", 0, "threshold"),
        ("mdl_a", 0, "threshold"),
        ("mdl_a", 0, "hysteresis"),
        ("mdl_a", DAY, "threshold"),
    ]
    assert setups[6].model.id == "mdl_b"
    # What is not varied is the base's, in every setup.
    for setup in setups:
        assert setup.data.instrument.id == "stock:AAPL"
        assert setup.decision.long_only is True
        assert setup.execution.fee_bps == 5.0


def test_a_list_left_out_keeps_the_bases_value() -> None:
    body = sweep({"rules": [threshold(0.2), threshold(0.3)]})

    first, second = grid(body.base, body.vary)

    assert first.model.id == second.model.id == "mdl_a"
    assert first.data == second.data == body.base.data
    assert (first.decision.rule, second.decision.rule) == tuple(body.vary.rules)
    # The base itself is not changed.
    assert body.base.decision.rule.model_dump() == threshold(0.1)


def test_buy_and_hold_follows_each_instrument_and_a_fund_stays() -> None:
    held = sweep({"instruments": ["stock:MSFT", "stock:aapl"]})
    fund = sweep(
        {"instruments": ["stock:MSFT", "stock:NVDA"]},
        benchmarks=[{"id": "spy", "kind": "buy_and_hold", "instrument": "stock:SPY"}],
    )

    assert [setup.benchmarks[0].instrument for setup in grid(held.base, held.vary)] == [
        "stock:MSFT",
        "stock:aapl",
    ]
    assert [setup.benchmarks[0].instrument for setup in grid(fund.base, fund.vary)] == [
        "stock:SPY",
        "stock:SPY",
    ]


def test_a_benchmark_written_otherwise_still_follows() -> None:
    body = sweep(
        {"instruments": ["stock:MSFT", "stock:NVDA"]},
        benchmarks=[{"id": "bh", "kind": "buy_and_hold", "instrument": "STOCK:aapl"}],
    )

    assert grid(body.base, body.vary)[0].benchmarks[0].instrument == "stock:MSFT"


def test_more_than_twenty_backtests_are_refused_with_the_count() -> None:
    body = sweep(
        {
            "models": ["mdl_a", "mdl_b", "mdl_c"],
            "rules": [threshold(limit / 10) for limit in range(7)],
        }
    )

    with pytest.raises(SweepTooLarge) as refused:
        grid(body.base, body.vary)

    assert refused.value.count == 21


def test_twenty_backtests_are_the_most() -> None:
    body = sweep(
        {"models": ["mdl_a", "mdl_b"], "rules": [threshold(limit / 10) for limit in range(10)]}
    )

    assert len(grid(body.base, body.vary)) == SWEEP_MAX


def test_the_size_is_counted_without_building_a_large_grid() -> None:
    lists = [f"x{index}" for index in range(SWEEP_MAX)]

    vary = SweepVary(models=lists, instruments=lists)

    assert grid_size(vary) == 400


@pytest.mark.parametrize("vary", [{}, {"models": ["mdl_b"]}], ids=["nothing", "one-value"])
def test_a_sweep_varies_something(vary: dict[str, Any]) -> None:
    body = sweep(vary)

    with pytest.raises(InvalidBacktest, match="at least two values"):
        grid(body.base, body.vary)


@pytest.mark.parametrize(
    "vary",
    [
        {"models": ["mdl_a", "mdl_a"]},
        {"instruments": ["stock:AAPL", " Stock:aapl "]},
        {"rules": [threshold(0.1), threshold(0.1)]},
        {"periods": [{"start": 5, "end": 1}]},
        {"models": [""]},
        {"instruments": ["s" * 65]},
        {"models": [f"mdl_{index}" for index in range(SWEEP_MAX + 1)]},
        {"timeframes": ["1h"]},
    ],
    ids=[
        "model-twice",
        "instrument-spelt-twice",
        "rule-twice",
        "period",
        "empty-id",
        "long-id",
        "long-list",
        "unknown",
    ],
)
def test_the_lists_are_checked(vary: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        SweepVary.model_validate(vary)


def test_an_instrument_that_does_not_parse_waits_for_its_setups_check() -> None:
    vary = SweepVary.model_validate({"instruments": ["AAPL", "stock:AAPL"]})

    assert vary.instruments == ["AAPL", "stock:AAPL"]


@pytest.mark.parametrize("flag", ["validateOnly", "dry_run", "validate"])
def test_a_mistyped_field_of_a_sweep_is_refused(flag: str) -> None:
    with pytest.raises(ValidationError):
        SweepIn.model_validate({"base": BASE, "vary": {"rules": [threshold(0.2)]}, flag: True})


def test_every_rankable_statistic_is_a_metric() -> None:
    assert {metric.value for metric in SweepMetric} == set(RANKABLE)


# --- The ranking ----------------------------------------------------------------------------


def test_ranks_share_values_that_read_the_same() -> None:
    stats = [{"sharpe": 1.0}, {"sharpe": 2.004}, None, {"sharpe": 2.001}, {"sharpe": None}]

    assert ranks(stats, "sharpe") == [3, 1, None, 1, None]


def test_a_fall_ranks_by_its_size_and_fees_lower_first() -> None:
    falls = [{"max_drawdown": -0.3}, {"max_drawdown": -0.1}, {"max_drawdown": 0.2}]
    fees = [{"fees_paid": 0.02}, {"fees_paid": 0.01}]

    assert ranks(falls, "max_drawdown") == [3, 1, 2]
    assert ranks(fees, "fees_paid") == [2, 1]


def row(number: int, status: str, stats: dict[str, Any] | None) -> Listed:
    backtest = Backtest(
        id=f"bt_{number:010d}",
        number=number,
        status=status,
        generated_name=f"Backtest {number}",
        name=None,
        created_at=datetime(2026, 11, 8, tzinfo=UTC),
    )
    backtest.stats = stats
    return Listed(backtest, "lstm.keras")


def test_the_ranking_puts_the_best_first_and_those_without_a_value_last() -> None:
    rows = [
        row(1, "finished", {"sharpe": 0.5}),
        row(2, "failed", None),
        row(3, "finished", {"sharpe": 1.5}),
        row(4, "finished", {"sharpe": 1.0}),
        row(5, "finished", {"sharpe": None}),
    ]

    ranked = ranking(rows, SweepMetric.sharpe)

    assert (ranked.metric, ranked.label, ranked.better) == (SweepMetric.sharpe, "Sharpe", "higher")
    assert [(each.number, each.rank, each.value, each.mark) for each in ranked.backtests] == [
        (3, 1, 1.5, "best"),
        (4, 2, 1.0, None),
        (1, 3, 0.5, "worst"),
        (2, None, None, None),
        (5, None, None, None),
    ]
    assert ranked.backtests[0].name == "Backtest 3"


def test_the_ranking_reads_only_finished_backtests() -> None:
    # A running backtest has no statistics yet; should one be stored, it is not ranked.
    rows = [row(1, "running", {"sharpe": 9.0}), row(2, "finished", {"sharpe": 1.0})]

    ranked = ranking(rows, SweepMetric.max_drawdown)

    assert ranked.better == "lower"
    assert [(each.number, each.rank) for each in ranked.backtests] == [(1, None), (2, None)]
    assert ranking(rows, SweepMetric.sharpe).backtests[0].number == 2


def listed_sweep(metric: str, rows: list[Listed]) -> Any:
    sweep = Sweep(id="sw_7f3k2m9qx4", metric=metric, created_at=datetime(2026, 11, 8, tzinfo=UTC))
    return _listed(sweep, rows)


def test_a_listed_sweep_has_its_progress_and_its_best_so_far() -> None:
    rows = [
        row(1, "finished", {"max_drawdown": -0.3}),
        row(2, "finished", {"max_drawdown": -0.1}),
        row(3, "running", None),
        row(4, "queued", None),
        row(5, "failed", None),
    ]

    listed = listed_sweep("max_drawdown", rows)

    assert (listed.count, listed.done) == (5, False)
    assert listed.progress.model_dump() == {"queued": 1, "running": 1, "finished": 2, "failed": 1}
    # The smallest fall is the best, as the ranking has it.
    assert listed.best is not None
    assert (listed.best.number, listed.best.value, listed.best.name) == (2, -0.1, "Backtest 2")


def test_a_listed_sweep_without_a_value_has_no_best() -> None:
    listed = listed_sweep("sharpe", [row(1, "failed", None), row(2, "finished", {"sharpe": None})])

    assert (listed.done, listed.best) == (True, None)
