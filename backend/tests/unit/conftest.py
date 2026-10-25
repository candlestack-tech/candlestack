"""What every unit test needs: no Postgres behind the app's startup."""

import pytest

from candlestack.experiments import BacktestRunner


async def _no_recovery(_runner: BacktestRunner) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_backtest_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """At startup the backtest runner fails the backtests a stopped process left running, a
    query unit tests have no Postgres for; the integration tests run it on theirs."""
    monkeypatch.setattr(BacktestRunner, "recover", _no_recovery)
