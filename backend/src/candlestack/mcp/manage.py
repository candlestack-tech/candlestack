"""The tools that find, organise and publish the user's backtests: the list of the Backtests
page, rename, star, cancel a queued one, Experiments, publish and unpublish, and the Dashboard.

They call ``BacktestsService`` and ``BacktestRuns`` as the routes do, raise the routes' problems
(``update_problems``, ``backtest_problems``) and answer compact models: a backtest without its
equity series and the other fields only the web app's cards and charts use.
"""

import json
from datetime import UTC, date, datetime
from typing import Annotated

from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field, StringConstraints

from candlestack.accounts import Scope
from candlestack.experiments import (
    BACKTEST_NAME_MAX,
    EXPERIMENTS_PAGE_SIZE,
    MODEL_NAME_MAX,
    PAGE_MAX,
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
    ExperimentsQuery,
    InstrumentMarket,
    ListQuery,
    ModelCount,
    Representation,
    SetupTimeframe,
    SortOrder,
    backtest_problems,
    backtest_runs,
    get_backtests_service,
    update_problems,
)
from candlestack.mcp.convention import Example, ToolSpec, changes, reads, tool_call

# --- Arguments ------------------------------------------------------------------------------

BacktestId = Annotated[
    str,
    Field(description="Public ID of the backtest, from list_backtests", examples=["bt_7f3k2m9qx4"]),
]
Page = Annotated[int, Field(ge=1, le=PAGE_MAX, description="The page, from 1")]
Order = Annotated[
    SortOrder, Field(description="Direction of the sort; the default sort is newest first anyway")
]
# What the Rename field takes; surrounding spaces are dropped first, as the app does.
BacktestName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=BACKTEST_NAME_MAX,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    ),
]

# --- Results --------------------------------------------------------------------------------


class BacktestSummary(BaseModel):
    """A backtest of the user. Returns and rates are fractions (0.246 is 24.6 %); the results
    are null for a backtest that did not finish."""

    id: str = Field(description="Public ID of the backtest")
    number: int = Field(description="The user's own number of the backtest, shown as #N")
    name: str = Field(description="Its name, unique in the account ignoring case")
    status: BacktestStatus
    queue_position: int | None = Field(
        description="Where a queued backtest is in the queue, 1 starts next; else null"
    )
    error: str | None = Field(description="What went wrong, for a failed backtest; else null")
    model: str = Field(description="Name of the model it ran")
    model_id: str | None = Field(description="Public ID of the model; null once it was deleted")
    instruments: list[BacktestInstrument]
    representation: Representation = Field(description="What the model reads")
    setup: BacktestSetup
    total_return: float | None = Field(description="Return of the strategy")
    buy_and_hold_return: float | None = Field(description="Return of holding the instruments")
    sharpe: float | None = Field(description="Sharpe ratio, per year")
    max_drawdown: float | None = Field(description="Largest fall from a peak, negative")
    win_rate: float | None = Field(description="Share of the trades that made money")
    num_trades: int | None
    starred: bool
    public: bool = Field(description="Published in Experiments, visible to every beta account")
    created_at: datetime = Field(description="When it was started (UTC)")


class BacktestList(BaseModel):
    """A page of the user's backtests that match, and the filter choices."""

    backtests: list[BacktestSummary] = Field(description=f"At most {PAGE_SIZE} backtests")
    total: int = Field(description="Backtests that match the search and the filters")
    page: int
    pages: int = Field(description="Pages of the backtests that match, at least 1")
    counts: BacktestCounts = Field(description="All the user's backtests, whatever the filters")
    models: list[ModelCount] = Field(
        description="Every model the backtests used, the most used first: the values of `model`"
    )


class BacktestState(BaseModel):
    """The backtest after the change."""

    id: str
    number: int
    name: str = Field(description="Its name now")
    status: BacktestStatus
    starred: bool
    public: bool = Field(description="Published in Experiments, visible to every beta account")


