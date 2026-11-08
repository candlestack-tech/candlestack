"""Experiments: the users' backtests, run, stored with their setup and results, listed on the
Backtests page and summed up on the Dashboard. A backtest's run puts together the candles of
``data``, the model's predictions from the worker (``jobs``), ``signals``, ``engine`` and
``metrics``, in a task of the API process (``BacktestRunner``).

Other modules import only from here (``from candlestack.experiments import backtests_router``),
never from the submodules. ``candlestack.main`` puts a ``BacktestRunner`` on
``app.state.backtest_runner`` (and ``recover`` and ``close`` it with the app), and
``model_usage`` on ``app.state.model_usage``,
so the models list of ``ml``, which cannot import this module, shows how the backtests used
each model. The MCP tools of sweeps build the service with ``build_sweeps`` and raise its
errors as problems with ``sweep_problems``, as the routes do. A user's backtests and sweeps go
with the account by the foreign keys' cascade: no hook. Importing the module declares its
tables (``backend/migrations`` relies on it).

The MCP tools (``candlestack.mcp``) build the same services as the routes (``backtest_runs``),
take the same setup (``BacktestIn`` and its groups) and raise the same problems
(``backtest_problems``).

``python -m candlestack.experiments.seed --email <email>`` fills an account with test backtests
(stage and local only).
"""

from candlestack.experiments.api import backtest_problems, sweep_problems
from candlestack.experiments.api import router as backtests_router
from candlestack.experiments.compare import COMPARE_MAX, CompareOut, check_ids
from candlestack.experiments.compare import compare as compare_backtests
from candlestack.experiments.dependencies import backtest_runs, build_sweeps
from candlestack.experiments.document import (
    BacktestCheck,
    BacktestHeader,
    BacktestIn,
    BenchmarkIn,
    DataIn,
    Decision,
    Execution,
    ModelRef,
    Stats,
)
from candlestack.experiments.runner import BacktestRunner
from candlestack.experiments.runs import BacktestRuns
from candlestack.experiments.store import model_usage
from candlestack.experiments.sweeps import SweepIn, SweepMetric, SweepOut, SweepPlan, SweepVary

__all__ = [
    "COMPARE_MAX",
    "BacktestCheck",
    "BacktestHeader",
    "BacktestIn",
    "BacktestRunner",
    "BacktestRuns",
    "BenchmarkIn",
    "CompareOut",
    "DataIn",
    "Decision",
    "Execution",
    "ModelRef",
    "Stats",
    "SweepIn",
    "SweepMetric",
    "SweepOut",
    "SweepPlan",
    "SweepVary",
    "backtest_problems",
    "backtest_runs",
    "backtests_router",
    "build_sweeps",
    "check_ids",
    "compare_backtests",
    "model_usage",
    "sweep_problems",
]
