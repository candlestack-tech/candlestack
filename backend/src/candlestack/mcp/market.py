"""The tools about market data and what a backtest is made of: finding instruments, their
candles, the blocks a model's manifest names (the preprocessing catalog), and the user's
backtest defaults and preferences.

The market data tools call the ``DataService`` of the data routes and answer the data module's
problems as they do (``data_problems``); an instrument and its candles count against the same
per-minute limit of requests per client address (``client_rate_limit``). Candles come in pages
of at most ``CANDLES_MAX`` rows, so one call never floods the model's context. Saving the
backtest defaults needs the scope ``backtests``, as its route does.
"""

import time
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from candlestack.accounts import (
    AccountsService,
    BacktestDefaults,
    BacktestDefaultsIn,
    DateFormat,
    Scope,
    TimeFormat,
    TimeZone,
    get_accounts_service,
)
from candlestack.data import (
    CandleSet,
    DataService,
    Exchange,
    Instrument,
    InstrumentId,
    InstrumentSort,
    InstrumentStats,
    Market,
    SearchResult,
    Timeframe,
    TimeParam,
    client_rate_limit,
    data_problems,
)
from candlestack.experiments import Decision, Execution, Period
from candlestack.mcp.convention import Example, ToolCall, ToolSpec, changes, reads, tool_call
from candlestack.preprocessing import BlockOut, Step, describe_catalog

# Rows of one get_candles call. Clients show a result a second time as indented text, so a full
# page has to stay well under the 25k tokens of tool output that clients such as Claude Code
# accept by default.
CANDLES_DEFAULT, CANDLES_MAX = 200, 500
# Patched by tests to move the clock.
_time = time.time
# The catalog is code: it changes only with a release.
_CATALOG = describe_catalog()


# --- Instruments -------------------------------------------------------------------------------

InstrumentIdArg = Annotated[
    str,
    Field(
        min_length=3,
        max_length=40,
        description="<market>:<symbol>, such as crypto:BTCUSDT or stock:AAPL",
    ),
]


def _moment(epoch: int | None) -> datetime | None:
    return None if epoch is None else datetime.fromtimestamp(epoch, UTC)


class InstrumentItem(BaseModel):
    """An instrument with its last price and 24 h stats, refreshed every few minutes."""

    id: str = Field(description="Instrument ID for the other tools, <market>:<symbol>")
    name: str = Field(description="Crypto: <base>/<quote>. Stocks: the company or fund name")
    exchange: str | None = Field(description="NASDAQ, NYSE, ARCA, ... for a stock, BINANCE")
    price: float | None = Field(
        description="Last price: in the quote asset for a crypto pair, in USD for a stock "
        "(delayed by 15 minutes); null while not known"
    )
    change_24h: float | None = Field(
        description="Change in percent: crypto against 24 hours ago, a stock against the "
        "previous session's close"
    )
    volume_24h: float | None = Field(description="Volume of the last 24 hours (session) in USD")
    available_from: datetime | None = Field(
        description="Open time of the first candle (UTC), where backtests can start; null "
        "while not known yet (get_instrument says it)"
    )

    @classmethod
    def of(
        cls, instrument: Instrument, stats: InstrumentStats | None, first: int | None
    ) -> "InstrumentItem":
        return cls(
            id=str(instrument.id),
            name=instrument.name,
            exchange=instrument.exchange,
            price=stats.price if stats else None,
            change_24h=stats.change_24h if stats else None,
            volume_24h=stats.volume_24h if stats else None,
            available_from=_moment(first),
        )


def _items(found: SearchResult) -> list[InstrumentItem]:
    return [
        InstrumentItem.of(item, stats, first)
        for item, stats, first in zip(found.items, found.stats, found.firsts, strict=True)
    ]


_UNAVAILABLE = (
    "Markets left out because their source is down right now; ask again later. Empty normally"
)


