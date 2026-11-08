"""The tools that run backtests and read what came out: check a setup, start it, follow it in
the queue and while it runs, read its results in a compact form and compare backtests.

They call the services of the backtests routes (``experiments.backtest_runs``) with the same
access rules: the user's own backtests and other accounts' published ones, read only. The
answers are smaller than the API's: the result document is cut to what an agent reads (the
statistics and the setup, and only when asked a page of trades and at most ``EQUITY_POINTS``
points of the equity), read from what the run stored.
"""

import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import anyio
import numpy as np
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from candlestack import experiments
from candlestack.accounts import HEAVY, Scope
from candlestack.core import DATABASE_ERRORS, ProblemError
from candlestack.experiments import (
    COMPARE_MAX,
    CURVE_POINTS,
    BacktestCheck,
    BacktestHeader,
    BacktestIn,
    BacktestRuns,
    BenchmarkIn,
    Committed,
    ComparedBacktest,
    CompareOut,
    DataIn,
    Decision,
    Execution,
    ModelRef,
    SetupMode,
    Stats,
    backtest_problems,
    backtest_runs,
    check_ids,
    idempotent,
)
from candlestack.mcp.apps import attach, shows_widget, tool_meta
from candlestack.mcp.convention import (
    COMPARE_PAGE_MAX,
    POLL_S,
    WAIT_DEFAULT_S,
    WAIT_MAX_S,
    Example,
    IdempotencyKey,
    Progress,
    ToolCall,
    ToolSpec,
    backtest_url,
    changes,
    compare_url,
    reads,
    runner_of,
    tool_call,
)

# The most backtests one wait follows.
WAIT_IDS_MAX = 20
# The most points of the equity get_backtest answers: enough to see its shape.
EQUITY_POINTS = 200
TRADES_PER_PAGE_MAX = 500
_ENDED = {"finished", "failed"}
# Values of the curves to six decimals: a millionth of the starting capital.
_DECIMALS = 6

# A wait's progress in five equal stages: the user's queue, then the four steps of the run.
_STAGES = 5
# What each step but the candles' does while it runs, for the progress' message.
_DOING = {
    "queue": "waiting for a worker",
    "model": "the model predicts",
    "trades": "trading and the statistics",
}

StepId = Literal["candles", "queue", "model", "trades"]
StepState = Literal["pending", "running", "done", "failed"]
Status = Literal["queued", "running", "finished", "failed"]


# --- What the tools answer ------------------------------------------------------------------


class StepOut(BaseModel):
    """A step of the run: candles (loads the market data), queue (waits for the worker), model
    (the worker predicts), trades (the engine and the statistics)."""

    id: StepId
    state: StepState
    duration_s: float | None = Field(description="Seconds it took, once done or failed")
    loaded: int | None = Field(
        description="candles: the chunks of market data loaded so far, of `total`"
    )
    total: int | None = Field(description="candles: the chunks of the load")
    waiting_until: datetime | None = Field(
        description="candles: while the load waits for the data source's request limit, when "
        "it goes on (UTC)"
    )


class BacktestStatusResult(BaseModel):
    """Where a backtest stands."""

    id: str = Field(description="Public ID of the backtest", examples=["bt_7f3k2m9qx4"])
    number: int = Field(description="The author's own number, shown as #N")
    name: str
    status: Status = Field(
        description="queued (waits for the user's running backtest or a free run slot), "
        "running, finished (has "
        "results) or failed (has an error, also when cancelled before it ran)"
    )
    ended: bool = Field(description="finished or failed: it will not change any more")
    queue_position: int | None = Field(
        description="While queued: 1 starts next of the user's backtests, when their running "
        "one ended and a run slot is free"
    )
    error: str | None = Field(description="What went wrong and what to do, for a failed one")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None = Field(description="Seconds it ran, the wait in the queue left out")
    steps: list[StepOut]
    eta_s: float | None = Field(
        description="About how many seconds until it ends: its place in the queue and its run, "
        "each run as long as the median of the user's recent runs, the wait for a free run "
        "slot left out; null once ended, for another account's backtest, and before any of "
        "the user's backtests finished"
    )
    url: str = Field(description="The backtest's page in the app, to show the user")


class BacktestWaitResult(BacktestStatusResult):
    """Where the backtest waited for stands; with `ids`, the one that decided the answer (the
    first that ended for `until` any, the first still going for `until` all, else the first),
    and each one in `backtests`."""

    done: bool = Field(
        description="The wait is over: the backtest ended, or with `ids` any or all of them "
        "(`until`); false when `timeout_s` passed first"
    )
    backtests: list[BacktestStatusResult] | None = Field(
        description="With `ids`: where each stands, in the order given; null with `backtest_id`"
    )


class Author(BaseModel):
    id: str = Field(description="Public ID of the account")
    name: str = Field(description="The account's display name")
    mine: bool = Field(description="The user's own backtest; else a published one, read only")


class BacktestSummary(BaseModel):
    id: str
    number: int
    name: str
    status: Status
    author: Author
    public: bool = Field(description="Published in Experiments")
    queue_position: int | None
    error: str | None
    sweep_id: str | None = Field(
        description="The sweep it was started in (get_sweep); null for one started alone and "
        "for another account's"
    )
    created_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    url: str = Field(description="The backtest's page in the app, to show the user")


