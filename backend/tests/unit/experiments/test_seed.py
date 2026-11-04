"""The seed command's data and its refusal of prod, without a database; what it writes is an
integration test (tests/integration/experiments/test_seed.py)."""

import runpy
import sys
from datetime import UTC, datetime, timedelta

import pytest

from candlestack.core import Settings, get_settings
from candlestack.experiments import seed as seed_module
from candlestack.experiments.seed import (
    RUNS,
    downsample,
    equity_walk,
    gauss,
    main,
    mulberry32,
    seed_backtests,
    seed_equity,
    started_at,
)
from candlestack.experiments.store import UserModel

USER_ID = "usr_7f3k2m9qx4"
EVENING = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)


def by_number(backtests):
    return {backtest.number: backtest for backtest in backtests}


def test_the_runs_are_the_mockups_fifteen() -> None:
    assert [run.number for run in RUNS] == list(range(1, 16))
    assert sum(run.starred for run in RUNS) == 5
    assert [run.number for run in RUNS if run.results is None] == [6, 13]


def test_the_backtests_of_the_runs() -> None:
    backtests = by_number(seed_backtests(USER_ID, first_number=1, models=[], now=EVENING))

    newest = backtests[15]
    assert newest.user_id == USER_ID
    assert newest.seeded is True
    assert newest.status == "finished"
    assert newest.representation == "windows"
    assert [item["symbol"] for item in newest.instruments] == [
        "AAPL", "MSFT", "NVDA", "SPY", "TSLA", "AMZN", "META", "GOOGL",
    ]  # fmt: skip
    assert {item["market"] for item in newest.instruments} == {"stock"}
    assert (newest.total_return, newest.buy_and_hold_return) == (0.246, 0.682)
    assert newest.benchmark_return == 0.682  # buy and hold, its benchmark
    assert (newest.sharpe, newest.max_drawdown, newest.win_rate) == (0.79, -0.197, 0.512)
    assert (newest.num_trades, newest.duration_s) == (1712, 162.0)
    assert newest.created_at == datetime(2026, 10, 17, 16, 5, tzinfo=UTC)
    assert newest.finished_at == newest.created_at + timedelta(seconds=162)
    assert newest.setup == {
        "timeframe": "30m",
        "period_start": "2021-01-04",
        "period_end": "2024-12-31",
        "direction": "long",
        "threshold": 0.05,
        "stop_loss_pct": 7,
        "fees_pct": 0.1,
        "slippage_pct": 0.02,
        "benchmark": "bh",
    }
    assert backtests[8].setup["direction"] == "both"
    assert backtests[4].setup["threshold"] == 0.1
    assert backtests[14].starred is True
    assert backtests[1].created_at == datetime(2026, 10, 10, 20, 40, tzinfo=UTC)
    assert backtests[10].created_at == datetime(2026, 10, 16, 18, 10, tzinfo=UTC)


def test_a_failed_run_has_its_error_and_no_results() -> None:
    backtests = by_number(seed_backtests(USER_ID, first_number=1, models=[], now=EVENING))

    failed = backtests[13]
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.startswith("blesk-novol-8.keras reads Renko bricks, not a Kagi line.")
    assert failed.representation == "kagi"
    assert [failed.total_return, failed.sharpe, failed.max_drawdown, failed.num_trades] == [
        None
    ] * 4
    assert failed.duration_s == 3.0
    assert backtests[6].error is not None
    assert backtests[6].error.startswith("stockmixer.onnx expects 30 candles per sample.")


def test_the_users_models_are_used_by_name_in_any_case() -> None:
    model = UserModel("mdl_7f3k2m9qx4", "My-TSMixer.keras", "keras")

    backtests = by_number(seed_backtests(USER_ID, first_number=1, models=[model], now=EVENING))

    assert (backtests[9].model_id, backtests[9].model_name) == (model.id, "My-TSMixer.keras")
    assert (backtests[1].model_id, backtests[1].model_name) == (None, "blesk-novol-8.keras")
    assert (backtests[6].model_name, backtests[6].model_framework) == ("stockmixer.onnx", "onnx")
    assert backtests[1].model_framework == "keras"


def test_the_numbers_continue_after_the_users_own() -> None:
    backtests = seed_backtests(USER_ID, first_number=8, models=[], now=EVENING)

    assert [backtest.number for backtest in backtests] == list(range(8, 23))
    assert by_number(backtests)[22].representation == "windows"  # the mockup's #15