class InstrumentsFound(BaseModel):
    """The instruments that match, best first."""

    items: list[InstrumentItem]
    total: int = Field(description="Number of all matches")
    next_offset: int | None = Field(
        description="The offset of the next page when there are more matches, else null"
    )
    unavailable: list[Market] = Field(description=_UNAVAILABLE)


def _data(call: ToolCall) -> DataService:
    return call.state.data_service


async def search_instruments(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=50,
            description="Symbol or name, any case; separators are ignored (btc/usdt finds BTCUSDT)",
        ),
    ],
    ctx: Context,
    *,
    market: Annotated[Market | None, Field(description="Only this market")] = None,
    exchange: Annotated[
        Exchange | None,
        Field(description="Only instruments that trade there: BINANCE for crypto pairs"),
    ] = None,
    limit: Annotated[int, Field(ge=1, le=50, description="Most items to answer")] = 10,
    offset: Annotated[int, Field(ge=0, description="Matches to skip")] = 0,
) -> InstrumentsFound:
    """Finds instruments by symbol or name and gives the instrument ID the other tools take
    (btc finds crypto:BTCUSDT, apple stock:AAPL): Binance spot pairs (market crypto), US stocks
    and ETFs at Alpaca (market stock). Best match first; page with offset while next_offset is
    not null. To browse without a query (most traded, top gainers): top_instruments."""
    call = tool_call(ctx)
    with data_problems():
        found = await _data(call).search(query, market, limit, offset, exchange=exchange)
    end = offset + len(found.items)
    return InstrumentsFound(
        items=_items(found),
        total=found.total,
        next_offset=end if end < found.total else None,
        unavailable=found.unavailable,
    )


class TopInstruments(BaseModel):
    """The instruments in the order asked for."""

    items: list[InstrumentItem]
    unavailable: list[Market] = Field(description=_UNAVAILABLE)


_TOP_SORTS = {
    "volume": InstrumentSort.VOLUME,
    "change": InstrumentSort.CHANGE,
    "-change": InstrumentSort.LOSERS,
}


async def top_instruments(
    ctx: Context,
    market: Annotated[Market | None, Field(description="Both when left out")] = None,
    sort: Annotated[
        Literal["volume", "change", "-change"],
        Field(
            description="volume: the most traded (24 h, in USD); change: the top gainers; "
            "-change: the top losers"
        ),
    ] = "volume",
    limit: Annotated[int, Field(ge=1, le=25, description="How many")] = 10,
) -> TopInstruments:
    """The most traded instruments, or the top gainers or losers of the last 24 hours, for
    ideas when the user names none; to find a named one: search_instruments."""
    call = tool_call(ctx)
    with data_problems():
        found = await _data(call).search(None, market, limit, sort=_TOP_SORTS[sort])
    return TopInstruments(items=_items(found), unavailable=found.unavailable)


async def _limit_source_requests(ctx: Context, call: ToolCall) -> None:
    """Counts the call against the per-minute limit of instrument and candle requests of the
    client address, as the routes do: 429 ``rate-limited`` beyond it."""
    request = ctx.request_context.request
    if not isinstance(request, Request):
        raise RuntimeError("A tool ran outside a request the MCP endpoint signed in")
    await client_rate_limit(request, call.settings, call.state.rate_limiter)


class InstrumentDetail(BaseModel):
    """An instrument and the period its candles can be asked for."""

    id: str = Field(description="Instrument ID, <market>:<symbol>")
    name: str = Field(description="Crypto: <base>/<quote>. Stocks: the company or fund name")
    exchange: str | None = Field(description="NASDAQ, NYSE, ARCA, ... for a stock, BINANCE")
    timeframes: list[Timeframe] = Field(description="The timeframes of its candles")
    available_from: datetime | None = Field(
        description="Open time of the first candle (UTC); null when the source cannot say now"
    )
    available_to: datetime = Field(
        description="Open time of the newest closed 1m candle (UTC): for a stock the last "
        "minute of the latest session, at least 15 minutes ago"
    )


