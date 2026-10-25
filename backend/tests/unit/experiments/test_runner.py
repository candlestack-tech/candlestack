"""The backtest runner when Postgres fails it: the start of the process goes on, and a run
that cannot even be read is a bug that is reported. The runs themselves are integration tests
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
    await RECOVER(runner(fake_database))

    assert "The backtests left running were not checked" in caplog.text


async def test_a_run_that_fails_on_a_bug_is_reported_and_its_marking_too(
    fake_database: Any, caplog: pytest.LogCaptureFixture
) -> None:
    runs = runner(fake_database)

    runs.start("bt_7f3k2m9qx4")
    await asyncio.gather(*list(runs._tasks))

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert "failed with an error of CandleStack" in errors[0].getMessage()
    assert "was not marked failed" in caplog.text
