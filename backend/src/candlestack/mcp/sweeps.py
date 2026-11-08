"""The tools of sweeps: a grid of backtests from one call, checked first or started, and
followed until it is ranked. They call the service of ``POST /api/v1/sweeps`` and
``GET /api/v1/sweeps/{sweep_id}``; ``wait_for_sweep`` waits on the runner's word that one of
its backtests changed, as ``wait_for_backtest`` does."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

import anyio
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from candlestack.accounts import Scope
from candlestack.experiments import (
    BacktestIn,
    SweepIn,
    SweepMetric,
    SweepOut,
    SweepPlan,
    SweepProgress,
    SweepRanking,
    SweepVary,
    backtest_runs,
    build_sweeps,
    idempotent,
    sweep_problems,
)
from candlestack.mcp.convention import (
    POLL_S,
    WAIT_DEFAULT_S,
    WAIT_MAX_S,
    Example,
    IdempotencyKey,
    ToolCall,
    ToolSpec,
    changes,
    reads,
    runner_of,
    tool_call,
)

_ETA = Field(
    description="About how many seconds until its last backtest ends, as wait_for_backtest "
    "estimates each; null once done and before any of the user's backtests finished"
)


class SweepStarted(SweepOut):
    """The sweep just started."""

    eta_s: float | None = _ETA


class SweepWaitResult(BaseModel):
    """Where a sweep stands after a wait: its progress, and its ranking once done."""

    id: str = Field(description="Public ID of the sweep", examples=["sw_7f3k2m9qx4"])
    count: int = Field(description="Its backtests")
    done: bool = Field(
        description="None of its backtests is queued or running any more: `ranking` is set; "
        "false when `timeout_s` passed first"
    )
    progress: SweepProgress
    eta_s: float | None = _ETA
    ranking: SweepRanking | None = Field(
        description="Once done: as get_sweep ranks them, by the sweep's own metric"
    )


async def _eta(call: ToolCall, ids: Sequence[str]) -> float | None:
    """The latest end of the backtests that have not ended (``BacktestRuns.statuses``)."""
    async with call.db() as db:
        with sweep_problems():
            standings = await backtest_runs(call.state, db).statuses(call.user.id, ids)
    etas = [standing.eta_s for standing in standings if standing.eta_s is not None]
    return max(etas) if etas else None


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
    idempotency_key: IdempotencyKey = None,
) -> SweepStarted:
    """Starts a backtest for every combination of `base` (the setup of one backtest) and the
    values in `vary`: models (each model's manifest sets what it reads and its timeframe, so
    list models to vary those), instruments, periods and decision rules, 2 to 20 backtests. All
    go to the end of the user's queue, which runs one at a time, or none does. `metric` is the
    statistic the ranking orders by. Pass an `idempotency_key` (a UUID you make) to call it
    again safely after a timeout: the same call with the same key within 24 hours answers the
    first sweep instead of starting another.

    Answers the sweep: its ID, each backtest's ID, status and setup, the progress and `eta_s`.
    Follow it with wait_for_sweep until `done`. The backtests are ordinary ones, with the
    sweep's ID."""
    call = tool_call(ctx)
    body = SweepIn(base=base, vary=vary or SweepVary(), metric=metric)

    async def started() -> bytes:
        async with call.db() as db:
            sweep = await build_sweeps(call.state, db, call.settings.app_version).start(
                call.user.id, body
            )
        eta = await _eta(call, [backtest.id for backtest in sweep.backtests])
        return SweepStarted(**sweep.model_dump(), eta_s=eta).model_dump_json().encode()

    with sweep_problems():
        content, _ = await idempotent(
            call.state.redis,
            user_id=call.user.id,
            kind="run_sweep",
            key=idempotency_key,
            body=body.model_dump_json().encode(),
            answer=started,
        )
    return SweepStarted.model_validate_json(content)


async def get_sweep(sweep_id: str, ctx: Context, metric: SweepMetric | None = None) -> SweepOut:
    """A sweep of the user: how many of its backtests are queued, running, finished and failed,
    each backtest's ID, status and setup, and once none is queued or running (`done`) the
    ranking, best first, by `metric` or else the sweep's own. Values that read the same in the
    app share a rank, and the best and the worst are marked as the Compare page marks them.

    To wait for it to end, call wait_for_sweep. For every statistic of the backtests, compare
    them with their IDs."""
    call = tool_call(ctx)
    async with call.db() as db:
        with sweep_problems():
            return await build_sweeps(call.state, db, call.settings.app_version).sweep(
                call.user.id, sweep_id, metric
            )


async def wait_for_sweep(
    sweep_id: Annotated[
        str, Field(description="Public ID of the sweep", examples=["sw_7f3k2m9qx4"])
    ],
    ctx: Context,
    timeout_s: Annotated[
        int,
        Field(
            ge=1,
            le=WAIT_MAX_S,
            description=f"How long to wait at most, in seconds (up to {WAIT_MAX_S})",
        ),
    ] = WAIT_DEFAULT_S,
) -> SweepWaitResult:
    """Waits until every backtest of a sweep of the user has ended or `timeout_s` seconds
    passed, then answers how many are queued, running, finished and failed, `eta_s`, and once
    `done` the ranking get_sweep gives. When `done` is false, call it again: a sweep's
    backtests run one after another."""
    call = tool_call(ctx)
    deadline = anyio.current_time() + timeout_s
    ids, _ = await _progress(call, sweep_id)
    with runner_of(call).watch(ids) as changed:
        while True:
            changed.clear()
            ids, progress = await _progress(call, sweep_id)
            done = not progress.queued and not progress.running
            left = deadline - anyio.current_time()
            if done or left <= 0:
                break
            with anyio.move_on_after(min(POLL_S, left)):
                await changed.wait()
    ranking = None
    if done:
        async with call.db() as db:
            with sweep_problems():
                sweep = await build_sweeps(call.state, db, call.settings.app_version).sweep(
                    call.user.id, sweep_id
                )
        ranking, progress = sweep.ranking, sweep.progress
    return SweepWaitResult(
        id=sweep_id,
        count=len(ids),
        done=done,
        progress=progress,
        eta_s=None if done else await _eta(call, ids),
        ranking=ranking,
    )


async def _progress(call: ToolCall, sweep_id: str) -> tuple[list[str], SweepProgress]:
    async with call.db() as db:
        with sweep_problems():
            return await build_sweeps(call.state, db, call.settings.app_version).progress(
                call.user.id, sweep_id
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
_STARTED = SweepStarted.model_validate(
    {
        **_SWEEP.model_dump(),
        "done": False,
        "ranking": None,
        "progress": {"queued": 1, "running": 1, "finished": 0, "failed": 0},
        "backtests": [
            {**_SWEEP.backtests[0].model_dump(), "status": "running"},
            {**_SWEEP.backtests[1].model_dump(), "status": "queued", "queue_position": 1},
        ],
        "eta_s": 312.0,
    }
)
_WAITED = SweepWaitResult(
    id=_SWEEP.id,
    count=_SWEEP.count,
    done=True,
    progress=_SWEEP.progress,
    eta_s=None,
    ranking=_SWEEP.ranking,
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
        wait_for_sweep,
        name="wait_for_sweep",
        title="Wait for a sweep",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"sweep_id": "sw_7f3k2m9qx4", "timeout_s": 50}, result=_WAITED),
    ),
)
