"""The tools of sweeps: a grid of backtests from one call, checked first or started,
followed until it is ranked, and the list of the user's sweeps. They call the service of
``POST /api/v1/sweeps``, ``GET /api/v1/sweeps`` and ``GET /api/v1/sweeps/{sweep_id}``."""

from datetime import UTC, datetime
from typing import Annotated

from mcp.server.mcpserver import Context
from pydantic import Field

from candlestack.accounts import Scope
from candlestack.experiments import (
    PAGE_MAX,
    PAGE_SIZE,
    PER_PAGE_MAX,
    BacktestIn,
    SweepIn,
    SweepMetric,
    SweepOut,
    SweepPlan,
    SweepsPage,
    SweepVary,
    build_sweeps,
    sweep_problems,
)
from candlestack.mcp.convention import Example, ToolSpec, changes, reads, tool_call

_MODEL = "mdl_k2m9qx47f3"
_PERIOD = {"start": 1704067200, "end": 1735689600}
_BASE = {
    "model": {"id": _MODEL},
    "data": {"instrument": {"id": "stock:AAPL"}, "period": _PERIOD},
    "decision": {"rule": {"name": "threshold", "limit": 0.1}},
    "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
}
_VARY = {"rules": [{"name": "threshold", "limit": 0.05}, {"name": "threshold", "limit": 0.2}]}


async def validate_sweep(
    base: BacktestIn, ctx: Context, vary: SweepVary | None = None
) -> SweepPlan:
    """Checks a sweep without starting anything: every combination of `base` (the setup of one
    backtest: model, data, decision, execution, benchmarks) and the values in `vary` (models,
    instruments, periods, decision rules) passes the checks of a new backtest, and the user's
    queue has room for all of them. Use it before run_sweep to see how many backtests a grid
    makes and what each one runs.

    Answers the count, the limit of 20, the queue's room and each setup in the grid's order. A
    setup that fails a check is the problem of that check with `setup`, its place from 1."""
    call = tool_call(ctx)
    body = SweepIn(base=base, vary=vary or SweepVary(), validate_only=True)
    async with call.db() as db:
        with sweep_problems():
            return await build_sweeps(call.state, db, call.settings.app_version).plan(
                call.user.id, body
            )


async def run_sweep(
    base: BacktestIn,
    ctx: Context,
    vary: SweepVary | None = None,
    metric: SweepMetric = SweepMetric.sharpe,
) -> SweepOut:
    """Starts a backtest for every combination of `base` (the setup of one backtest) and the
    values in `vary`: models (each model's manifest sets what it reads and its timeframe, so
    list models to vary those), instruments, periods and decision rules, 2 to 20 backtests. All
    go to the end of the user's queue, which runs one at a time, or none does. `metric` is the
    statistic the ranking orders by.

    Answers the sweep: its ID, each backtest's ID, status and setup, and the progress. Follow it
    with get_sweep until `done`. The backtests are ordinary ones, with the sweep's ID."""
    call = tool_call(ctx)
    body = SweepIn(base=base, vary=vary or SweepVary(), metric=metric)
    async with call.db() as db:
        with sweep_problems():
            return await build_sweeps(call.state, db, call.settings.app_version).start(
                call.user.id, body
            )


async def get_sweep(sweep_id: str, ctx: Context, metric: SweepMetric | None = None) -> SweepOut:
    """A sweep of the user: how many of its backtests are queued, running, finished and failed,
    each backtest's ID, status and setup, and once none is queued or running (`done`) the
    ranking, best first, by `metric` or else the sweep's own. Values that read the same in the
    app share a rank, and the best and the worst are marked as the Compare page marks them.

    Call it again while it runs; a backtest takes a minute or a few. For every statistic of the
    backtests, compare them with their IDs."""
    call = tool_call(ctx)
    async with call.db() as db:
        with sweep_problems():
            return await build_sweeps(call.state, db, call.settings.app_version).sweep(
                call.user.id, sweep_id, metric
            )