class ComparedBacktestOut(ComparedBacktest):
    url: str = Field(description="The backtest's page in the app, to show the user")


class CompareResult(CompareOut):
    """Backtests side by side, in the order asked for, each with its page in the app."""

    backtests: list[ComparedBacktestOut]
    url: str | None = Field(
        description=f"The app's Compare page with these backtests side by side, to show the "
        f"user; null for one, or for more than {COMPARE_PAGE_MAX}, which the page does not show"
    )


class ModelOut(BaseModel):
    id: str | None = Field(description="Null once deleted, and for another account's backtest")
    name: str = Field(description="The file name when it ran")
    framework: str


class InstrumentOut(BaseModel):
    id: str = Field(description="<market>:<symbol>", examples=["stock:AAPL"])
    name: str
    currency: str = Field(description="What the prices are in")


class PeriodOut(BaseModel):
    start: int = Field(description="First second of the test, UTC epoch seconds")
    end: int = Field(description="End of the test, excluded")


class DataSetup(BaseModel):
    instrument: InstrumentOut
    period: PeriodOut
    timeframe: str = Field(description="The model's timeframe", examples=["30m"])
    session: str = Field(description="The model's session: regular (US trading hours) or 24/7")


class SetupOut(BaseModel):
    """What the backtest ran on and with, in the shape of the arguments of start_backtest
    (the other fields are ignored there), so it can be run again, changed or not."""

    model: ModelOut
    data: DataSetup
    decision: Decision
    execution: Execution
    benchmarks: list[BenchmarkIn]


class TradeOut(BaseModel):
    open_ts: int = Field(description="Open time of the entry candle, UTC epoch seconds")
    close_ts: int = Field(description="Open time of the exit candle")
    side: int = Field(description="1 long, -1 short")
    open_price: float
    close_price: float
    trade_return: float = Field(description="Return of the trade, after costs, a fraction")
    fees: float
    exit_reason: str = Field(description="signal, stop, gap, end, ruin or drawdown")
    candles_held: int


class TradesPage(BaseModel):
    page: int
    per_page: int
    pages: int = Field(description="Pages of the trades in the result")
    count: int = Field(description="The trades in the result")
    total: int = Field(description="The trades the run had")
    complete: bool = Field(description="False when only the newest 10,000 trades were kept")
    items: list[TradeOut] = Field(description="The page's trades, oldest first")


class EquityOut(BaseModel):
    """The equity curve: the capital, 1.0 at the start, at evenly spaced rows of the run, the
    first, the last and the deepest point of the drawdown included."""

    points: int = Field(description=f"At most {EQUITY_POINTS}")
    rows: int = Field(description="Rows of the run's series the points were taken from")
    resolution: str = Field(description="candle, or day when the series has a point per day")
    ts: list[int] = Field(description="Open time of each point's candle, UTC epoch seconds")
    equity: list[float]
    drawdown: list[float] = Field(description="Fall from the highest equity, 0 or below")
    benchmarks: dict[str, list[float]] = Field(
        description="Each benchmark's equity at the same points, by its ID"
    )


class BacktestResult(BaseModel):
    """A backtest, its setup and, once finished, its results."""

    backtest: BacktestSummary
    setup: SetupOut
    candles: int | None = Field(description="Candles of the period it ran on, once finished")
    data_changed: bool | None = Field(
        description="The source changed the candles since the run: the results are rebuilt on "
        "the candles as they are now"
    )
    metrics: Stats | None = Field(description="The 12 statistics, once finished; fractions")
    benchmark_returns: dict[str, float | None] = Field(
        description="Each benchmark's total return, by its ID"
    )
    trades: TradesPage | None = Field(
        description="A page of its trades, once finished and asked for (`trades_per_page`)"
    )
    equity: EquityOut | None = Field(
        description="Its equity curve, once finished and asked for (`equity_points`)"
    )


# --- Helpers ----------------------------------------------------------------------------------


@asynccontextmanager
async def _runs(call: ToolCall) -> AsyncIterator[BacktestRuns]:
    """The backtests service of the call, its problems raised as the API's."""
    async with call.db() as db:
        with backtest_problems():
            yield backtest_runs(call.state, db)


def _status(
    call: ToolCall, header: BacktestHeader, eta_s: float | None = None
) -> BacktestStatusResult:
    return BacktestStatusResult(
        id=header.id,
        number=header.number,
        name=header.name,
        status=header.status.value,
        ended=header.status.value in _ENDED,
        queue_position=header.queue_position,
        error=header.error,
        created_at=header.created_at,
        started_at=header.started_at,
        finished_at=header.finished_at,
        duration_s=header.duration_s,
        steps=[
            StepOut(
                id=step.id,
                state=step.state,
                duration_s=step.duration_s,
                loaded=step.loaded,
                total=step.total,
                waiting_until=step.waiting_until,
            )
            for step in header.steps
        ],
        eta_s=eta_s,
        url=backtest_url(call, header.id),
    )


