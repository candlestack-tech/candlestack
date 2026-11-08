"""What the backtests routes depend on: the services, on the request's Postgres session, and
for starting a backtest or a sweep and building a document the market data and the runner the
app keeps (``app.state.data_service``, ``app.state.backtest_runner``)."""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import State

from candlestack.core import DbSessionDep, SettingsDep
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.service import BacktestsService
from candlestack.experiments.store import BacktestStore
from candlestack.experiments.sweeps import Sweeps


def get_backtests_service(db: DbSessionDep) -> BacktestsService:
    return BacktestsService(BacktestStore(db))


BacktestsServiceDep = Annotated[BacktestsService, Depends(get_backtests_service)]


def backtest_runs(state: State, db: AsyncSession) -> BacktestRuns:
    """The runs on `db` with the clients the app keeps on `state`: what a route depends on, and
    what an MCP tool builds for its call."""
    return BacktestRuns(
        BacktestStore(db), state.data_service, state.backtest_runner, state.settings.app_version
    )


def get_backtest_runs(request: Request, db: DbSessionDep) -> BacktestRuns:
    return backtest_runs(request.app.state, db)


BacktestRunsDep = Annotated[BacktestRuns, Depends(get_backtest_runs)]


def build_sweeps(state: State, db: AsyncSession, app_version: str) -> Sweeps:
    """The sweeps of the app with ``state`` on the Postgres session ``db``: for the routes and
    the MCP tools alike."""
    store = BacktestStore(db)
    runs = BacktestRuns(store, state.data_service, state.backtest_runner, app_version)
    return Sweeps(store, runs)


def get_sweeps(request: Request, db: DbSessionDep, settings: SettingsDep) -> Sweeps:
    return build_sweeps(request.app.state, db, settings.app_version)


SweepsDep = Annotated[Sweeps, Depends(get_sweeps)]
