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
from candlestack.accounts import Scope
from candlestack.experiments import (
    COMPARE_MAX,
    CURVE_POINTS,
    BacktestCheck,
    BacktestHeader,
    BacktestIn,
    BacktestRuns,
    BenchmarkIn,
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
)
from candlestack.mcp.convention import (
    Example,
    ToolCall,
    ToolSpec,
    backtest_url,
    changes,
    reads,
    tool_call,
)

# The longest wait of one call of wait_for_backtest: clients give up on a call after a minute
# or so, and the transport answers JSON at the end of the call, so the agent calls again.
WAIT_MAX_S = 60
WAIT_DEFAULT_S = 50
# How often a wait reads the backtest's row again.
POLL_S = 1.0
# The most points of the equity get_backtest answers: enough to see its shape.
EQUITY_POINTS = 200
TRADES_PER_PAGE_MAX = 500
_ENDED = {"finished", "failed"}
# Values of the curves to six decimals: a millionth of the starting capital.
_DECIMALS = 6

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
        description="queued (waits for the user's running backtest), running, finished (has "
        "results) or failed (has an error, also when cancelled before it ran)"
    )
    ended: bool = Field(description="finished or failed: it will not change any more")
    queue_position: int | None = Field(
        description="While queued: 1 starts next, when the running one ends"
    )
    error: str | None = Field(description="What went wrong and what to do, for a failed one")
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None = Field(description="Seconds it ran, the wait in the queue left out")
    steps: list[StepOut]
    url: str = Field(description="The backtest's page in the app, to show the user")


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
    created_at: datetime
    finished_at: datetime | None
    duration_s: float | None
    url: str = Field(description="The backtest's page in the app, to show the user")


class ComparedBacktestOut(ComparedBacktest):
    url: str = Field(description="The backtest's page in the app, to show the user")


class CompareResult(CompareOut):
    """Backtests side by side, in the order asked for, each with its page in the app."""

    backtests: list[ComparedBacktestOut]


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


def _status(call: ToolCall, header: BacktestHeader) -> BacktestStatusResult:
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
        url=backtest_url(call, header.id),
    )


async def _header(call: ToolCall, backtest_id: str) -> BacktestHeader:
    async with _runs(call) as runs:
        return await runs.header(call.user.id, backtest_id)


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
    those of start_backtest."""
    call = tool_call(ctx)
    body = BacktestIn(
        model=model, data=data, decision=decision, execution=execution, benchmarks=benchmarks
    )
    async with _runs(call) as runs:
        return await runs.check(call.user.id, body)


async def start_backtest(
    model: ModelRef,
    data: DataIn,
    *,
    ctx: Context,
    decision: Decisions,
    execution: Executions,
    benchmarks: Benchmarks,
) -> BacktestStatusResult:
    """Starts a backtest of one of the user's models on one instrument and period. One
    backtest of a user runs at a time and up to 20 more wait in the queue, so it starts
    running at once or waits (queued, with its place).

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

    Answers its ID and status at once; the run takes from seconds to minutes. Then call
    wait_for_backtest with the ID until it has ended, and get_backtest for the results."""
    call = tool_call(ctx)
    body = BacktestIn(
        model=model, data=data, decision=decision, execution=execution, benchmarks=benchmarks
    )
    async with _runs(call) as runs:
        _, content = await runs.start(call.user.id, body)
    header = BacktestHeader.model_validate(json.loads(content)["backtest"])
    return _status(call, header)


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
    backtest_id: BacktestId,
    timeout_s: Annotated[
        int,
        Field(
            ge=1,
            le=WAIT_MAX_S,
            description=f"How long to wait at most, in seconds (up to {WAIT_MAX_S})",
        ),
    ] = WAIT_DEFAULT_S,
    *,
    ctx: Context,
) -> BacktestStatusResult:
    """Waits until a backtest has ended (finished or failed) or `timeout_s` seconds passed,
    then answers where it stands. When `ended` is false, call it again: a backtest can wait in
    the queue and run for several minutes. Once it finished, read it with get_backtest."""
    call = tool_call(ctx)
    deadline = anyio.current_time() + timeout_s
    while True:
        header = await _header(call, backtest_id)
        left = deadline - anyio.current_time()
        if header.status.value in _ENDED or left <= 0:
            return _status(call, header)
        await anyio.sleep(min(POLL_S, left))


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
    backtest the user names; it reads what the run stored, so it is quick."""
    call = tool_call(ctx)
    async with _runs(call) as runs:
        _, document = await runs.light(
            call.user.id, backtest_id, series=equity_points > 0, trades=trades_per_page > 0
        )
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
    user.

    For the equity, drawdown and buy and hold of each on one time axis pass `curve_points` (up
    to 200); without it the curves are null and `times` empty. With many similar backtests
    pass `setup_mode="diff"` to read the shared setup once.

    max_drawdown is 0 or below and a smaller fall is better: -0.05 is better than -0.20, so its
    best value is the highest one; its `better` says `lower` as in a lower fall, by its size.
    The marks already account for it."""
    call = tool_call(ctx)
    async with _runs(call) as runs:
        compared = await experiments.compare_backtests(
            runs, call.user.id, check_ids(ids), curve_points=curve_points, setup_mode=setup_mode
        )
    return CompareResult(
        **compared.model_dump(exclude={"backtests"}),
        backtests=[
            ComparedBacktestOut(**backtest.model_dump(), url=backtest_url(call, backtest.id))
            for backtest in compared.backtests
        ],
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
    }
)

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
    ),
    ToolSpec(
        wait_for_backtest,
        name="wait_for_backtest",
        title="Wait for a backtest",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"backtest_id": _ID, "timeout_s": 50}, result=_FINISHED),
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
    ),
)