class CancelledBacktest(BaseModel):
    """The backtest after it was cancelled: `failed`, with an error that says so."""

    id: str
    number: int
    name: str
    status: BacktestStatus
    error: str | None


class ExperimentSummary(BaseModel):
    """A published backtest as every beta account sees it: its setup and results, never the
    model file. Returns and rates are fractions."""

    id: str = Field(description="Public ID of the backtest; get_backtest reads it")
    number: int = Field(description="The author's own number of the backtest")
    name: str
    author: Author
    model: str = Field(description="Name of the model when it ran")
    instruments: list[BacktestInstrument]
    representation: Representation
    setup: BacktestSetup
    total_return: float | None
    benchmark_return: float | None = Field(description="Return of `setup.benchmark`")
    buy_and_hold_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    win_rate: float | None
    num_trades: int | None
    published_at: datetime


class ExperimentList(BaseModel):
    """A page of Experiments that match, the counts and what the filters offer."""

    experiments: list[ExperimentSummary] = Field(
        description=f"At most {EXPERIMENTS_PAGE_SIZE} published backtests"
    )
    total: int = Field(description="Published backtests that match")
    page: int
    pages: int = Field(description="Pages of those that match, at least 1")
    counts: ExperimentCounts
    facets: ExperimentFacets = Field(
        description="The values each filter offers, with counts, over the scope and the search"
    )


class BestBacktests(BaseModel):
    """The 5 best finished backtests by each result, the best first (for `max_drawdown` the
    smallest fall)."""

    sharpe: list[BacktestSummary]
    total_return: list[BacktestSummary]
    max_drawdown: list[BacktestSummary]
    win_rate: list[BacktestSummary]


class Dashboard(BaseModel):
    """The Dashboard of the user's backtests."""

    counts: BacktestCounts
    newest: list[BacktestSummary] = Field(
        description="The 6 newest backtests that ended, failed ones included"
    )
    best: BestBacktests
    starred: list[BacktestSummary] = Field(
        description="The 4 starred backtests with the best Sharpe ratio"
    )


def summary(backtest: BacktestOut) -> BacktestSummary:
    fields = backtest.model_dump(include=set(BacktestSummary.model_fields) - {"model"})
    return BacktestSummary(**fields, model=backtest.model.name, model_id=backtest.model.id)


def experiment_summary(experiment: ExperimentOut) -> ExperimentSummary:
    fields = experiment.model_dump(include=set(ExperimentSummary.model_fields) - {"model"})
    return ExperimentSummary(**fields, model=experiment.model.name)


def _state(backtest: BacktestOut) -> BacktestState:
    return BacktestState.model_validate(
        backtest.model_dump(include=set(BacktestState.model_fields))
    )


# --- The tools ------------------------------------------------------------------------------


async def list_backtests(  # noqa: PLR0913 (one argument per filter, as GET /backtests)
    ctx: Context,
    *,
    query: Annotated[
        str,
        Field(
            max_length=QUERY_MAX,
            description="Search: part of the name, of the number (#12 or 12), of an "
            "instrument's symbol or of the model's name, in any case",
        ),
    ] = "",
    show: Annotated[
        BacktestShow, Field(description="All, the starred, the failed or the published ones")
    ] = BacktestShow.all,
    model: Annotated[
        str | None,
        Field(
            max_length=MODEL_NAME_MAX,
            description="Only the backtests of the model with this name (one of `models`)",
        ),
    ] = None,
    sort: Annotated[
        BacktestSort,
        Field(description="What to sort by; those without the value (failed ones) come last"),
    ] = BacktestSort.default,
    order: Order = SortOrder.desc,
    page: Page = 1,
) -> BacktestList:
    """Lists the user's backtests as the Backtests page does: search, show the starred, failed
    or published ones, one model's, sort by a result, 10 a page. Use it to find a backtest's ID
    by its name, number, instrument or model, or the best ones by a result. Answers the page,
    how many match, the counts of all, starred, failed and published backtests and the models
    used. A page past the last one is empty."""
    call = tool_call(ctx)
    list_query = ListQuery(query=query, show=show, model=model, sort=sort, order=order, page=page)
    async with call.db() as db:
        found = await get_backtests_service(db).backtests(call.user.id, list_query)
    return BacktestList(
        backtests=[summary(backtest) for backtest in found.backtests],
        total=found.total,
        page=found.page,
        pages=found.pages,
        counts=found.counts,
        models=found.models,
    )