def test_todays_last_run_ends_when_the_mockups_newest_ends() -> None:
    # The mockup's #15 starts at 16:05 and takes 162 s; #14 ends at 15:51:22.
    assert timedelta(hours=16, minutes=5, seconds=162) == seed_module._TODAY_LAST_END


def test_early_in_the_day_todays_runs_move_back_but_keep_their_gaps() -> None:
    morning = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
    today = [run for run in RUNS if run.days_ago == 0]

    starts = [started_at(run, morning) for run in today]

    last = max(starts)
    assert last + timedelta(seconds=162) == morning
    assert sorted(starts) == starts
    assert starts[1] - starts[0] == timedelta(minutes=24)  # 13:58 to 14:22
    yesterday = next(run for run in RUNS if run.days_ago == 1)
    assert started_at(yesterday, morning).date() == morning.date() - timedelta(days=1)


def test_the_random_numbers_are_the_mockups() -> None:
    # What the mockup's mulberry(15 * 7919 + 13) draws first, in a browser.
    draw = mulberry32(15 * 7919 + 13)

    assert [draw() for _ in range(4)] == [
        0.8188890744931996,
        0.6068964912556112,
        0.5411353525705636,
        0.1895393084269017,
    ]


def test_the_equity_walk_is_the_mockups() -> None:
    # The mockup's eqRun of #15 (return 24.6 %, buy and hold 68.2 %, drawdown -19.7 %), minus 1.
    draw = mulberry32(15 * 7919 + 13)
    volatility = min(1.4, 0.6 + 19.7 / 25)

    strategy = equity_walk(draw, 0.246, 0.018 * volatility)
    benchmark = equity_walk(draw, 0.682, 0.03 * volatility)

    assert len(strategy) == 121
    assert strategy[0] == 0
    assert strategy[-1] == pytest.approx(0.246)
    assert strategy[1] == pytest.approx(-0.008811369370075206)
    assert strategy[60] == pytest.approx(0.19943062867440609)
    assert benchmark[1] == pytest.approx(0.023422844172914514)
    assert benchmark[119] == pytest.approx(0.6871947615654217)


def test_gauss_skips_a_zero_draw() -> None:
    draws = iter([0.0, 0.5, 0.0, 0.25])

    assert gauss(lambda: next(draws)) == pytest.approx(0.0, abs=1e-12)  # cos(pi / 2)


def test_downsample_keeps_the_ends_and_spaces_the_rest_evenly() -> None:
    points = [float(i) for i in range(121)]

    sampled = downsample(points)

    assert len(sampled) == 60
    assert (sampled[0], sampled[-1]) == (0.0, 120.0)
    assert sorted(set(sampled)) == sampled
    assert downsample(points, 3) == [0.0, 60.0, 120.0]


def test_the_seeded_equity() -> None:
    run = RUNS[14]  # the mockup's #15

    strategy, benchmark = seed_equity(run) or ([], [])

    assert len(strategy) == len(benchmark) == 60
    assert (strategy[0], strategy[-1]) == (0.0, 0.246)
    assert (benchmark[0], benchmark[-1]) == (0.0, 0.682)
    assert seed_equity(run) == (strategy, benchmark)  # the same lines every time
    assert (seed_equity(RUNS[13]) or ([], []))[0] != strategy  # each run its own
    assert seed_equity(RUNS[12]) is None  # #13 failed


def test_the_seeded_backtests_keep_their_equity() -> None:
    backtests = by_number(seed_backtests(USER_ID, first_number=1, models=[], now=EVENING))

    assert backtests[8].equity is not None
    assert backtests[8].equity["strategy"][-1] == -0.042
    assert backtests[6].equity is None


@pytest.fixture
def prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(seed_module, "get_settings", lambda: Settings(app_env="prod"))


@pytest.mark.usefixtures("prod")
def test_the_command_refuses_prod(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--email", "ada@example.com"]) == 1
    assert capsys.readouterr().err == (
        "python -m candlestack.experiments.seed: error: APP_ENV is prod: test backtests go to "
        "stage and local accounts only\n"
    )


def test_the_command_needs_an_email(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as stopped:
        main([])

    assert stopped.value.code == 2
    assert "--email" in capsys.readouterr().err


def test_python_m_runs_the_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr("sys.argv", ["seed", "--email", "ada@example.com"])
    # Run as `python -m` would, not on top of the module this file imported.
    monkeypatch.delitem(sys.modules, "candlestack.experiments.seed")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as stopped:
            runpy.run_module("candlestack.experiments.seed", run_name="__main__")
    finally:
        get_settings.cache_clear()

    assert stopped.value.code == 1