async def get_instrument(instrument_id: InstrumentIdArg, ctx: Context) -> InstrumentDetail:
    """One instrument, the timeframes of its candles and the period they cover. Check it
    before get_candles or a backtest: a period outside it fails with period-out-of-range."""
    call = tool_call(ctx)
    await _limit_source_requests(ctx, call)
    with data_problems():
        info = await _data(call).instrument(InstrumentId.parse(instrument_id))
    instrument = info.instrument
    return InstrumentDetail(
        id=str(instrument.id),
        name=instrument.name,
        exchange=instrument.exchange,
        timeframes=list(info.timeframes),
        available_from=_moment(info.available_from),
        available_to=datetime.fromtimestamp(info.available_to, UTC),
    )


# --- Candles -----------------------------------------------------------------------------------

Candle = tuple[int, float, float, float, float, float]


class CandlesPage(BaseModel):
    """A page of closed candles, oldest first, and where the next page starts."""

    instrument: str = Field(description="Instrument ID, <market>:<symbol>")
    timeframe: Timeframe
    start: datetime = Field(description="Start of the page's period (UTC), inclusive")
    end: datetime = Field(description="End of the page's period (UTC), exclusive")
    candles: list[Candle] = Field(
        description="[open time (UTC epoch seconds), open, high, low, close, volume] each, "
        "oldest first; volume in the base asset for crypto, in shares for stocks"
    )
    gaps: list[tuple[int, int]] = Field(
        description="Missing candles in the page as [start, end) in UTC epoch seconds: no "
        "trade then, never filled in"
    )
    next_start: datetime | None = Field(
        description="With start: call again with start set to this (same end) for the next "
        "page; null when the period is done"
    )
    next_end: datetime | None = Field(
        description="Without start: call again with end set to this for the earlier candles; "
        "null when the data begins in this page (when it begins exactly at next_end, that call "
        "answers period-out-of-range: there is nothing earlier)"
    )


def _page(found: CandleSet, *, next_start: int | None, next_end: int | None) -> CandlesPage:
    """The candles of `found` as a page, with their gaps."""
    frame = found.frame
    rows = zip(
        frame["ts"].to_list(),
        frame["open"].to_list(),
        frame["high"].to_list(),
        frame["low"].to_list(),
        frame["close"].to_list(),
        frame["volume"].to_list(),
        strict=True,
    )
    return CandlesPage(
        instrument=str(found.instrument),
        timeframe=found.timeframe,
        start=datetime.fromtimestamp(found.start, UTC),
        end=datetime.fromtimestamp(found.end, UTC),
        candles=list(rows),
        gaps=found.gaps,
        next_start=_moment(next_start),
        next_end=_moment(next_end),
    )


async def _newest(
    service: DataService, instrument: InstrumentId, timeframe: Timeframe, end: int, limit: int
) -> CandlesPage:
    """The last `limit` candles before `end`, and where the earlier ones end: where this page
    starts, unless its candles and gaps are fewer than `limit`, which means the data begins in
    it."""
    found = await service.last_candles(instrument, timeframe, end, limit)
    full = found.frame.height + found.gaps_total >= limit
    return _page(found, next_start=None, next_end=found.start if full else None)


async def _from_start(
    service: DataService,
    instrument: InstrumentId,
    timeframe: Timeframe,
    *,
    start: int,
    end: int,
    limit: int,
) -> CandlesPage:
    """The first `limit` candles from `start` on, and where the next ones start."""
    found = await service.first_candles(instrument, timeframe, start, end, limit)
    return _page(found, next_start=found.end if found.end < end else None, next_end=None)


