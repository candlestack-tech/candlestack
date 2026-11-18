"""The tools of sweeps: a grid of backtests from one call, checked first or started,
followed until it is ranked, and the list of the user's sweeps. They call the service of
``POST /api/v1/sweeps``, ``GET /api/v1/sweeps`` and ``GET /api/v1/sweeps/{sweep_id}``;
``wait_for_sweep`` waits on the runner's word that one of its backtests changed, as
``wait_for_backtest`` does."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

import anyio
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from candlestack.accounts import HEAVY, Scope
from candlestack.core import DATABASE_ERRORS
from candlestack.experiments import (
    PAGE_MAX,
    PAGE_SIZE,
    PER_PAGE_MAX,
    BacktestIn,
    BacktestStep,
    Committed,
    SweepBest,
    SweepIn,
    SweepListed,
    SweepMetric,
    SweepOut,
    SweepPlan,
    SweepProgress,
    SweepRanking,
    SweepsPage,
    SweepVary,
    backtest_runs,
    build_sweeps,
    idempotent,
    sweep_problems,
)
from candlestack.mcp.convention import (
    COMPARE_PAGE_MAX,
    POLL_S,
    WAIT_DEFAULT_S,
    WAIT_MAX_S,
    Example,
    IdempotencyKey,
    Progress,
    Step,
    ToolCall,
    ToolSpec,
    backtest_url,
    changes,
    compare_url,
    reads,
    run_progress,
    runner_of,
    tool_call,
)

_ETA = Field(
    description="About how many seconds until its last backtest ends, as wait_for_backtest "
    "estimates each; null once done and before any of the user's backtests finished"
)


def _compare_url(
    call: ToolCall, backtest_ids: Sequence[str], ranking: SweepRanking | None
) -> str | None:
    """The compare page of the sweep's best backtests once ranked (those with a value), else of
    all of them when the page shows them all."""
    if ranking is not None:
        ranked = [backtest.id for backtest in ranking.backtests if backtest.rank is not None]
        return compare_url(call, ranked[:COMPARE_PAGE_MAX])
    return compare_url(call, backtest_ids)


class SweepResult(SweepOut):
    """A sweep: its backtests, how far they are, once all ended the ranking, and its page."""

    # A default, as run_sweep answers a retry with its key from the first answer as it was kept,
    # which may predate the field.
    url: str | None = Field(
        default=None,
        description=f"The app's Compare page with its backtests side by side, to show the "
        f"user: once ranked its best {COMPARE_PAGE_MAX}, before that all of them when they "
        f"are {COMPARE_PAGE_MAX} at most; else null",
    )


def _result(call: ToolCall, sweep: SweepOut) -> SweepResult:
    ids = [backtest.id for backtest in sweep.backtests]
    return SweepResult(**sweep.model_dump(), url=_compare_url(call, ids, sweep.ranking))


class SweepStarted(SweepResult):
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
    url: str | None = Field(
        description=f"Once done: the app's Compare page with its best {COMPARE_PAGE_MAX} side "
        "by side, to show the user"
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
    """Checks a sweep without starting anything: every combination of `base` and `vary`
    passes a new backtest's checks and the queue has room for all. Use it before run_sweep to
    see what a grid runs. A failing setup answers its check's problem with `setup`, its place
    from 1. Sweeps: get_docs page sweeps."""
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
    """Starts a backtest for every combination of `base` (one backtest's setup) and the values
    in `vary`, 2 to 20; all join the user's queue, or none does. `metric` ranks them. Follow it
    with wait_for_sweep until `done`. Sweeps: get_docs page sweeps; an example of `vary`:
    get_docs page mcp/tools (section run_sweep)."""
    call = tool_call(ctx)
    body = SweepIn(base=base, vary=vary or SweepVary(), metric=metric)

    async def started(committed: Committed) -> bytes:
        async with call.db() as db:
            sweep = await build_sweeps(call.state, db, call.settings.app_version).start(
                call.user.id, body, committed
            )
        try:
            eta = await _eta(call, [backtest.id for backtest in sweep.backtests])
        except DATABASE_ERRORS:
            eta = None  # started already: answered without the estimate
        result = _result(call, sweep)
        return SweepStarted(**result.model_dump(), eta_s=eta).model_dump_json().encode()

    async def again(sweep_id: str) -> bytes:
        async with call.db() as db:
            sweep = await build_sweeps(call.state, db, call.settings.app_version).sweep(
                call.user.id, sweep_id
            )
        eta = await _eta(call, [backtest.id for backtest in sweep.backtests])
        return (
            SweepStarted(**_result(call, sweep).model_dump(), eta_s=eta).model_dump_json().encode()
        )

    with sweep_problems():
        content, _ = await idempotent(
            call.state.redis,
            user_id=call.user.id,
            kind="run_sweep",
            key=idempotency_key,
            body=body.model_dump_json().encode(),
            answer=started,
            rebuild=again,
        )
    return SweepStarted.model_validate_json(content)


async def get_sweep(sweep_id: str, ctx: Context, metric: SweepMetric | None = None) -> SweepResult:
    """A sweep of the user: its backtests' statuses and setups and, once `done`, the ranking
    by `metric` (else the sweep's own). To wait: wait_for_sweep; every statistic:
    compare_backtests."""
    call = tool_call(ctx)
    async with call.db() as db:
        with sweep_problems():
            sweep = await build_sweeps(call.state, db, call.settings.app_version).sweep(
                call.user.id, sweep_id, metric
            )
    return _result(call, sweep)


class LinkedBest(SweepBest):
    url: str = Field(description="The backtest's page in the app, to show the user")


class LinkedSweep(SweepListed):
    best: LinkedBest | None = Field(
        description="The finished backtest with the best `metric` so far, with its page; null "
        "until one has a value. get_sweep ranks them all"
    )


class LinkedSweepsPage(SweepsPage):
    sweeps: list[LinkedSweep] = Field(description="The page, newest first")


def _linked(call: ToolCall, page: SweepsPage) -> LinkedSweepsPage:
    """The page with the app's page of each sweep's best backtest so far."""
    sweeps = [
        LinkedSweep(
            **sweep.model_dump(exclude={"best"}),
            best=None
            if sweep.best is None
            else LinkedBest(**sweep.best.model_dump(), url=backtest_url(call, sweep.best.id)),
        )
        for sweep in page.sweeps
    ]
    return LinkedSweepsPage(**page.model_dump(exclude={"sweeps"}), sweeps=sweeps)


async def list_sweeps(
    ctx: Context,
    page: Annotated[int, Field(ge=1, le=PAGE_MAX, description="The page, from 1")] = 1,
    per_page: Annotated[
        int, Field(ge=1, le=PER_PAGE_MAX, description="Sweeps per page")
    ] = PAGE_SIZE,
) -> LinkedSweepsPage:
    """Lists the user's sweeps, newest first, with their progress and best backtest so far.
    get_sweep ranks one; list_backtests with `sweep_id` lists its backtests."""
    call = tool_call(ctx)
    async with call.db() as db:
        listed = await build_sweeps(call.state, db, call.settings.app_version).sweeps(
            call.user.id, page, per_page
        )
    return _linked(call, listed)


async def wait_for_sweep(
    sweep_id: Annotated[
        str, Field(description="Public ID of the sweep", examples=["sw_7f3k2m9qx4"])
    ],
    ctx: Context,
    timeout_s: Annotated[
        int,
        Field(ge=1, le=WAIT_MAX_S, description="Seconds to wait at most"),
    ] = WAIT_DEFAULT_S,
) -> SweepWaitResult:
    """Waits until every backtest of a sweep ended or `timeout_s` passed, then answers the
    counts, `eta_s` and, once `done`, get_sweep's ranking. Not `done`: call it again (the
    backtests run one after another)."""
    call = tool_call(ctx)
    deadline = anyio.current_time() + timeout_s
    ids, _, _ = await _progress(call, sweep_id)
    reported = Progress(ctx)
    with runner_of(call).watch(ids) as changed:
        while True:
            changed.clear()
            ids, progress, running = await _progress(call, sweep_id)
            done = not progress.queued and not progress.running
            await reported.report(*sweep_progress(progress, running))
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
        url=None if ranking is None else _compare_url(call, ids, ranking),
    )


def sweep_progress(
    progress: SweepProgress, running: Sequence[Sequence[Step]]
) -> tuple[float, int, str]:
    """The progress of a wait for a sweep, in backtests of all: those that ended and how far
    each running one is by its steps (`running`); and its message."""
    ended = progress.finished + progress.failed
    total = ended + progress.queued + progress.running
    message = (
        f"{ended} of {total} ended: {progress.finished} finished, {progress.failed} failed, "
        f"{progress.running} running, {progress.queued} queued"
    )
    return round(ended + sum(run_progress(steps) for steps in running), 2), total, message


async def _progress(
    call: ToolCall, sweep_id: str
) -> tuple[list[str], SweepProgress, list[list[BacktestStep]]]:
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
_SWEEP = SweepResult.model_validate(
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
        "url": "https://app.candlestack.tech/backtests/compare?ids=bt_t9n8v7d6a5,bt_7f3k2m9qx4",
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
        "url": "https://app.candlestack.tech/backtests/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5",
    }
)
_WAITED = SweepWaitResult(
    id=_SWEEP.id,
    count=_SWEEP.count,
    done=True,
    progress=_SWEEP.progress,
    eta_s=None,
    ranking=_SWEEP.ranking,
    url=_SWEEP.url,
)

_LISTED = LinkedSweepsPage.model_validate(
    {
        "sweeps": [
            {
                "id": _SWEEP.id,
                "created_at": _SWEEP.created_at,
                "metric": "sharpe",
                "count": 2,
                "done": True,
                "progress": _SWEEP.progress,
                "best": {
                    "id": "bt_t9n8v7d6a5",
                    "number": 13,
                    "name": "Bright Vega",
                    "value": 1.42,
                    "url": "https://app.candlestack.tech/backtests/bt_t9n8v7d6a5",
                },
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
        details="""
            `base` is the setup of one backtest (model, data, decision, execution, benchmarks);
            `vary` takes models, instruments, periods and decision rules. Answers the count, the
            limit of 20, the queue's room and each setup in the grid's order. The shape of
            `vary`, with an example: run_sweep.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"base": _BASE, "vary": _VARY}, result=_PLAN),
    ),
    ToolSpec(
        run_sweep,
        name="run_sweep",
        title="Run a sweep",
        details="""
            `vary` takes models (each model's manifest sets what it reads and its timeframe, so
            list models to vary those), instruments, periods and decision rules. The backtests
            go to the end of the user's queue, which runs one at a time. `metric` is the
            statistic the ranking orders by.

            Pass an `idempotency_key` (a UUID you make) to call it again safely after a
            timeout: the same call with the same key within 24 hours answers the first sweep
            instead of starting another.

            Answers the sweep: its ID, each backtest's ID, status and setup, the progress,
            `eta_s` and, for 4 backtests at most, `url`, the app's Compare page of them. The
            backtests are ordinary ones, with the sweep's ID.""",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=False),
        example=Example(
            arguments={"base": _BASE, "vary": _VARY, "metric": "sharpe"}, result=_STARTED
        ),
        cost=HEAVY,
    ),
    ToolSpec(
        get_sweep,
        name="get_sweep",
        title="Get a sweep",
        details="""
            How many of its backtests are queued, running, finished and failed, and each
            backtest's ID, status and setup. The ranking comes once none is queued or running
            (`done`), best first. Values that read the same in the app share a rank, and the
            best and the worst are marked as the Compare page marks them. `url` is the app's
            Compare page with the best 4 side by side: give it to the user.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"sweep_id": "sw_7f3k2m9qx4"}, result=_SWEEP),
    ),
    ToolSpec(
        list_sweeps,
        name="list_sweeps",
        title="List sweeps",
        details="""
            Each one's ID, metric, how many of its backtests are queued, running, finished and
            failed, whether it is done, and its best finished backtest so far by the metric with
            `url`, its page in the app. Use it to find a sweep again. A page past the last one
            is empty.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={}, result=_LISTED),
    ),
    ToolSpec(
        wait_for_sweep,
        name="wait_for_sweep",
        title="Wait for a sweep",
        details="""
            It answers how many backtests are queued, running, finished and failed. Once `done`,
            the ranking comes with `url`, the app's Compare page of the best 4.
            `timeout_s` is at most 60 seconds. A client that asks for progress gets how many
            ended while it waits.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"sweep_id": "sw_7f3k2m9qx4", "timeout_s": 50}, result=_WAITED),
        progress=True,
    ),
)