async def _header(call: ToolCall, backtest_id: str) -> BacktestHeader:
    async with _runs(call) as runs:
        return await runs.header(call.user.id, backtest_id)


async def _statuses(call: ToolCall, ids: Sequence[str]) -> list[BacktestStatusResult]:
    """Where each of the backtests stands, from their rows alone, in a session of its own."""
    async with _runs(call) as runs:
        standings = await runs.statuses(call.user.id, ids)
    return [_status(call, standing.header, standing.eta_s) for standing in standings]


def _wanted(backtest_id: str | None, ids: list[str] | None) -> list[str]:
    """The backtests to wait for: ``backtest_id`` or ``ids`` (once each), not both."""
    if (backtest_id is None) == (ids is None):
        raise ProblemError(
            422,
            "validation",
            "Invalid request",
            "Give either backtest_id or ids, the backtests to wait for.",
        )
    return [backtest_id] if backtest_id is not None else list(dict.fromkeys(ids or []))


def _decided(
    statuses: Sequence[BacktestStatusResult], until: Literal["any", "all"]
) -> tuple[BacktestStatusResult, bool]:
    """The backtest the answer is about, and whether the wait is over."""
    if until == "any":
        ended = [status for status in statuses if status.ended]
        return (ended[0], True) if ended else (statuses[0], False)
    going = [status for status in statuses if not status.ended]
    return (going[0], False) if going else (statuses[0], True)


def equity_rows(drawdown: Sequence[float], points: int = EQUITY_POINTS) -> list[int]:
    """The rows of a series of ``len(drawdown)`` rows to show at most ``points`` of: evenly
    spaced, the first and the last, and the deepest point of the drawdown (only the last of
    one point, the first and the last of two)."""
    count = len(drawdown)
    if count <= points:
        return list(range(count))
    if points <= 2:
        return [0, count - 1][-points:]
    spaced = {round(i * (count - 1) / (points - 2)) for i in range(points - 1)}
    deepest = min(range(count), key=drawdown.__getitem__)
    return sorted(spaced | {deepest})


def _rounded(values: Sequence[float | None], rows: Sequence[int]) -> list[float]:
    # A value that is not a number (null in the document) reads as 0.
    picked = np.asarray([values[row] for row in rows], dtype=np.float64)
    return [round(value, _DECIMALS) for value in np.nan_to_num(picked, nan=0.0).tolist()]


def _equity(series: dict[str, Any], points: int) -> EquityOut:
    columns = series["columns"]
    rows = equity_rows(columns["drawdown"], points)
    return EquityOut(
        points=len(rows),
        rows=series["count"],
        resolution=series["resolution"],
        ts=[int(columns["ts"][row]) for row in rows],
        equity=_rounded(columns["equity"], rows),
        drawdown=_rounded(columns["drawdown"], rows),
        benchmarks={key: _rounded(line, rows) for key, line in series["benchmarks"].items()},
    )


