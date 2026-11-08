"""The backtest runner when Postgres fails it: the start of the process goes on, a run that
cannot even be read is a bug that is reported, and a queue that cannot be moved on is a
warning, moved on later. The runs and the queues themselves are integration tests
(tests/integration/experiments/test_runs.py)."""

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.experiments import BacktestRunner
from candlestack.experiments.runner import _covers
from candlestack.preprocessing import parse_manifest

pytestmark = pytest.mark.anyio

# The unit tests' conftest replaces it once a test runs; this is the runner's own.
RECOVER = BacktestRunner.recover


def runner(database: Any) -> BacktestRunner:
    return BacktestRunner(database, data=None, jobs=None)  # ty: ignore[invalid-argument-type]


async def test_without_postgres_the_start_goes_on(
    fake_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    runs = runner(fake_database)

    await RECOVER(runs)
    await runs.close()

    assert "The backtests left running were not checked" in caplog.text


async def test_without_postgres_the_queues_wait_for_the_next_check(
    fake_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    runs = runner(fake_database)

    await runs.resume()
    await runs._advance("bt_7f3k2m9qx4")
    await runs._next("usr_7f3k2m9qx4")

    assert "The backtest queues were not checked" in caplog.text
    assert "The queue of backtest bt_7f3k2m9qx4 was not moved on" in caplog.text
    assert "The next backtest of a queue was not started" in caplog.text


async def test_the_queues_are_checked_until_the_runner_closes(
    fake_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = BacktestRunner(
        fake_database,
        data=None,  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
        check_every=0.01,
    )
    checked = asyncio.Event()

    async def resume() -> None:
        checked.set()

    await RECOVER(runs)
    monkeypatch.setattr(runs, "_check", resume)
    await asyncio.wait_for(checked.wait(), timeout=5)
    await runs.close()
    checked.clear()
    await asyncio.sleep(0.05)

    assert runs._watch is not None
    assert runs._watch.cancelled()
    assert not checked.is_set()


async def test_without_postgres_at_the_start_the_backtests_left_running_are_failed_later(
    fake_database: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = BacktestRunner(
        fake_database,
        data=None,  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
        check_every=0.01,
    )
    resumed = asyncio.Event()

    async def resume() -> None:
        resumed.set()

    monkeypatch.setattr(runs, "resume", resume)
    check = runs._check
    tries = 0
    twice = asyncio.Event()

    async def counted() -> None:
        nonlocal tries
        await check()
        tries += 1
        if tries == 2:
            twice.set()

    monkeypatch.setattr(runs, "_check", counted)
    await RECOVER(runs)
    await asyncio.wait_for(twice.wait(), timeout=5)
    await runs.close()

    assert caplog.text.count("The backtests left running were not checked") >= 2

    # Not one queue moved on before those left running were failed.
    assert not resumed.is_set()
    assert not runs._recovered


async def test_a_check_that_fails_on_a_bug_is_reported_and_the_checks_go_on(
    fake_database: Any, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs = BacktestRunner(
        fake_database,
        data=None,  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
        check_every=0.01,
    )
    calls = 0
    checked = asyncio.Event()

    async def check() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("a bug")
        checked.set()

    monkeypatch.setattr(runs, "_check", check)
    runs._watch = asyncio.create_task(runs._keep_moving())
    await asyncio.wait_for(checked.wait(), timeout=5)
    await runs.close()

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert errors[0].getMessage() == "The backtest queues could not be checked"


class _NoDatabase:
    """A database a test must not use: opening a session fails the test."""

    def sessions(self) -> Any:
        raise AssertionError("a session was opened")


async def test_a_closed_runner_takes_no_queued_backtest() -> None:
    runs = runner(_NoDatabase())
    await runs.close()

    await runs._next("usr_7f3k2m9qx4")

    assert runs._tasks == {}


async def test_a_backtest_handed_to_a_closed_runner_goes_back_to_its_queue(
    fake_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    runs = runner(fake_database)
    await runs.close()

    runs.start("bt_7f3k2m9qx4")
    await runs.close()  # waits for it to be put back

    assert runs._tasks == {}
    assert runs._requeues == set()
    assert "Backtest bt_7f3k2m9qx4 was not put back in its queue" in caplog.text


async def test_steps_that_cannot_be_saved_do_not_stop_the_run(
    fake_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    await runner(fake_database)._save_steps("bt_7f3k2m9qx4", [])

    assert "The steps of backtest bt_7f3k2m9qx4 were not saved" in caplog.text


async def test_a_run_that_fails_on_a_bug_is_reported_and_its_marking_too(
    fake_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    runs = runner(fake_database)

    runs.start("bt_7f3k2m9qx4")
    await asyncio.gather(*list(runs._tasks))

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert "failed with an error of CandleStack" in errors[0].getMessage()
    assert "was not marked failed" in caplog.text


# --- The warm-up of a run ----------------------------------------------------------------------

MANIFEST = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "keras" / "raw_candles.manifest.json").read_text()
)
# 2024-01-01, the start of the period of these tests.
START = 1_704_067_200


def candles(before: int, after: int = 10) -> pl.DataFrame:
    """30-minute candles, ``before`` of them before ``START`` and ``after`` from it on."""
    ts = START + 1800 * np.arange(-before, after, dtype=np.int64)
    close = 100 + np.sin(ts / 7_000)
    return pl.DataFrame(
        {
            "ts": ts,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": np.full(ts.size, 1000.0),
        }
    )


@pytest.mark.parametrize(("before", "covered"), [(24, True), (30, True), (23, False)])
def test_a_warm_up_covers_the_period_with_a_sample_on_its_first_candle(
    before: int, covered: bool
) -> None:
    assert _covers(candles(before), parse_manifest(MANIFEST), START) is covered


def test_a_period_without_candles_or_with_candles_the_model_refuses_needs_no_more() -> None:
    manifest = parse_manifest(MANIFEST)
    refused = candles(10).with_columns(close=pl.lit(float("nan")))

    assert _covers(candles(10, after=0), manifest, START) is True
    assert _covers(refused, manifest, START) is True


class _Market:
    """A data module whose warm-up starts where ``start`` says: no load may follow."""

    def __init__(self, start: int) -> None:
        self.start = start

    async def warm_up_start(self, *_args: Any) -> int:
        return self.start

    async def candles(self, *_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("loaded again")


def _spec(manifest: dict[str, Any]) -> Any:
    return SimpleNamespace(
        model=SimpleNamespace(manifest=manifest),
        data=SimpleNamespace(
            instrument=SimpleNamespace(id="stock:AAPL"),
            timeframe="30m",
            period=SimpleNamespace(start=START, end=START + 10 * 1800),
        ),
    )


async def test_a_warm_up_is_not_lengthened_without_earlier_candles_or_a_manifest() -> None:
    frame = candles(10)
    loaded = SimpleNamespace(frame=frame, start=START - 10 * 1800, marked_frame=lambda: frame)
    # The data module has no candle before those loaded.
    runs = BacktestRunner(
        None,  # ty: ignore[invalid-argument-type]
        data=_Market(loaded.start),  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
    )

    assert await runs._with_warm_up(_spec(MANIFEST), loaded) is loaded  # ty: ignore[invalid-argument-type]
    # A manifest that does not parse is the worker's to refuse.
    assert await runs._with_warm_up(_spec({"version": 0}), loaded) is loaded
