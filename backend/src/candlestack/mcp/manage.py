"""The tools that find, organise and publish the user's backtests: the list of the Backtests
page, rename, star (one or many), cancel a queued one, Experiments, publish and unpublish (one
or many), and the Dashboard.

They call ``BacktestsService`` and ``BacktestRuns`` as the routes do, raise the routes' problems
(``update_problems``, ``backtest_problems``) and answer compact models: a backtest without its
equity series and the other fields only the web app's cards and charts use.
"""

import json
from datetime import UTC, date, datetime
from typing import Annotated

from mcp.server.mcpserver import Context
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

from candlestack.accounts import Scope
from candlestack.experiments import (
    BACKTEST_NAME_MAX,
    EXPERIMENTS_PAGE_SIZE,
    MODEL_NAME_MAX,
    PAGE_MAX,
    PAGE_SIZE,
    PER_PAGE_MAX,
    QUERY_MAX,
    UPDATE_MAX,
    Author,
    BacktestCounts,
    BacktestFilters,
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
    ExperimentsQuery,
    InstrumentMarket,
    ListQuery,
    ModelCount,
    Representation,
    SetupTimeframe,
    SortOrder,
    as_utc,
    backtest_problems,
    backtest_runs,
    get_backtests_service,
    instrument_filter,
    update_problems,
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

# --- Arguments ------------------------------------------------------------------------------

BacktestId = Annotated[
    str,
    Field(description="Public ID of the backtest, from list_backtests", examples=["bt_7f3k2m9qx4"]),
]
Page = Annotated[int, Field(ge=1, le=PAGE_MAX, description="The page, from 1")]
Order = Annotated[
    SortOrder, Field(description="Direction of the sort; the default sort is newest first anyway")
]
# The IDs a bulk change takes, as delete_backtests does.
BacktestIds = Annotated[
    list[Annotated[str, Field(min_length=1, max_length=32, examples=["bt_7f3k2m9qx4"])]],
    Field(
        min_length=1,
        max_length=UPDATE_MAX,
        description=f"1 to {UPDATE_MAX} IDs of the user's backtests, from list_backtests",
    ),
]
# Whether a list answers its counts and filter values, which cost queries of their own.
IncludeFacets = Annotated[
    bool | None,
    Field(
        description="Answer the counts and the values the filters offer: true on every page, "
        "false never; left out, only on page 1"
    ),
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
    sweep_id: str | None = Field(
        description="The sweep it was started in (get_sweep); null for one started alone"
    )
    model: str = Field(description="Name of the model it ran")
    model_id: str | None = Field(description="Public ID of the model; null once it was deleted")
    instruments: list[BacktestInstrument]
    representation: Representation = Field(description="What the model reads")
    setup: BacktestSetup
    total_return: float | None = Field(description="Return of the strategy")
    buy_and_hold_return: float | None = Field(description="Return of holding the instruments")
    sharpe: float | None = Field(description="Sharpe ratio, per year")
    max_drawdown: float | None = Field(
        description="Largest fall from a peak, 0 or below: the smaller the fall the better, so "
        "-0.05 is better than -0.20"
    )
    win_rate: float | None = Field(description="Share of the trades that made money")
    num_trades: int | None
    starred: bool
    public: bool = Field(description="Published in Experiments, visible to every beta account")
    created_at: datetime = Field(description="When it was started (UTC)")
    url: str = Field(description="The backtest's page in the app, to show the user")


class BacktestList(BaseModel):
    """A page of the user's backtests that match, and the filter choices."""

    backtests: list[BacktestSummary] = Field(description="At most `per_page` backtests")
    total: int = Field(description="Backtests that match the search and the filters")
    page: int
    pages: int = Field(description="Pages of the backtests that match, at least 1")
    counts: BacktestCounts | None = Field(
        description="All the user's backtests, whatever the filters; null unless "
        "`include_facets` asks (by default on page 1)"
    )
    models: list[ModelCount] | None = Field(
        description="Every model the backtests used, the most used first: the values of "
        "`model`; null like `counts`"
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
    url: str = Field(description="The backtest's page in the app, to show the user")


class ExperimentList(BaseModel):
    """A page of Experiments that match, the counts and what the filters offer."""

    experiments: list[ExperimentSummary] = Field(
        description=f"At most {EXPERIMENTS_PAGE_SIZE} published backtests"
    )
    total: int = Field(description="Published backtests that match")
    page: int
    pages: int = Field(description="Pages of those that match, at least 1")
    counts: ExperimentCounts | None = Field(
        description="Null unless `include_facets` asks (by default on page 1)"
    )
    facets: ExperimentFacets | None = Field(
        description="The values each filter offers, with counts, over the scope and the "
        "search; null like `counts`"
    )


class RankedBacktest(BaseModel):
    """A backtest in a ranking of the Dashboard and the value it is ranked by."""

    id: str = Field(description="Public ID of the backtest, a key of `backtests`")
    value: float = Field(description="Its result the ranking orders by")


class BestBacktests(BaseModel):
    """The 5 best finished backtests by each result, the best first (for `max_drawdown` the
    smallest fall, the value nearest 0)."""

    sharpe: list[RankedBacktest]
    total_return: list[RankedBacktest]
    max_drawdown: list[RankedBacktest]
    win_rate: list[RankedBacktest]


class Dashboard(BaseModel):
    """The Dashboard of the user's backtests: each backtest once in `backtests`, and the lists
    of the Dashboard as their IDs."""

    counts: BacktestCounts
    backtests: dict[str, BacktestSummary] = Field(
        description="Every backtest the lists below name, by its ID"
    )
    newest: list[str] = Field(
        description="IDs of the 6 newest backtests that ended, failed ones included, newest first"
    )
    best: BestBacktests
    starred: list[str] = Field(
        description="IDs of the 4 starred backtests with the best Sharpe ratio"
    )


def summary(call: ToolCall, backtest: BacktestOut) -> BacktestSummary:
    fields = backtest.model_dump(include=set(BacktestSummary.model_fields) - {"model", "url"})
    return BacktestSummary(
        **fields,
        model=backtest.model.name,
        model_id=backtest.model.id,
        url=backtest_url(call, backtest.id),
    )


def experiment_summary(call: ToolCall, experiment: ExperimentOut) -> ExperimentSummary:
    fields = experiment.model_dump(include=set(ExperimentSummary.model_fields) - {"model", "url"})
    return ExperimentSummary(
        **fields, model=experiment.model.name, url=backtest_url(call, experiment.id)
    )


def _state(backtest: BacktestOut) -> BacktestState:
    return BacktestState.model_validate(
        backtest.model_dump(include=set(BacktestState.model_fields))
    )


# --- The tools ------------------------------------------------------------------------------


def _facets(page: int, *, include: bool | None) -> bool:
    return page == 1 if include is None else include


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
    status: Annotated[
        list[BacktestStatus] | None,
        Field(description="Only backtests with one of these statuses"),
    ] = None,
    sweep_id: Annotated[
        str | None, Field(max_length=32, description="Only the backtests of this sweep")
    ] = None,
    model_id: Annotated[
        str | None, Field(max_length=32, description="Only the backtests of this model's ID")
    ] = None,
    instrument: Annotated[
        str | None,
        Field(max_length=64, description="Only this instrument's, <market>:<symbol>"),
        AfterValidator(instrument_filter),
    ] = None,
    timeframe: Annotated[SetupTimeframe | None, Field(description="Only this timeframe")] = None,
    representation: Annotated[
        Representation | None, Field(description="Only models that read this")
    ] = None,
    created_after: Annotated[
        datetime | None,
        Field(description="Only those started at or after this time (UTC)"),
        AfterValidator(as_utc),
    ] = None,
    created_before: Annotated[
        datetime | None,
        Field(description="Only those started before this time (UTC)"),
        AfterValidator(as_utc),
    ] = None,
    min_sharpe: Annotated[
        float | None, Field(allow_inf_nan=False, description="Sharpe ratio at least this")
    ] = None,
    min_total_return: Annotated[
        float | None, Field(allow_inf_nan=False, description="Return at least this, a fraction")
    ] = None,
    min_max_drawdown: Annotated[
        float | None,
        Field(
            le=0,
            allow_inf_nan=False,
            description="Largest fall no deeper than this, 0 or below: -0.2 keeps falls of up "
            "to 20 percent",
        ),
    ] = None,
    starred: Annotated[bool | None, Field(description="Only starred (true) or not")] = None,
    public: Annotated[bool | None, Field(description="Only published (true) or not")] = None,
    sort: Annotated[
        BacktestSort,
        Field(description="What to sort by; those without the value (failed ones) come last"),
    ] = BacktestSort.default,
    order: Order = SortOrder.desc,
    page: Page = 1,
    per_page: Annotated[
        int, Field(ge=1, le=PER_PAGE_MAX, description="Backtests per page")
    ] = PAGE_SIZE,
    include_facets: IncludeFacets = None,
) -> BacktestList:
    """Lists the user's backtests: search, filters (status, sweep, model, instrument,
    timeframe, representation, start time, least Sharpe ratio or return, deepest drawdown,
    starred, published; all must hold), sort by a result, 10 a page or up to 100. Use it to
    find a backtest's ID by its name, number, instrument or model, a sweep's backtests, or the
    best ones by a result. Answers the page and how many match, and on page 1 the counts of
    all, starred, failed and published backtests and the models used; each backtest has its
    `url`, the page to show the user. A page past the last one is empty."""
    call = tool_call(ctx)
    more = BacktestFilters(
        statuses=tuple(status or ()),
        sweep_id=sweep_id,
        model_id=model_id,
        instrument=instrument,
        timeframe=timeframe,
        representation=representation,
        created_after=created_after,
        created_before=created_before,
        min_sharpe=min_sharpe,
        min_total_return=min_total_return,
        min_max_drawdown=min_max_drawdown,
        starred=starred,
        public=public,
    )
    list_query = ListQuery(
        query=query,
        show=show,
        model=model,
        sort=sort,
        order=order,
        page=page,
        more=more,
        per_page=per_page,
    )
    async with call.db() as db:
        service = get_backtests_service(db)
        found = await service.find(call.user.id, list_query)
        facets = (
            await service.facets(call.user.id) if _facets(page, include=include_facets) else None
        )
    return BacktestList(
        backtests=[summary(call, backtest) for backtest in found.backtests],
        total=found.total,
        page=found.page,
        pages=found.pages,
        counts=None if facets is None else facets.counts,
        models=None if facets is None else facets.models,
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


async def star_backtests(
    ids: BacktestIds,
    ctx: Context,
    *,
    starred: Annotated[bool, Field(description="true stars them, false unstars them")] = True,
) -> BacktestsUpdated:
    """Stars up to 100 of the user's backtests at once, or unstars them with starred false, as
    star_backtest does one. Answers the IDs now as asked in `updated` (those that already were
    included, so calling it again changes nothing) and the IDs the user has no backtest with in
    `not_found`, which change nothing."""
    call = tool_call(ctx)
    async with call.db() as db:
        return await get_backtests_service(db).update_many(call.user.id, ids, starred=starred)


async def cancel_backtest(backtest_id: BacktestId, ctx: Context) -> CancelledBacktest:
    """Cancels one of the user's queued backtests, so it never runs: it becomes failed, with an
    error that says it was cancelled, and stays in the list. Only a queued backtest can be
    cancelled; a running or ended one answers the problem backtest-not-queued."""
    call = tool_call(ctx)
    async with call.db() as db:
        runs = backtest_runs(call.state, db)
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
    include_facets: IncludeFacets = None,
) -> ExperimentList:
    """Lists Experiments: the backtests that accounts of the beta published, which every
    signed-in account can see, with search, filters (author, market, symbol, representation,
    timeframe), sort and 12 a page. Use it to find what others tried and how it did, then
    read one with its ID. Answers the page, how many match, and on page 1 the counts and the
    values each filter offers; each backtest has its `url`, the page to show the user. A page
    past the last one is empty."""
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
        service = get_backtests_service(db)
        found = await service.find_experiments(call.user.id, experiments_query)
        facets = (
            await service.experiment_facets(call.user.id, experiments_query)
            if _facets(page, include=include_facets)
            else None
        )
    return ExperimentList(
        experiments=[experiment_summary(call, experiment) for experiment in found.experiments],
        total=found.total,
        page=found.page,
        pages=found.pages,
        counts=None if facets is None else facets.counts,
        facets=None if facets is None else facets.facets,
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


async def publish_backtests(
    ids: BacktestIds,
    ctx: Context,
    *,
    published: Annotated[
        bool, Field(description="true publishes them, false takes them out of Experiments")
    ] = True,
) -> BacktestsUpdated:
    """Publishes up to 100 of the user's finished backtests in Experiments at once, or with
    published false takes them out, as publish_backtest and unpublish_backtest do one: a
    published backtest is visible to every account of the beta with its name, the user's
    display name, setup, results, charts, trades and its model's name and input (never the
    model file). Ask the user before publishing or unpublishing. Answers the IDs now as asked
    in `updated` (those that already were included, so calling it again changes nothing), the
    IDs the user has no backtest with in `not_found`, and when publishing those that cannot be
    published (queued, running or failed) in `cannot_publish`; nothing changes for those two."""
    call = tool_call(ctx)
    async with call.db() as db:
        return await get_backtests_service(db).update_many(call.user.id, ids, public=published)


def _ranked(backtests: list[BacktestOut], result: str) -> list[RankedBacktest]:
    return [
        RankedBacktest(id=backtest.id, value=getattr(backtest, result)) for backtest in backtests
    ]


async def get_dashboard(ctx: Context) -> Dashboard:
    """The user's Dashboard in one call: the counts of all, starred, failed and published
    backtests, the 6 newest that ended, the 5 best finished ones by Sharpe ratio, return,
    drawdown (the smallest fall) and win rate with the value, and the 4 best starred ones.
    Each list gives IDs; `backtests` has each backtest once, by ID, with its `url`, the page to
    show the user. Call it for an overview before digging in with list_backtests."""
    call = tool_call(ctx)
    async with call.db() as db:
        found = await get_backtests_service(db).own_dashboard(call.user.id)
    best = found.best
    listed = [
        *found.last,
        *best.sharpe,
        *best.total_return,
        *best.max_drawdown,
        *best.win_rate,
        *found.starred,
    ]
    return Dashboard(
        counts=found.counts,
        backtests={backtest.id: summary(call, backtest) for backtest in listed},
        newest=[backtest.id for backtest in found.last],
        best=BestBacktests(
            sharpe=_ranked(best.sharpe, "sharpe"),
            total_return=_ranked(best.total_return, "total_return"),
            max_drawdown=_ranked(best.max_drawdown, "max_drawdown"),
            win_rate=_ranked(best.win_rate, "win_rate"),
        ),
        starred=[backtest.id for backtest in found.starred],
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
    sweep_id=None,
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
    url="https://app.candlestack.tech/backtests/bt_7f3k2m9qx4",
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
        star_backtests,
        name="star_backtests",
        title="Star backtests",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=True),
        example=Example(
            arguments={"ids": ["bt_7f3k2m9qx4", "bt_t9n8v7d6a5", "bt_nonexistent"]},
            result=BacktestsUpdated(
                updated=["bt_7f3k2m9qx4", "bt_t9n8v7d6a5"], count=2, not_found=["bt_nonexistent"]
            ),
        ),
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
                        author=Author(id="usr_7f3k2m9qx4", name="Grace Hopper", mine=False),
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
                        url="https://app.candlestack.tech/backtests/bt_7f3k2m9qx4",
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
        publish_backtests,
        name="publish_backtests",
        title="Publish backtests",
        scopes=(Scope.publish,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"ids": ["bt_7f3k2m9qx4", "bt_t9n8v7d6a5"]},
            result=BacktestsUpdated(
                updated=["bt_7f3k2m9qx4"],
                count=1,
                not_found=[],
                cannot_publish=["bt_t9n8v7d6a5"],
            ),
        ),
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
                backtests={_ORION.id: _ORION},
                newest=[_ORION.id],
                best=BestBacktests(
                    sharpe=[RankedBacktest(id=_ORION.id, value=0.79)],
                    total_return=[RankedBacktest(id=_ORION.id, value=0.246)],
                    max_drawdown=[RankedBacktest(id=_ORION.id, value=-0.197)],
                    win_rate=[RankedBacktest(id=_ORION.id, value=0.512)],
                ),
                starred=[_ORION.id],
            ),
        ),
    ),
)