async def _update(ctx: Context, backtest_id: str, update: BacktestUpdate) -> BacktestState:
    call = tool_call(ctx)
    async with call.db() as db:
        with update_problems():
            changed = await get_backtests_service(db).update(call.user.id, backtest_id, update)
    return _state(changed)


async def rename_backtest(
    backtest_id: BacktestId,
    name: Annotated[
        BacktestName | None,
        Field(
            description=f"The new name, 1 to {BACKTEST_NAME_MAX} characters, unique in the "
            "account ignoring case; null goes back to the name it got when it was created",
        ),
    ],
    ctx: Context,
) -> BacktestState:
    """Renames one of the user's backtests, or with a name of null gives it back the name it
    got when it was created. Answers the problem name-taken when another backtest of the user
    has the name, in any case."""
    return await _update(ctx, backtest_id, BacktestUpdate(name=name))


async def star_backtest(
    backtest_id: BacktestId,
    ctx: Context,
    *,
    starred: Annotated[bool, Field(description="true stars it, false unstars it")] = True,
) -> BacktestState:
    """Stars one of the user's backtests, or unstars it with starred false. Starred backtests
    are listed with show starred in list_backtests and on the Dashboard."""
    return await _update(ctx, backtest_id, BacktestUpdate(starred=starred))


async def cancel_backtest(backtest_id: BacktestId, ctx: Context) -> CancelledBacktest:
    """Cancels one of the user's queued backtests, so it never runs: it becomes failed, with an
    error that says it was cancelled, and stays in the list. Only a queued backtest can be
    cancelled; a running or ended one answers the problem backtest-not-queued."""
    call = tool_call(ctx)
    async with call.db() as db:
        runs = backtest_runs(call.state, db, call.settings.app_version)
        with backtest_problems():
            document = await runs.cancel(call.user.id, backtest_id)
    return CancelledBacktest.model_validate(json.loads(document)["backtest"])


async def list_experiments(  # noqa: PLR0913 (one argument per filter, as GET /experiments)
    ctx: Context,
    *,
    query: Annotated[
        str,
        Field(
            max_length=QUERY_MAX,
            description="Search: part of the backtest's name, of an instrument's symbol, of "
            "the model's name or of the author's name, in any case",
        ),
    ] = "",
    mine: Annotated[bool, Field(description="Only the user's own published backtests")] = False,
    author: Annotated[
        str | None,
        Field(max_length=32, description="Only this account's (`author.id`, usr_...)"),
    ] = None,
    market: Annotated[InstrumentMarket | None, Field(description="Only this market")] = None,
    symbol: Annotated[
        str | None, Field(max_length=32, description="Only this instrument's symbol")
    ] = None,
    representation: Annotated[
        Representation | None, Field(description="Only models that read this")
    ] = None,
    timeframe: Annotated[SetupTimeframe | None, Field(description="Only this timeframe")] = None,
    sort: Annotated[
        ExperimentSort,
        Field(description="What to sort by; newest is the newest published first"),
    ] = ExperimentSort.newest,
    order: Order = SortOrder.desc,
    page: Page = 1,
) -> ExperimentList:
    """Lists Experiments: the backtests that accounts of the beta published, which every
    signed-in account can see, with search, filters (author, market, symbol, representation,
    timeframe), sort and 12 a page. Use it to find what others tried and how it did, then
    read one with its ID. Answers the page, how many match, the counts and the values each
    filter offers. A page past the last one is empty."""
    call = tool_call(ctx)
    experiments_query = ExperimentsQuery(
        query=query,
        mine=mine,
        author=author,
        market=market,
        symbol=symbol,
        representation=representation,
        timeframe=timeframe,
        sort=sort,
        order=order,
        page=page,
    )
    async with call.db() as db:
        found = await get_backtests_service(db).experiments(call.user.id, experiments_query)
    return ExperimentList(
        experiments=[experiment_summary(experiment) for experiment in found.experiments],
        total=found.total,
        page=found.page,
        pages=found.pages,
        counts=found.counts,
        facets=found.facets,
    )