def _trades(trades: dict[str, Any], page: int, per_page: int) -> TradesPage:
    count = trades["count"]
    first = (page - 1) * per_page
    # The page's rows of each column as Python values (the stored columns are NumPy arrays).
    columns = {
        name: list(values[first : first + per_page])
        if isinstance(values, list)
        else values[first : first + per_page].tolist()
        for name, values in trades["columns"].items()
    }
    items = [
        TradeOut(
            open_ts=columns["open_ts"][row],
            close_ts=columns["close_ts"][row],
            side=columns["side"][row],
            open_price=columns["open_price"][row],
            close_price=columns["close_price"][row],
            trade_return=columns["return"][row],
            fees=columns["fees"][row],
            exit_reason=columns["exit_reason"][row],
            candles_held=columns["candles_held"][row],
        )
        for row in range(min(per_page, max(count - first, 0)))
    ]
    return TradesPage(
        page=page,
        per_page=per_page,
        pages=-(-count // per_page),
        count=count,
        total=trades["total"],
        complete=trades["complete"],
        items=items,
    )


def _setup(document: dict[str, Any]) -> SetupOut:
    data = document["data"]
    instrument = data["instrument"]
    return SetupOut(
        model=ModelOut.model_validate(document["model"]),
        data=DataSetup(
            instrument=InstrumentOut(
                id=instrument["id"], name=instrument["name"], currency=instrument["currency"]
            ),
            period=PeriodOut.model_validate(data["period"]),
            timeframe=data["timeframe"],
            session=data["session"],
        ),
        decision=Decision.model_validate(document["decision"]),
        execution=Execution.model_validate(document["execution"]),
        benchmarks=[BenchmarkIn.model_validate(item) for item in document["benchmarks"]],
    )


# What the widget draws of a backtest of a comparison, and of the one backtest of get_backtest.
_COMPARED_CURVE = frozenset({"id", "equity"})
_BACKTEST_CURVES = frozenset({"id", "equity", "drawdown", "buy_and_hold"})


def _curves(
    compared: CompareOut, fields: frozenset[str] = _COMPARED_CURVE, shown: int = COMPARE_PAGE_MAX
) -> dict[str, Any]:
    """The curves of the first `shown` backtests of a comparison on its times, as the widget
    draws them."""
    return {
        "times": compared.times,
        "backtests": [
            backtest.model_dump(include=set(fields)) for backtest in compared.backtests[:shown]
        ],
    }


async def _backtest_curves(call: ToolCall, backtest_id: str) -> dict[str, Any] | None:
    """The widget's chart of a finished backtest: its equity, drawdown and buy and hold at
    ``CURVE_POINTS`` times, from the small curves its run stored for comparisons (as the
    Compare page draws them), not from its whole series; None for one that has none (test
    data without a document)."""
    try:
        async with _runs(call) as runs:
            compared = await experiments.compare_backtests(
                runs, call.user.id, [backtest_id], curve_points=CURVE_POINTS, setup_mode="full"
            )
    except ProblemError:
        return None
    return _curves(compared, _BACKTEST_CURVES, shown=1)


def _without_curves(compared: CompareOut) -> CompareOut:
    """The comparison as ``curve_points`` 0 answers it: no times and no curves."""
    empty = {"equity": None, "drawdown": None, "buy_and_hold": None}
    return compared.model_copy(
        update={
            "times": [],
            "backtests": [backtest.model_copy(update=empty) for backtest in compared.backtests],
        }
    )


def backtest_result(
    document: dict[str, Any],
    url: str,
    *,
    trades_page: int = 1,
    trades_per_page: int = 0,
    equity_points: int = 0,
) -> BacktestResult:
    """The compact answer of get_backtest from the light result document: a page of trades
    only with ``trades_per_page`` and the equity only with ``equity_points``."""
    header, data, results = document["backtest"], document["data"], document["results"]
    return BacktestResult(
        backtest=BacktestSummary(
            id=header["id"],
            number=header["number"],
            name=header["name"],
            status=header["status"],
            author=Author.model_validate(header["author"]),
            public=header["public"],
            queue_position=header["queue_position"],
            error=header["error"],
            sweep_id=header.get("sweep_id"),
            created_at=header["created_at"],
            finished_at=header["finished_at"],
            duration_s=header["duration_s"],
            url=url,
        ),
        setup=_setup(document),
        candles=None if data["rows"] is None else data["rows"]["count"],
        data_changed=data["changed"],
        metrics=None if results is None else Stats.model_validate(results["stats"]),
        benchmark_returns={}
        if results is None
        else {key: value["total_return"] for key, value in results["benchmarks"].items()},
        trades=None
        if results is None or trades_per_page == 0
        else _trades(results["trades"], trades_page, trades_per_page),
        equity=None
        if results is None or equity_points == 0
        else _equity(results["series"], equity_points),
    )


# --- The tools --------------------------------------------------------------------------------

BacktestId = Annotated[
    str, Field(description="Public ID of the backtest", examples=["bt_7f3k2m9qx4"])
]
Decisions = Annotated[Decision, Field(default_factory=Decision)]
Executions = Annotated[Execution, Field(default_factory=Execution)]
Benchmarks = Annotated[list[BenchmarkIn], Field(default_factory=list, max_length=1)]


async def validate_backtest(
    model: ModelRef,
    data: DataIn,
    *,
    ctx: Context,
    decision: Decisions,
    execution: Executions,
    benchmarks: Benchmarks,
) -> BacktestCheck:
    """Checks a backtest setup without starting anything: the same checks as start_backtest
    (the model is the user's and has a manifest, the instrument fits the model, the period with
    the candles the model needs before it fits the market data, the queue has room). Call it
    before start_backtest when you built the setup yourself.

    Answers what would run (the model, the instrument, the model's timeframe and session, the
    warm-up) and whether it would wait in the user's queue; a setup that fails a check answers
    the problem start_backtest would, with what to change in its detail. The arguments are
    those of start_backtest.

    What each setting does to the trades: get_docs page engine (sections Decision steps and
    Execution settings)."""
    call = tool_call(ctx)
    body = BacktestIn(
        model=model, data=data, decision=decision, execution=execution, benchmarks=benchmarks
    )
    async with _runs(call) as runs:
        return await runs.check(call.user.id, body)


async def start_backtest(  # noqa: PLR0913 (the setup's groups)
    model: ModelRef,
    data: DataIn,
    *,
    ctx: Context,
    decision: Decisions,
    execution: Executions,
    benchmarks: Benchmarks,
    idempotency_key: IdempotencyKey = None,
) -> BacktestStatusResult:
    """Starts a backtest of one of the user's models on one instrument and period. One
    backtest of a user runs at a time and up to 20 more wait in the queue, and a few run at
    once over all users, taking turns; so it starts running at once or waits (queued, with
    its place and `eta_s`).

    The arguments are the body of POST /api/v1/backtests: model.id (one of the user's models
    with a manifest), data.instrument.id (<market>:<symbol>: a stock for a model of US trading
    hours, a crypto pair for one of 24/7) and data.period ([start, end) in UTC epoch seconds),
    and optionally decision (how the predictions become a signal: threshold 0 by default),
    execution (fills, stop loss, position size, fees and slippage) and benchmarks (buy and
    hold of the instrument, or of SPY, QQQ or DIA for a stock). The `setup` get_backtest
    answers has this shape, so a backtest can be run again with changes.

    Units: execution.stop_loss and position_size are fractions (0.07 is 7 %), fee_bps and
    slippage_bps basis points (10 is 0.1 %), not the percent of the app's Settings.
    get_backtest_defaults answers the user's defaults in these units too (`period`,
    `decision`, `execution`, `benchmark_instrument`): pass them through for every setting the
    user does not name.

    Pass an `idempotency_key` (a UUID you make) to call it again safely after a timeout: the
    same call with the same key within 24 hours answers the first backtest instead of starting
    another.

    Answers its ID and status at once; the run takes from seconds to minutes. Then call
    wait_for_backtest with the ID until it has ended, and get_backtest for the results.

    How the settings become trades: get_docs page engine; how the results are computed:
    get_docs page metrics. Read them before you explain a setting or a result."""
    call = tool_call(ctx)
    body = BacktestIn(
        model=model, data=data, decision=decision, execution=execution, benchmarks=benchmarks
    )

    async def started(committed: Committed) -> bytes:
        async with _runs(call) as runs:
            _, content = await runs.start(call.user.id, body, committed)
        header = BacktestHeader.model_validate(json.loads(content)["backtest"])
        try:
            return await again(header.id)
        except DATABASE_ERRORS:
            # Stored and scheduled already: answered as the start saw it, without the estimate.
            return _status(call, header).model_dump_json().encode()

    async def again(backtest_id: str) -> bytes:
        async with _runs(call) as runs:
            (standing,) = await runs.statuses(call.user.id, [backtest_id])
        return _status(call, standing.header, standing.eta_s).model_dump_json().encode()

    with backtest_problems():
        content, _ = await idempotent(
            call.state.redis,
            user_id=call.user.id,
            kind="start_backtest",
            key=idempotency_key,
            body=body.model_dump_json().encode(),
            answer=started,
            rebuild=again,
        )
    return BacktestStatusResult.model_validate_json(content)


def progress_of(status: BacktestStatusResult) -> float:
    """How far the backtest is, 0 to 1, from where it stands, as its page shows it: the user's
    queue is the first fifth, nearer its end the nearer its turn; then each step a fifth, the
    candles' load by its chunks; 1 once it ended."""
    if status.ended:
        return 1.0
    if status.status == "queued":
        position = status.queue_position
        return 1 / (position + 1) / _STAGES if position else 0.0
    done = sum(step.state == "done" for step in status.steps)
    running = next((step for step in status.steps if step.state == "running"), None)
    part = 0.0
    if running is not None and running.id == "candles" and running.total:
        part = min((running.loaded or 0) / running.total, 1.0)
    return (1 + done + part) / _STAGES


def stage_of(status: BacktestStatusResult) -> str:
    """Where the backtest is, in words, for the progress' message."""
    if status.status == "queued":
        position = status.queue_position
        return f"queued, number {position} in the queue" if position else "queued"
    running = next((step for step in status.steps if step.state == "running"), None)
    if status.ended or running is None:
        return status.status
    if running.id != "candles":
        return _DOING[running.id]
    stage = "loading candles"
    if running.total:
        stage += f", {running.loaded or 0} of {running.total} chunks"
    if running.waiting_until is not None:
        stage += ", waiting for the data source's limit"
    return stage


def wait_progress(
    statuses: Sequence[BacktestStatusResult],
    shown: BacktestStatusResult,
    until: Literal["any", "all"],
) -> tuple[float, float, str]:
    """The progress of a wait, in percent of 100, and its message: one backtest's, or with
    several the mean of theirs (`until` all) or the furthest (any)."""
    parts = [progress_of(status) for status in statuses]
    part = max(parts) if until == "any" else sum(parts) / len(parts)
    message = f"{shown.name} (#{shown.number}): {stage_of(shown)}"
    if len(statuses) > 1:
        ended = sum(status.ended for status in statuses)
        message = f"{ended} of {len(statuses)} ended; {message}"
    return round(100 * part, 1), 100.0, message


async def get_backtest_status(
    backtest_id: BacktestId,
    *,
    ctx: Context,
) -> BacktestStatusResult:
    """Where a backtest stands right now: queued with its place in the queue, running with
    its steps (loading candles, waiting for the worker, the model, the trades), finished or
    failed with the error. It does not wait; to wait for the end, call wait_for_backtest."""
    call = tool_call(ctx)
    return _status(call, await _header(call, backtest_id))


async def wait_for_backtest(
    backtest_id: Annotated[
        str | None,
        Field(description="Public ID of the backtest; or give `ids`", examples=["bt_7f3k2m9qx4"]),
    ] = None,
    timeout_s: Annotated[
        int,
        Field(
            ge=1,
            le=WAIT_MAX_S,
            description=f"How long to wait at most, in seconds (up to {WAIT_MAX_S})",
        ),
    ] = WAIT_DEFAULT_S,
    ids: Annotated[
        list[str] | None,
        Field(
            min_length=1,
            max_length=WAIT_IDS_MAX,
            description=f"Instead of `backtest_id`: up to {WAIT_IDS_MAX} backtest IDs to wait "
            "for at once",
        ),
    ] = None,
    until: Annotated[
        Literal["any", "all"],
        Field(description="With `ids`: wait until any of them ended, or all of them"),
    ] = "all",
    *,
    ctx: Context,
) -> BacktestWaitResult:
    """Waits until a backtest has ended (finished or failed) or `timeout_s` seconds passed,
    then answers where it stands. Give `ids` instead of `backtest_id` to wait for several at
    once, until any or all of them ended (`until`); `backtests` then has each one. When `done`
    is false, call it again: a backtest can wait in the queue and run for several minutes, and
    `eta_s` says about how long. Once it finished, read it with get_backtest. A client that
    asks for progress gets where it stands while it waits."""
    call = tool_call(ctx)
    wanted = _wanted(backtest_id, ids)
    deadline = anyio.current_time() + timeout_s
    progress = Progress(ctx)
    with runner_of(call).watch(wanted) as changed:
        while True:
            changed.clear()
            statuses = await _statuses(call, wanted)
            shown, done = _decided(statuses, until)
            await progress.report(*wait_progress(statuses, shown, until))
            left = deadline - anyio.current_time()
            if done or left <= 0:
                break
            with anyio.move_on_after(min(POLL_S, left)):
                await changed.wait()
    return BacktestWaitResult(
        **shown.model_dump(), done=done, backtests=None if ids is None else statuses
    )


async def get_backtest(
    backtest_id: BacktestId,
    trades_page: Annotated[int, Field(ge=1, description="The page of trades, from 1")] = 1,
    trades_per_page: Annotated[
        int,
        Field(
            ge=0,
            le=TRADES_PER_PAGE_MAX,
            description=f"Trades per page, up to {TRADES_PER_PAGE_MAX}; 0 for no trades",
        ),
    ] = 0,
    equity_points: Annotated[
        int,
        Field(
            ge=0,
            le=EQUITY_POINTS,
            description=f"Points of the equity curve, up to {EQUITY_POINTS}; 0 for no curve",
        ),
    ] = 0,
    *,
    ctx: Context,
) -> BacktestResult:
    """Reads a backtest, the user's own or one another account published: its status, its
    setup (which can be sent to start_backtest again, changed or not) and, once it finished,
    the 12 statistics and the benchmarks' returns, with a link to its page in the app.

    That light answer is the default. For its trades (oldest first) pass `trades_per_page`
    (e.g. 50) and `trades_page`; a page past the last one is empty. For the equity curve pass
    `equity_points` (up to 200). Call it after wait_for_backtest answered finished, or for a
    backtest the user names; it reads what the run stored, so it is quick.

    What each statistic means and how it is computed: get_docs page metrics; the problem of a
    failed backtest: get_docs page api/errors."""
    call = tool_call(ctx)
    async with _runs(call) as runs:
        _, document = await runs.light(
            call.user.id, backtest_id, series=equity_points > 0, trades=trades_per_page > 0
        )
    # A client that shows the widget gets the curves for its chart, not for the model.
    if document["results"] is not None and shows_widget(ctx):
        chart = await _backtest_curves(call, backtest_id)
        if chart is not None:
            attach(ctx, chart)
    return backtest_result(
        document,
        backtest_url(call, backtest_id),
        trades_page=trades_page,
        trades_per_page=trades_per_page,
        equity_points=equity_points,
    )


async def compare_backtests(
    ids: Annotated[
        list[str],
        Field(
            min_length=1,
            max_length=COMPARE_MAX,
            description=f"1 to {COMPARE_MAX} backtest IDs: the user's own and published ones",
            examples=[["bt_7f3k2m9qx4", "bt_t9n8v7d6a5"]],
        ),
    ],
    curve_points: Annotated[
        int,
        Field(
            ge=0,
            le=CURVE_POINTS,
            description=f"Times of the curves, up to {CURVE_POINTS}; 0 for none",
        ),
    ] = 0,
    setup_mode: Annotated[
        SetupMode,
        Field(
            description="full: each backtest's whole setup. diff: what all share once in "
            "`shared_setup`, each backtest's `setup` only its fields of `differs`"
        ),
    ] = "full",
    *,
    ctx: Context,
) -> CompareResult:
    """Compares backtests side by side, as the app's Compare page does: each of the 12
    statistics of each with the best and the worst marked (values that read the same count as
    equal), their setups and which setup fields differ. Use it to pick the best of several
    runs; it reads only what each stored, so it is quick. A running or failed backtest is
    listed with its status and no values. Each backtest has its `url`, the page to show the
    user, and `url` is the app's Compare page with 2 to 4 of them: give it to the user to see
    the curves and the table.

    For the equity, drawdown and buy and hold of each on one time axis pass `curve_points` (up
    to 200); without it the curves are null and `times` empty. With many similar backtests
    pass `setup_mode="diff"` to read the shared setup once.

    max_drawdown is 0 or below and a smaller fall is better: -0.05 is better than -0.20, so its
    best value is the highest one; its `better` says `lower` as in a lower fall, by its size.
    The marks already account for it.

    What each statistic means and how it is computed: get_docs page metrics; how sweeps and
    comparisons rank: get_docs page sweeps."""
    call = tool_call(ctx)
    # A client that shows the widget gets the curves of those it draws, not for the model; the
    # widget draws the ones the model asked for (`curve_points`) as they are.
    chart = curve_points == 0 and shows_widget(ctx)
    async with _runs(call) as runs:
        compared = await experiments.compare_backtests(
            runs,
            call.user.id,
            check_ids(ids),
            curve_points=CURVE_POINTS if chart else curve_points,
            setup_mode=setup_mode,
        )
    if chart:
        attach(ctx, _curves(compared))
        compared = _without_curves(compared)
    return CompareResult(
        **compared.model_dump(exclude={"backtests"}),
        backtests=[
            ComparedBacktestOut(**backtest.model_dump(), url=backtest_url(call, backtest.id))
            for backtest in compared.backtests
        ],
        url=compare_url(call, [backtest.id for backtest in compared.backtests]),
    )


# --- Examples for the tool reference ----------------------------------------------------------

_ID = "bt_7f3k2m9qx4"
_URL = f"https://app.candlestack.tech/backtests/{_ID}"
_START, _END = 1_717_372_800, 1_725_148_800  # 2024-06-03 to 2024-09-01
_CREATED = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
_SETUP_ARGUMENTS: dict[str, Any] = {
    "model": {"id": "mdl_k2m9qx47f3"},
    "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": _START, "end": _END}},
    "decision": {"rule": {"name": "threshold", "limit": 0.1}, "long_only": True},
    "execution": {"stop_loss": 0.05, "fee_bps": 5},
    "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
}