async def list_sweeps(
    ctx: Context,
    page: Annotated[int, Field(ge=1, le=PAGE_MAX, description="The page, from 1")] = 1,
    per_page: Annotated[
        int, Field(ge=1, le=PER_PAGE_MAX, description="Sweeps per page")
    ] = PAGE_SIZE,
) -> SweepsPage:
    """Lists the user's sweeps, newest first: each one's ID, metric, how many of its
    backtests are queued, running, finished and failed, whether it is done, and its best
    finished backtest so far by the metric. Use it to find a sweep again; get_sweep ranks one,
    and list_backtests with `sweep_id` lists its backtests. A page past the last one is
    empty."""
    call = tool_call(ctx)
    async with call.db() as db:
        return await build_sweeps(call.state, db, call.settings.app_version).sweeps(
            call.user.id, page, per_page
        )


def _setups() -> list[dict[str, object]]:
    return [
        {
            "model": _MODEL,
            "model_name": "lstm.keras",
            "representation": "windows",
            "timeframe": "30m",
            "instrument": "stock:AAPL",
            "period": _PERIOD,
            "rule": rule,
        }
        for rule in _VARY["rules"]
    ]


_PLAN = SweepPlan.model_validate({"count": 2, "limit": 20, "queue_room": 20, "setups": _setups()})
_SWEEP = SweepOut.model_validate(
    {
        "id": "sw_7f3k2m9qx4",
        "created_at": datetime(2026, 11, 8, 10, tzinfo=UTC),
        "metric": "sharpe",
        "count": 2,
        "done": True,
        "progress": {"queued": 0, "running": 0, "finished": 2, "failed": 0},
        "backtests": [
            {
                "id": backtest_id,
                "number": number,
                "name": name,
                "status": "finished",
                "queue_position": None,
                "error": None,
                "setup": setup,
            }
            for (backtest_id, number, name), setup in zip(
                [("bt_7f3k2m9qx4", 12, "Silent Orion"), ("bt_t9n8v7d6a5", 13, "Bright Vega")],
                _setups(),
                strict=True,
            )
        ],
        "ranking": {
            "metric": "sharpe",
            "label": "Sharpe",
            "better": "higher",
            "backtests": [
                {
                    "rank": 1,
                    "id": "bt_t9n8v7d6a5",
                    "number": 13,
                    "name": "Bright Vega",
                    "value": 1.42,
                    "mark": "best",
                },
                {
                    "rank": 2,
                    "id": "bt_7f3k2m9qx4",
                    "number": 12,
                    "name": "Silent Orion",
                    "value": 0.81,
                    "mark": "worst",
                },
            ],
        },
    }
)
_STARTED = SweepOut.model_validate(
    {
        **_SWEEP.model_dump(),
        "done": False,
        "ranking": None,
        "progress": {"queued": 1, "running": 1, "finished": 0, "failed": 0},
        "backtests": [
            {**_SWEEP.backtests[0].model_dump(), "status": "running"},
            {**_SWEEP.backtests[1].model_dump(), "status": "queued", "queue_position": 1},
        ],
    }
)

_LISTED = SweepsPage.model_validate(
    {
        "sweeps": [
            {
                "id": _SWEEP.id,
                "created_at": _SWEEP.created_at,
                "metric": "sharpe",
                "count": 2,
                "done": True,
                "progress": _SWEEP.progress,
                "best": {"id": "bt_t9n8v7d6a5", "number": 13, "name": "Bright Vega", "value": 1.42},
            }
        ],
        "total": 1,
        "page": 1,
        "pages": 1,
    }
)

TOOLS = (
    ToolSpec(
        validate_sweep,
        name="validate_sweep",
        title="Check a sweep",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"base": _BASE, "vary": _VARY}, result=_PLAN),
    ),
    ToolSpec(
        run_sweep,
        name="run_sweep",
        title="Run a sweep",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=False),
        example=Example(
            arguments={"base": _BASE, "vary": _VARY, "metric": "sharpe"}, result=_STARTED
        ),
    ),
    ToolSpec(
        get_sweep,
        name="get_sweep",
        title="Get a sweep",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"sweep_id": "sw_7f3k2m9qx4"}, result=_SWEEP),
    ),
    ToolSpec(
        list_sweeps,
        name="list_sweeps",
        title="List sweeps",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={}, result=_LISTED),
    ),
)