async def publish_backtest(backtest_id: BacktestId, ctx: Context) -> BacktestState:
    """Publishes one of the user's finished backtests in Experiments: it becomes visible to
    every account of the beta, with its name, the user's display name, its setup, results,
    charts and trades and its model's name and input (never the model file), and others can
    compare with it. Ask the user before publishing. Publishing one that is published already
    changes nothing. A queued, running or failed backtest answers backtest-not-finished."""
    return await _update(ctx, backtest_id, BacktestUpdate(public=True))


async def unpublish_backtest(backtest_id: BacktestId, ctx: Context) -> BacktestState:
    """Takes one of the user's backtests out of Experiments: no other account can see it any
    more, and it leaves their comparisons. The backtest itself stays. Ask the user first."""
    return await _update(ctx, backtest_id, BacktestUpdate(public=False))


async def get_dashboard(ctx: Context) -> Dashboard:
    """The user's Dashboard in one call: the counts of all, starred, failed and published
    backtests, the 6 newest that ended, the 5 best finished ones by Sharpe ratio, return,
    drawdown and win rate, and the 4 best starred ones. Call it for an overview before
    digging in with list_backtests."""
    call = tool_call(ctx)
    async with call.db() as db:
        found = await get_backtests_service(db).dashboard(call.user.id)
    best = found.best
    return Dashboard(
        counts=found.counts,
        newest=[summary(backtest) for backtest in found.last],
        best=BestBacktests(
            sharpe=[summary(backtest) for backtest in best.sharpe],
            total_return=[summary(backtest) for backtest in best.total_return],
            max_drawdown=[summary(backtest) for backtest in best.max_drawdown],
            win_rate=[summary(backtest) for backtest in best.win_rate],
        ),
        starred=[summary(backtest) for backtest in found.starred.backtests],
    )


# --- Examples for the tool reference --------------------------------------------------------

_SETUP = BacktestSetup.model_validate(
    {
        "timeframe": "1h",
        "period_start": date(2023, 1, 2),
        "period_end": date(2025, 12, 31),
        "direction": "long",
        "threshold": 0.05,
        "stop_loss_pct": 7,
        "fees_pct": 0.1,
        "slippage_pct": 0.02,
        "benchmark": "bh",
    }
)
_AAPL = BacktestInstrument(market=InstrumentMarket.stock, symbol="AAPL")
_ORION = BacktestSummary(
    id="bt_7f3k2m9qx4",
    number=12,
    name="Silent Orion",
    status=BacktestStatus.finished,
    queue_position=None,
    error=None,
    model="blesk-novol-8.keras",
    model_id="mdl_k2m9qx47f3",
    instruments=[_AAPL],
    representation=Representation.renko,
    setup=_SETUP,
    total_return=0.246,
    buy_and_hold_return=0.682,
    sharpe=0.79,
    max_drawdown=-0.197,
    win_rate=0.512,
    num_trades=1712,
    starred=True,
    public=False,
    created_at=datetime(2026, 10, 24, 9, 30, tzinfo=UTC),
)
_COUNTS = BacktestCounts(all=15, starred=5, failed=2, public=3)
_ORION_STATE = BacktestState(
    id="bt_7f3k2m9qx4",
    number=12,
    name="Silent Orion",
    status=BacktestStatus.finished,
    starred=True,
    public=False,
)