def _steps(*states: StepState) -> list[StepOut]:
    ids: tuple[StepId, ...] = ("candles", "queue", "model", "trades")
    return [
        StepOut(id=step, state=state, duration_s=None, loaded=None, total=None, waiting_until=None)
        for step, state in zip(ids, states, strict=True)
    ]


_QUEUED = BacktestStatusResult(
    id=_ID,
    number=12,
    name="Silent Orion",
    status="queued",
    ended=False,
    queue_position=1,
    error=None,
    created_at=_CREATED,
    started_at=None,
    finished_at=None,
    duration_s=None,
    steps=_steps("pending", "pending", "pending", "pending"),
    eta_s=324.0,
    url=_URL,
)
_FINISHED = _QUEUED.model_copy(
    update={
        "status": "finished",
        "ended": True,
        "queue_position": None,
        "started_at": datetime(2026, 11, 2, 9, 31, tzinfo=UTC),
        "finished_at": datetime(2026, 11, 2, 9, 33, 42, tzinfo=UTC),
        "duration_s": 162.0,
        "eta_s": None,
        "steps": [
            step.model_copy(update={"duration_s": seconds})
            for step, seconds in zip(
                _steps("done", "done", "done", "done"), (4.2, 0.3, 151.0, 6.5), strict=True
            )
        ],
    }
)
_RUNNING = _QUEUED.model_copy(
    update={
        "status": "running",
        "queue_position": None,
        "started_at": datetime(2026, 11, 2, 9, 31, tzinfo=UTC),
        "eta_s": 150.0,
        "steps": [
            StepOut(
                id="candles",
                state="running",
                duration_s=None,
                loaded=37,
                total=128,
                waiting_until=None,
            ),
            *_steps("pending", "pending", "pending", "pending")[1:],
        ],
    }
)
_CHECK = BacktestCheck.model_validate(
    {
        "model": {"id": "mdl_k2m9qx47f3", "name": "raw-candles.keras", "framework": "keras"},
        "instrument": {
            "id": "stock:AAPL",
            "market": "stock",
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASDAQ",
            "source": "alpaca",
            "feed": "sip",
            "currency": "USD",
        },
        "timeframe": "30m",
        "session": "regular",
        "period": {"start": _START, "end": _END},
        "warm_up": {"candles": 24, "kind": "exact", "load_start": _START - 3 * 86_400},
        "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
        "starts": "now",
        "queue_position": None,
    }
)
_STATS = Stats(
    total_return=0.0812,
    max_drawdown=-0.0431,
    sharpe=1.42,
    sortino=2.05,
    num_trades=2,
    win_rate=0.5,
    avg_trade_return=0.0398,
    best_trade_return=0.0921,
    worst_trade_return=-0.0125,
    exposure=0.37,
    fees_paid=0.002,
    buy_and_hold_return=0.0544,
)
_RESULT = BacktestResult(
    backtest=BacktestSummary(
        id=_ID,
        number=12,
        name="Silent Orion",
        status="finished",
        author=Author(id="usr_7f3k2m9qx4", name="Ada Lovelace", mine=True),
        public=False,
        queue_position=None,
        error=None,
        sweep_id=None,
        created_at=_CREATED,
        finished_at=datetime(2026, 11, 2, 9, 33, 42, tzinfo=UTC),
        duration_s=162.0,
        url=_URL,
    ),
    setup=SetupOut(
        model=ModelOut(id="mdl_k2m9qx47f3", name="raw-candles.keras", framework="keras"),
        data=DataSetup(
            instrument=InstrumentOut(id="stock:AAPL", name="Apple Inc.", currency="USD"),
            period=PeriodOut(start=_START, end=_END),
            timeframe="30m",
            session="regular",
        ),
        decision=Decision.model_validate(_SETUP_ARGUMENTS["decision"]),
        execution=Execution.model_validate(_SETUP_ARGUMENTS["execution"]),
        benchmarks=[BenchmarkIn.model_validate(_SETUP_ARGUMENTS["benchmarks"][0])],
    ),
    candles=1_118,
    data_changed=False,
    metrics=_STATS,
    benchmark_returns={"bh": 0.0544},
    trades=TradesPage(
        page=1,
        per_page=50,
        pages=1,
        count=2,
        total=2,
        complete=True,
        items=[
            TradeOut(
                open_ts=1_717_421_400,
                close_ts=1_718_112_600,
                side=1,
                open_price=194.03,
                close_price=211.94,
                trade_return=0.0921,
                fees=0.001,
                exit_reason="signal",
                candles_held=91,
            ),
            TradeOut(
                open_ts=1_721_136_600,
                close_ts=1_721_309_400,
                side=1,
                open_price=235.16,
                close_price=223.40,
                trade_return=-0.0125,
                fees=0.001,
                exit_reason="stop",
                candles_held=23,
            ),
        ],
    ),
    equity=EquityOut(
        points=3,
        rows=1_118,
        resolution="candle",
        ts=[1_717_421_400, 1_721_309_400, 1_725_031_800],
        equity=[1.0, 1.0431, 1.0812],
        drawdown=[0.0, -0.0431, 0.0],
        benchmarks={"bh": [1.0, 1.1213, 1.0544]},
    ),
)
_COMPARE = CompareResult.model_validate(
    {
        "backtests": [
            {
                "id": _ID,
                "number": 12,
                "name": "Silent Orion",
                "author": {"id": "usr_7f3k2m9qx4", "name": "Ada Lovelace", "mine": True},
                "public": False,
                "status": "finished",
                "error": None,
                "setup": {"instrument": "stock:AAPL", "decision": {"rule": {"limit": 0.1}}},
                "candles": 1_118,
                "equity": [0.0, 0.0812],
                "drawdown": [0.0, 0.0],
                "buy_and_hold": [0.0, 0.0544],
                "url": _URL,
            },
            {
                "id": "bt_t9n8v7d6a5",
                "number": 13,
                "name": "Quiet Vega",
                "author": {"id": "usr_7f3k2m9qx4", "name": "Ada Lovelace", "mine": True},
                "public": False,
                "status": "finished",
                "error": None,
                "setup": {"instrument": "stock:AAPL", "decision": {"rule": {"limit": 0.2}}},
                "candles": 1_118,
                "equity": [0.0, -0.031],
                "drawdown": [0.0, -0.05],
                "buy_and_hold": [0.0, 0.0544],
                "url": "https://app.candlestack.tech/backtests/bt_t9n8v7d6a5",
            },
        ],
        "metrics": [
            {
                "key": "total_return",
                "label": "Total return",
                "better": "higher",
                "values": [0.0812, -0.031],
                "marks": ["best", "worst"],
            }
        ],
        "differs": ["decision.rule.limit"],
        "times": [_START, _END - 1_800],
        "url": f"https://app.candlestack.tech/backtests/compare?ids={_ID},bt_t9n8v7d6a5",
    }
)