async def get_candles(
    instrument_id: InstrumentIdArg,
    timeframe: Timeframe,
    ctx: Context,
    *,
    start: Annotated[
        TimeParam | None,
        Field(
            description="Inclusive: an ISO 8601 date or date-time (UTC unless it gives an "
            "offset) or epoch seconds. Without it, the newest candles before end"
        ),
    ] = None,
    end: Annotated[
        TimeParam | None,
        Field(description="Exclusive, in the same forms; default now"),
    ] = None,
    limit: Annotated[
        int, Field(ge=1, le=CANDLES_MAX, description="Most candles in this page")
    ] = CANDLES_DEFAULT,
) -> CandlesPage:
    """Closed OHLCV candles of one instrument and timeframe, up to 500 a page. A backtest
    loads its own candles: do not fetch them for it.

    With start: from start on, oldest first; the next page with start=next_start. Without
    start: the newest before end; earlier ones with end=next_end. Stocks have the regular
    session only (09:30-16:00 New York); missing candles stay gaps. A per-minute limit applies:
    ask for a longer timeframe rather than many pages.

    Data terms: stocks come from Alpaca (15 minutes delayed) for the user's personal,
    noncommercial use only: never publish or pass on the stock candles."""
    call = tool_call(ctx)
    await _limit_source_requests(ctx, call)
    until = int(_time()) if end is None else end
    with data_problems():
        instrument = InstrumentId.parse(instrument_id)
        if start is None:
            return await _newest(_data(call), instrument, timeframe, until, limit)
        return await _from_start(
            _data(call), instrument, timeframe, start=start, end=until, limit=limit
        )


# --- Preprocessing catalog ---------------------------------------------------------------------


def _left_out(value: object) -> bool:
    return value is None


class CatalogParam(BaseModel):
    """A parameter of a block and the values it takes; what it does not have is left out."""

    name: str
    type: Literal["integer", "number", "string", "boolean"] = Field(description="Its JSON type")
    description: str | None = Field(default=None, exclude_if=_left_out)
    required: bool = Field(description="The manifest must give it; else default applies")
    default: int | float | str | bool | None = Field(
        default=None, exclude_if=_left_out, description="Left out when required"
    )
    minimum: int | float | None = Field(default=None, exclude_if=_left_out)
    maximum: int | float | None = Field(default=None, exclude_if=_left_out)
    exclusive_minimum: int | float | None = Field(
        default=None, exclude_if=_left_out, description="Values must be above it"
    )
    exclusive_maximum: int | float | None = Field(
        default=None, exclude_if=_left_out, description="Values must be below it"
    )
    options: list[str] | None = Field(
        default=None, exclude_if=_left_out, description="The values allowed, for a choice"
    )


class CatalogBlock(BaseModel):
    """A block a manifest can name for one step of the model's input."""

    id: str = Field(description="The versioned name the manifest uses, such as rsi@2")
    step: Step
    description: str
    warm_up: str = Field(
        description="Candles the block needs before its first complete value (rows, for the "
        "window), as a formula of its parameters"
    )
    params: list[CatalogParam]

    @classmethod
    def of(cls, block: BlockOut) -> "CatalogBlock":
        return cls(
            id=block.id,
            step=block.step,
            description=block.description,
            warm_up=block.warm_up,
            params=[
                CatalogParam.model_validate(param.model_dump(exclude={"tunable"}))
                for param in block.params
            ],
        )


class Catalog(BaseModel):
    """The blocks, by step in the order they apply."""

    blocks: list[CatalogBlock]


async def preprocessing_catalog(
    ctx: Context,
    step: Annotated[
        Step | None,
        Field(description="Only the blocks of this step"),
    ] = None,
) -> Catalog:
    """Every block a manifest can name, by step (representation, feature, normalization,
    window), with its versioned ID, warm-up and parameters. Use it to write or check a
    manifest. The manifest format: get_docs page manifest."""
    tool_call(ctx)
    return Catalog(
        blocks=[CatalogBlock.of(block) for block in _CATALOG.blocks if step in (None, block.step)]
    )


# --- The user's defaults and preferences -------------------------------------------------------


def _accounts(call: ToolCall, db: AsyncSession) -> AccountsService:
    """The accounts service of the routes, on the call's Postgres session."""
    state = call.state
    return get_accounts_service(
        call.settings, state.redis, db, state.http, state.account_deletion_hooks
    )