TOOLS = (
    ToolSpec(
        list_backtests,
        name="list_backtests",
        title="List backtests",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"query": "aapl", "sort": "sharpe"},
            result=BacktestList(
                backtests=[_ORION],
                total=1,
                page=1,
                pages=1,
                counts=_COUNTS,
                models=[ModelCount(name="blesk-novol-8.keras", framework="keras", count=9)],
            ),
        ),
    ),
    ToolSpec(
        rename_backtest,
        name="rename_backtest",
        title="Rename a backtest",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=True),
        example=Example(
            arguments={"backtest_id": "bt_7f3k2m9qx4", "name": "AAPL renko, long only"},
            result=_ORION_STATE.model_copy(update={"name": "AAPL renko, long only"}),
        ),
    ),
    ToolSpec(
        star_backtest,
        name="star_backtest",
        title="Star a backtest",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=True),
        example=Example(arguments={"backtest_id": "bt_7f3k2m9qx4"}, result=_ORION_STATE),
    ),
    ToolSpec(
        cancel_backtest,
        name="cancel_backtest",
        title="Cancel a queued backtest",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"backtest_id": "bt_t9n8v7d6a5"},
            result=CancelledBacktest(
                id="bt_t9n8v7d6a5",
                number=14,
                name="Quiet Vega",
                status=BacktestStatus.failed,
                error="You cancelled the backtest before it ran.",
            ),
        ),
    ),
    ToolSpec(
        list_experiments,
        name="list_experiments",
        title="List Experiments",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"symbol": "AAPL", "sort": "sharpe"},
            result=ExperimentList(
                experiments=[
                    ExperimentSummary(
                        id="bt_7f3k2m9qx4",
                        number=12,
                        name="Silent Orion",
                        author=Author(id="usr_7f3k2m9qx4", name="Tymur Tkach", mine=False),
                        model="blesk-novol-8.keras",
                        instruments=[_AAPL],
                        representation=Representation.renko,
                        setup=_SETUP,
                        total_return=0.246,
                        benchmark_return=0.682,
                        buy_and_hold_return=0.682,
                        sharpe=0.79,
                        max_drawdown=-0.197,
                        win_rate=0.512,
                        num_trades=1712,
                        published_at=datetime(2026, 10, 25, 18, 5, tzinfo=UTC),
                    )
                ],
                total=1,
                page=1,
                pages=1,
                counts=ExperimentCounts(all=25, mine=2, people=7),
                facets=ExperimentFacets.model_validate(
                    {
                        "markets": [{"value": "stock", "count": 1}],
                        "instruments": [{"market": "stock", "symbol": "AAPL", "count": 1}],
                        "representations": [{"value": "renko", "count": 1}],
                        "timeframes": [{"value": "1h", "count": 1}],
                    }
                ),
            ),
        ),
    ),
    ToolSpec(
        publish_backtest,
        name="publish_backtest",
        title="Publish a backtest",
        scopes=(Scope.publish,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"backtest_id": "bt_7f3k2m9qx4"},
            result=_ORION_STATE.model_copy(update={"public": True}),
        ),
    ),
    ToolSpec(
        unpublish_backtest,
        name="unpublish_backtest",
        title="Unpublish a backtest",
        scopes=(Scope.publish,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(arguments={"backtest_id": "bt_7f3k2m9qx4"}, result=_ORION_STATE),
    ),
    ToolSpec(
        get_dashboard,
        name="get_dashboard",
        title="Dashboard",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={},
            result=Dashboard(
                counts=_COUNTS,
                newest=[_ORION],
                best=BestBacktests(
                    sharpe=[_ORION], total_return=[_ORION], max_drawdown=[_ORION], win_rate=[_ORION]
                ),
                starred=[_ORION],
            ),
        ),
    ),
)