def _get_backtest_cost(arguments: dict[str, Any]) -> int:
    """``get_backtest``'s cost: its default answer (setup, metrics, benchmark returns) is read
    from stored data and costs 1; asking for trades or equity points costs ``HEAVY``."""
    asks_more = arguments.get("trades_per_page") or arguments.get("equity_points")
    return HEAVY if asks_more else 1


TOOLS = (
    ToolSpec(
        validate_backtest,
        name="validate_backtest",
        title="Check a backtest setup",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments=_SETUP_ARGUMENTS, result=_CHECK),
    ),
    ToolSpec(
        start_backtest,
        name="start_backtest",
        title="Start a backtest",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=False),
        example=Example(arguments=_SETUP_ARGUMENTS, result=_QUEUED),
        cost=HEAVY,
    ),
    ToolSpec(
        wait_for_backtest,
        name="wait_for_backtest",
        title="Wait for a backtest",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"backtest_id": _ID, "timeout_s": 50},
            result=BacktestWaitResult(**_FINISHED.model_dump(), done=True, backtests=None),
        ),
    ),
    ToolSpec(
        get_backtest_status,
        name="get_backtest_status",
        title="Backtest status",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"backtest_id": _ID}, result=_RUNNING),
    ),
    ToolSpec(
        get_backtest,
        name="get_backtest",
        title="Read a backtest",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"backtest_id": _ID, "trades_per_page": 50, "equity_points": 3},
            result=_RESULT,
        ),
        cost=_get_backtest_cost,
        meta=tool_meta("Reading the backtest", "Read the backtest"),
    ),
    ToolSpec(
        compare_backtests,
        name="compare_backtests",
        title="Compare backtests",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"ids": [_ID, "bt_t9n8v7d6a5"], "curve_points": 2}, result=_COMPARE
        ),
        cost=HEAVY,
        meta=tool_meta("Comparing the backtests", "Compared the backtests"),
    ),
)
