"""What the backtests routes depend on: the services, on the request's Postgres session, and
for starting a backtest and building its document the market data and the runner the app keeps
(``app.state.data_service``, ``app.state.backtest_runner``)."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import State

from candlestack.core import DbSessionDep, SettingsDep
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.service import BacktestsService
from candlestack.experiments.store import BacktestStore


def get_backtests_service(db: DbSessionDep) -> BacktestsService:
    return BacktestsService(BacktestStore(db))


BacktestsServiceDep = Annotated[BacktestsService, Depends(get_backtests_service)]


def backtest_runs(state: State, db: AsyncSession, app_version: str) -> BacktestRuns:
    """The runs on `db`, with the market data and the runner of the app's `state`: for the
    routes and the MCP tools."""
    return BacktestRuns(BacktestStore(db), state.data_service, state.backtest_runner, app_version)


def get_backtest_runs(request: Request, db: DbSessionDep, settings: SettingsDep) -> BacktestRuns:
    return backtest_runs(request.app.state, db, settings.app_version)


BacktestRunsDep = Annotated[BacktestRuns, Depends(get_backtest_runs)]