class BacktestDefaultsOut(BacktestDefaults):
    """The backtest defaults twice: as Settings shows them, in percent (`stop_loss_pct`,
    `fees_pct`, `slippage_pct`) and days, and in the units start_backtest takes (`period`,
    `decision`, `execution`, `benchmark_instrument`)."""

    period: Period = Field(
        description="`period_start` to `period_end` as start_backtest's `data.period`: UTC epoch "
        "seconds from the first day's midnight to the midnight after the last day (excluded), "
        "or to now while that day has not ended, as the app's builder sends it"
    )
    decision: Decision = Field(
        description="`threshold` and `direction` as start_backtest's `decision`: the threshold "
        "rule with `limit` the threshold, `long_only` for the direction long"
    )
    execution: Execution = Field(
        description="`stop_loss_pct`, `fees_pct` and `slippage_pct` as start_backtest's "
        "`execution`: `stop_loss` a fraction (7 % is 0.07), `fee_bps` and `slippage_bps` in basis "
        "points (0.1 % is 10)"
    )
    benchmark_instrument: str | None = Field(
        description="`benchmark` as the `instrument` of start_backtest's `benchmarks` item "
        "(`kind` buy_and_hold): null for the backtest's own instrument, else stock:SPY, "
        "stock:QQQ or stock:DIA, for a stock backtest only",
        examples=[None],
    )


