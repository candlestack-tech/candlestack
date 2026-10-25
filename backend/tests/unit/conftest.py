"""What every unit test needs: no Postgres behind the app's startup."""

import pytest

from candlestack.experiments import BacktestRunner
from candlestack.ml import ModelChecks


async def _no_recovery(_runner: BacktestRunner) -> None:
    return None


def _no_checks(_checks: ModelChecks) -> None:
    return None


@pytest.fixture(autouse=True)
def _no_backtest_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """At startup the backtest runner fails the backtests a stopped process left running, and
    the model checks look up the models left unchecked: queries unit tests have no Postgres
    for; the integration tests run them on theirs."""
    monkeypatch.setattr(BacktestRunner, "recover", _no_recovery)
    monkeypatch.setattr(ModelChecks, "recover", _no_checks)
