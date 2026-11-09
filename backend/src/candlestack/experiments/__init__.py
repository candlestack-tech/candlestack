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
errors as problems with ``sweep_problems``, as the routes do; the MCP tool that deletes
backtests builds ``build_deletes`` and raises the same problems. A user's backtests and sweeps go
with the account by the foreign keys' cascade: no hook. Importing the module declares its
tables (``backend/migrations`` relies on it).

The MCP tools (``candlestack.mcp``) build the same services as the routes (``backtest_runs``),
take the same setup (``BacktestIn`` and its groups) and raise the same problems
(``backtest_problems``); their starts keep idempotency keys as the routes do (``idempotent``),
and their waits learn from ``app.state.backtest_runner`` (``BacktestRunner.watch``) when a
backtest changed.

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
from candlestack.experiments.compare import (
    COMPARE_MAX,
    CURVE_POINTS,
    ComparedBacktest,
    CompareOut,
    SetupMode,
    check_ids,
)
from candlestack.experiments.compare import compare as compare_backtests
from candlestack.experiments.deletes import DELETE_MAX, BacktestsDeleted
from candlestack.experiments.dependencies import (
    backtest_runs,
    build_deletes,
    build_sweeps,
    get_backtests_service,
)
from candlestack.experiments.document import (
    BacktestCheck,
    BacktestDocument,
    BacktestHeader,
    BacktestIn,
    BacktestStep,
    BenchmarkIn,
    DataIn,
    Decision,
    Execution,
    ModelRef,
    Period,
    Stats,
)
from candlestack.experiments.idempotency import KEY_MAX as IDEMPOTENCY_KEY_MAX
from candlestack.experiments.idempotency import KEY_PATTERN as IDEMPOTENCY_KEY_PATTERN
from candlestack.experiments.idempotency import Committed
from candlestack.experiments.idempotency import once as idempotent
from candlestack.experiments.runner import BacktestRunner
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.schemas import (
    BACKTEST_NAME_MAX,
    EXPERIMENTS_PAGE_SIZE,
    MODEL_NAME_MAX,
    PAGE_SIZE,
    PER_PAGE_MAX,
    QUERY_MAX,
    UPDATE_MAX,
    Author,
    BacktestCounts,
    BacktestInstrument,
    BacktestOut,
    BacktestSetup,
    BacktestShow,
    BacktestSort,
    BacktestStatus,
    BacktestsUpdated,
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
    as_utc,
    instrument_filter,
)
from candlestack.experiments.service import ExperimentsQuery, ListQuery
from candlestack.experiments.store import BacktestFilters, model_usage
from candlestack.experiments.sweeps import (
    SweepIn,
    SweepMetric,
    SweepOut,
    SweepPlan,
    SweepProgress,
    SweepRanking,
    SweepsPage,
    SweepVary,
)

__all__ = [
    "BACKTEST_NAME_MAX",
    "COMPARE_MAX",
    "CURVE_POINTS",
    "DELETE_MAX",
    "EXPERIMENTS_PAGE_SIZE",
    "IDEMPOTENCY_KEY_MAX",
    "IDEMPOTENCY_KEY_PATTERN",
    "MODEL_NAME_MAX",
    "PAGE_MAX",
    "PAGE_SIZE",
    "PER_PAGE_MAX",
    "QUERY_MAX",
    "UPDATE_MAX",
    "Author",
    "BacktestCheck",
    "BacktestCounts",
    "BacktestDocument",
    "BacktestFilters",
    "BacktestHeader",
    "BacktestIn",
    "BacktestInstrument",
    "BacktestOut",
    "BacktestRunner",
    "BacktestRuns",
    "BacktestSetup",
    "BacktestShow",
    "BacktestSort",
    "BacktestStatus",
    "BacktestStep",
    "BacktestUpdate",
    "BacktestsDeleted",
    "BacktestsUpdated",
    "BenchmarkIn",
    "Committed",
    "CompareOut",
    "ComparedBacktest",
    "DataIn",
    "Decision",
    "Execution",
    "ExperimentCounts",
    "ExperimentFacets",
    "ExperimentOut",
    "ExperimentSort",
    "ExperimentsQuery",
    "InstrumentMarket",
    "ListQuery",
    "ModelCount",
    "ModelRef",
    "Period",
    "Representation",
    "SetupMode",
    "SetupTimeframe",
    "SortOrder",
    "Stats",
    "SweepIn",
    "SweepMetric",
    "SweepOut",
    "SweepPlan",
    "SweepProgress",
    "SweepRanking",
    "SweepVary",
    "SweepsPage",
    "as_utc",
    "backtest_problems",
    "backtest_runs",
    "backtests_router",
    "build_deletes",
    "build_sweeps",
    "check_ids",
    "compare_backtests",
    "get_backtests_service",
    "idempotent",
    "instrument_filter",
    "model_usage",
    "sweep_problems",
    "update_problems",
]