def _midnight(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _bps(pct: float) -> float:
    """Percent as basis points, without the float's tail (0.07 % is 7, not 7.000000000000001)."""
    return round(pct * 100, 4)


def _period(defaults: BacktestDefaults) -> Period:
    """The defaults' days as epoch seconds, ending now at the latest as the builder's period
    does: there are no candles after, and a backtest refuses an end in the future. A period
    that starts in the future keeps its end, so it stays a period (the backtest refuses it)."""
    start = _midnight(defaults.period_start)
    end = _midnight(defaults.period_end + timedelta(days=1))
    now = int(_time())
    return Period(start=start, end=min(end, now) if now > start else end)


def defaults_out(defaults: BacktestDefaults) -> BacktestDefaultsOut:
    """The defaults with the same settings in start_backtest's units, as the app's builder
    starts a backtest from them (the next candle's open, the whole capital, no drawdown stop)."""
    return BacktestDefaultsOut(
        **defaults.model_dump(),
        period=_period(defaults),
        decision=Decision.model_validate(
            {
                "rule": {"name": "threshold", "limit": defaults.threshold},
                "long_only": defaults.direction == "long",
            }
        ),
        execution=Execution(
            stop_loss=defaults.stop_loss_pct / 100,
            fee_bps=_bps(defaults.fees_pct),
            slippage_bps=_bps(defaults.slippage_pct),
        ),
        benchmark_instrument=None
        if defaults.benchmark == "bh"
        else f"stock:{defaults.benchmark.value}",
    )


async def get_backtest_defaults(ctx: Context) -> BacktestDefaultsOut:
    """The user's defaults for new backtests (Settings, Backtest defaults): use them for every
    setting of a backtest the user does not name.

    They come twice: as Settings shows them, `stop_loss_pct`, `fees_pct` and `slippage_pct` in
    percent, the period in days; and in start_backtest's units, to pass through unchanged:
    `period` (as data.period), `decision`, `execution` (stop_loss a fraction, fee_bps and
    slippage_bps in basis points) and `benchmark_instrument` (null: the backtest's own). The
    timeframe is not an argument of start_backtest: a backtest runs at its model's."""
    call = tool_call(ctx)
    async with call.db() as db:
        preferences = await _accounts(call, db).preferences(call.user)
    return defaults_out(preferences.backtest_defaults)


_DERIVED = "Ignored"


class BacktestDefaultsArg(BacktestDefaultsIn):
    # The backtest defaults to save, in Settings' units. The settings in start_backtest's units
    # that get_backtest_defaults adds are taken and ignored, so its answer can come back with
    # one setting changed. No docstring: the argument's description says it to a model.

    period: Any = Field(default=None, exclude=True, description=_DERIVED)
    decision: Any = Field(default=None, exclude=True, description=_DERIVED)
    execution: Any = Field(default=None, exclude=True, description=_DERIVED)
    benchmark_instrument: Any = Field(default=None, exclude=True, description=_DERIVED)


async def update_backtest_defaults(
    defaults: Annotated[
        BacktestDefaultsArg,
        Field(
            description="Every setting in Settings' units (percent, days); "
            "get_backtest_defaults' answer can be sent back as it is"
        ),
    ],
    ctx: Context,
) -> BacktestDefaults:
    """Saves the user's defaults for new backtests, all of them, replacing the saved ones.
    Only when the user asks to change their defaults (for one backtest, pass the settings to
    start_backtest): read get_backtest_defaults, change only what the user named."""
    call = tool_call(ctx)
    async with call.db() as db:
        return await _accounts(call, db).save_backtest_defaults(
            call.user, BacktestDefaultsIn.model_validate(defaults.model_dump())
        )


class Preferences(BaseModel):
    """How the user reads dates and times in the app."""

    time_zone: TimeZone = Field(
        description="ny: exchange time (New York); local: the time zone of the user's browser, "
        "which you cannot know: ask the user, or write UTC and say so"
    )
    date_format: DateFormat = Field(description="dmy 12 Oct 2024, iso 2024-10-12, dot 12.10.2024")
    time_format: TimeFormat = Field(description="24: 16:05, 12: 4:05 PM")


async def get_preferences(ctx: Context) -> Preferences:
    """How the user reads dates and times in the app (time zone, date format, 12 or 24
    hour): write them that way in your answers."""
    call = tool_call(ctx)
    async with call.db() as db:
        preferences = await _accounts(call, db).preferences(call.user)
    appearance = preferences.appearance
    return Preferences(
        time_zone=appearance.time_zone,
        date_format=appearance.date_format,
        time_format=appearance.time_format,
    )


# --- The tools ---------------------------------------------------------------------------------

_BTC = InstrumentItem(
    id="crypto:BTCUSDT",
    name="BTC/USDT",
    exchange="BINANCE",
    price=83249.35,
    change_24h=1.51,
    volume_24h=1258811866.0,
    available_from=datetime(2017, 8, 17, 4, tzinfo=UTC),
)
_ETH = InstrumentItem(
    id="crypto:ETHUSDT",
    name="ETH/USDT",
    exchange="BINANCE",
    price=3121.4,
    change_24h=-0.82,
    volume_24h=702114530.0,
    available_from=datetime(2017, 8, 17, 4, tzinfo=UTC),
)
_DAY = 1717372800  # 2024-06-03T00:00:00Z

TOOLS = (
    ToolSpec(
        search_instruments,
        name="search_instruments",
        title="Search instruments",
        details="""
            The order: the most traded pair of a crypto asset equal to the query, then the
            exact symbol, symbol prefixes, words of the name; separators are ignored (brkb finds
            BRK.B). Crypto is every Binance spot pair that trades, stocks every tradable US
            stock and ETF at Alpaca. Each item has its last price, 24 h change and volume, and
            since when it has candles.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"query": "btc", "limit": 1},
            result=InstrumentsFound(items=[_BTC], total=41, next_offset=1, unavailable=[]),
        ),
    ),
    ToolSpec(
        top_instruments,
        name="top_instruments",
        title="Top instruments",
        details="""
            Use it when the user asks what moves or what is liquid enough to test, or wants
            ideas for instruments without naming one. Of one market, or both without `market`;
            each with its last price, 24 h change and volume.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"market": "crypto", "limit": 2},
            result=TopInstruments(items=[_BTC, _ETH], unavailable=[]),
        ),
    ),
    ToolSpec(
        get_instrument,
        name="get_instrument",
        title="Instrument",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"instrument_id": "stock:AAPL"},
            result=InstrumentDetail(
                id="stock:AAPL",
                name="Apple Inc. Common Stock",
                exchange="NASDAQ",
                timeframes=list(Timeframe),
                available_from=datetime(2016, 1, 4, 14, 30, tzinfo=UTC),
                available_to=datetime(2026, 11, 6, 20, 59, tzinfo=UTC),
            ),
        ),
    ),
    ToolSpec(
        get_candles,
        name="get_candles",
        title="Candles",
        details="""
            Use it to look at the price around a trade, check a period before a backtest or
            compute something the backtest result does not say.

            `start` and `end` take an ISO 8601 date or date-time, such as 2024-06-03 or
            2024-06-03T13:30:00Z, or epoch seconds. While `next_start` is not null, call again
            with start set to it for the next page. Every call counts against a per-minute
            limit of candle requests.

            Data terms: crypto comes from Binance spot, stocks from Alpaca (SIP, every US
            exchange, 15 minutes delayed). Alpaca allows its data for the user's personal,
            noncommercial use only: use it for the user's analysis here, never publish or pass
            on the stock candles.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={
                "instrument_id": "crypto:BTCUSDT",
                "timeframe": "1h",
                "start": "2024-06-03",
                "limit": 3,
            },
            result=CandlesPage(
                instrument="crypto:BTCUSDT",
                timeframe=Timeframe.H1,
                start=datetime(2024, 6, 3, tzinfo=UTC),
                end=datetime(2024, 6, 3, 3, tzinfo=UTC),
                candles=[
                    (_DAY, 67491.0, 67700.0, 67420.2, 67612.4, 512.31),
                    (_DAY + 3600, 67612.5, 67650.0, 67510.0, 67540.3, 398.07),
                    (_DAY + 7200, 67540.3, 67640.0, 67488.8, 67601.9, 421.55),
                ],
                gaps=[],
                next_start=datetime(2024, 6, 3, 3, tzinfo=UTC),
                next_end=None,
            ),
        ),
    ),
    ToolSpec(
        preprocessing_catalog,
        name="preprocessing_catalog",
        title="Preprocessing catalog",
        details="""
            The steps: the representation (which rows the model sees: candles, Renko bricks, a
            Kagi line), the features (its columns, in order), their normalization and the
            window (rows to samples). Each block has its versioned ID, what it does, its warm-up
            and its parameters with types, ranges and defaults. Use it also to explain what a
            model's input is made of.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"step": "window"},
            result=Catalog(
                blocks=[
                    CatalogBlock.of(block) for block in _CATALOG.blocks if block.id == "window@1"
                ]
            ),
        ),
    ),
    ToolSpec(
        get_backtest_defaults,
        name="get_backtest_defaults",
        title="Backtest defaults",
        details="""
            The settings: timeframe, period, direction (long only or both), the signal
            threshold, stop loss, fees, slippage and the benchmark, as saved in Settings,
            Backtest defaults, or the app's defaults. With them a backtest runs as the app
            would start it. `period` is in UTC epoch seconds; `benchmark_instrument` is the
            instrument of the buy and hold benchmark.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={}, result=defaults_out(BacktestDefaults())),
    ),
    ToolSpec(
        update_backtest_defaults,
        name="update_backtest_defaults",
        title="Save backtest defaults",
        details="""
            It saves as Save defaults in Settings, Backtest defaults does: every setting as a
            whole (timeframe, period_start, period_end, direction, threshold, stop_loss_pct,
            fees_pct, slippage_pct and benchmark). The settings in start_backtest's units that
            get_backtest_defaults adds (`period`, `decision`, `execution`,
            `benchmark_instrument`) are taken and ignored. Answers the saved defaults.""",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"defaults": BacktestDefaults(stop_loss_pct=5).model_dump(mode="json")},
            result=BacktestDefaults(stop_loss_pct=5),
        ),
    ),
    ToolSpec(
        get_preferences,
        name="get_preferences",
        title="Date and time preferences",
        details="""
            The time zone is exchange time or the user's own. The backtest defaults are
            get_backtest_defaults.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={},
            result=Preferences(
                time_zone=TimeZone.ny, date_format=DateFormat.dmy, time_format=TimeFormat.h24
            ),
        ),
    ),
)
