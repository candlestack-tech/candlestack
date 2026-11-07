"""The backtest runner when Postgres fails it: the start of the process goes on, a run that
cannot even be read is a bug that is reported, and a queue that cannot be moved on is a
warning, moved on later. The runs and the queues themselves are integration tests
(tests/integration/experiments/test_runs.py)."""

import asyncio
import logging
from typing import Any

import pytest

from candlestack.experiments import BacktestRunner

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
