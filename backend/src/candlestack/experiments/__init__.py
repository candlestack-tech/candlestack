"""Experiments: the users' backtests, run, stored with their setup and results, listed on the
Backtests page and summed up on the Dashboard. A backtest's run puts together the candles of
``data``, the model's predictions from the worker (``jobs``), ``signals``, ``engine`` and
``metrics``, in a task of the API process (``BacktestRunner``).

Other modules import only from here (``from candlestack.experiments import backtests_router``),
never from the submodules; the MCP tools take the services, their queries, bodies and enums and
the problems of their errors from here too. ``candlestack.main`` puts a ``BacktestRunner`` on
``app.state.backtest_runner`` (and ``recover`` and ``close`` it with the app), and
``model_usage`` on ``app.state.model_usage``,
so the models list of ``ml``, which cannot import this module, shows how the backtests used
each model. The MCP tools of sweeps build the service with ``build_sweeps`` and raise its
errors as problems with ``sweep_problems``, as the routes do. A user's backtests and sweeps go
with the account by the foreign keys' cascade: no hook. Importing the module declares its
tables (``backend/migrations`` relies on it).

``python -m candlestack.experiments.seed --email <email>`` fills an account with test backtests
(stage and local only).
"""

from candlestack.experiments.api import (
    PAGE_MAX,
    backtest_problems,
    sweep_problems,
    update_problems,
)
from candlestack.experiments.api import router as backtests_router
from candlestack.experiments.dependencies import backtest_runs, build_sweeps, get_backtests_service
from candlestack.experiments.document import BacktestIn
from candlestack.experiments.runner import BacktestRunner
from candlestack.experiments.schemas import (
    BACKTEST_NAME_MAX,
    EXPERIMENTS_PAGE_SIZE,
    MODEL_NAME_MAX,
    PAGE_SIZE,
    QUERY_MAX,
    Author,
    BacktestCounts,
    BacktestInstrument,
    BacktestOut,
    BacktestSetup,
    BacktestShow,
    BacktestSort,
    BacktestStatus,
    BacktestUpdate,
    ExperimentCounts,
    ExperimentFacets,
    ExperimentOut,
    ExperimentSort,
    InstrumentMarket,
    ModelCount,
    Representation,
    SetupTimeframe,
    SortOrder,
)
from candlestack.experiments.service import ExperimentsQuery, ListQuery
from candlestack.experiments.store import model_usage
from candlestack.experiments.sweeps import SweepIn, SweepMetric, SweepOut, SweepPlan, SweepVary

__all__ = [
    "BACKTEST_NAME_MAX",
    "EXPERIMENTS_PAGE_SIZE",
    "MODEL_NAME_MAX",
    "PAGE_MAX",
    "PAGE_SIZE",
    "QUERY_MAX",
    "Author",
    "BacktestCounts",
    "BacktestIn",
    "BacktestInstrument",
    "BacktestOut",
    "BacktestRunner",
    "BacktestSetup",
    "BacktestShow",
    "BacktestSort",
    "BacktestStatus",
    "BacktestUpdate",
    "ExperimentCounts",
    "ExperimentFacets",
    "ExperimentOut",
    "ExperimentSort",
    "ExperimentsQuery",
    "InstrumentMarket",
    "ListQuery",
    "ModelCount",
    "Representation",
    "SetupTimeframe",
    "SortOrder",
    "SweepIn",
    "SweepMetric",
    "SweepOut",
    "SweepPlan",
    "SweepVary",
    "backtest_problems",
    "backtest_runs",
    "backtests_router",
    "build_sweeps",
    "get_backtests_service",
    "model_usage",
    "sweep_problems",
    "update_problems",
]
