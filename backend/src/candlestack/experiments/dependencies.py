"""What the backtests routes depend on: the services, on the request's Postgres session, and
for starting a backtest and building its document the market data and the runner the app keeps
(``app.state.data_service``, ``app.state.backtest_runner``)."""

from typing import Annotated

from fastapi import Depends, Request

from candlestack.core import DbSessionDep, SettingsDep
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.service import BacktestsService
from candlestack.experiments.store import BacktestStore


def get_backtests_service(db: DbSessionDep) -> BacktestsService:
    return BacktestsService(BacktestStore(db))


BacktestsServiceDep = Annotated[BacktestsService, Depends(get_backtests_service)]


def get_backtest_runs(request: Request, db: DbSessionDep, settings: SettingsDep) -> BacktestRuns:
    state = request.app.state
    return BacktestRuns(
        BacktestStore(db), state.data_service, state.backtest_runner, settings.app_version
    )


BacktestRunsDep = Annotated[BacktestRuns, Depends(get_backtest_runs)]
