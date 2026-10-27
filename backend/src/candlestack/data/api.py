"""HTTP API of the data module: ``/api/v1/data/...`` and ``/api/health/sources``.

The routes validate parameters, ask the ``DataService`` and turn its errors into problem
details (docs/data.md, "Errors"). The service lives on ``app.state.data_service``. The market
data itself (``market``: the list, an instrument, its candles) is for signed-in accounts only:
``candlestack.main`` adds that check, as data does not know accounts. The home page's few top
instruments and the icons are public.
"""

import logging
import math
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import AfterValidator, BeforeValidator, WithJsonSchema
from pydantic_core import PydanticCustomError

from candlestack.core import Problem, ProblemError, RateLimiterDep, SettingsDep, client_address
from candlestack.data.errors import (
    DataError,
    DataIntegrityError,
    InstrumentNotFound,
    InvalidRequest,
    PeriodOutOfRange,
    SourceUnavailable,
    TooManyCandles,
    WarmUpOutOfRange,
)
from candlestack.data.models import Exchange, InstrumentId, InstrumentSort, Market, Timeframe
from candlestack.data.schemas import (
    CandlesOut,
    InstrumentDetailOut,
    InstrumentItemOut,
    InstrumentSearchOut,
    SourcesHealthOut,
    TopInstrumentsOut,
    candles_json,
)
from candlestack.data.service import TOP_SECONDS, DataService

logger = logging.getLogger(__name__)

router = APIRouter()
# Alpaca's terms do not let us show its data to anyone on the internet: candlestack.main lets
# only signed-in accounts call these. The home page's few top instruments and the icons (logos,
# not market data) stay public.
market = APIRouter(prefix="/api/v1/data", tags=["data"])
data = APIRouter(prefix="/api/v1/data", tags=["data"])
health = APIRouter(tags=["health"])

_SOURCE_TITLES = {"binance": "Binance", "alpaca": "Alpaca"}
# Patched by tests to move the clock.
_time = time.time
# 9999-12-31T23:59:59Z: a larger number is not epoch seconds (most likely milliseconds).
_MAX_EPOCH = 253402300799
DEFAULT_LIMIT, MAX_LIMIT = 500, 5000
_DIGITS = re.compile(r"[0-9]+")


def get_data_service(request: Request) -> DataService:
    return request.app.state.data_service


DataServiceDep = Annotated[DataService, Depends(get_data_service)]


def _instrument_id(value: str) -> str:
    try:
        return str(InstrumentId.parse(value))
    except InvalidRequest as error:
        raise PydanticCustomError("instrument_id", "{reason}", {"reason": error.detail}) from None


def _epoch_seconds(value: Any) -> Any:
    """Epoch seconds from digits or an ISO 8601 date or date-time (without offset: UTC)."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if _DIGITS.fullmatch(text):
        seconds = int(text)
        if seconds > _MAX_EPOCH:
            raise PydanticCustomError(
                "epoch_seconds",
                "{value} is after the year 9999: send epoch seconds, not milliseconds",
                {"value": text},
            )
        return seconds
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        hint = " (in a URL, write + as %2B)" if " " in text else ""
        raise PydanticCustomError(
            "time",
            "'{value}' is neither epoch seconds nor an ISO 8601 date or date-time{hint}",
            {"value": text, "hint": hint},
        ) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return math.floor(moment.timestamp())


InstrumentParam = Annotated[str, AfterValidator(_instrument_id)]
TIME_SCHEMA = {
    "anyOf": [{"type": "integer", "minimum": 0}, {"type": "string", "format": "date-time"}],
    "examples": ["2024-06-03", "2024-06-03T13:30:00Z", 1717421400],
}
TimeParam = Annotated[int, BeforeValidator(_epoch_seconds), WithJsonSchema(TIME_SCHEMA)]


@contextmanager
def problems() -> Iterator[None]:
    """Raises the data module's errors inside the block as problem details; other modules
    whose routes ask for market data use it too (``data_problems``)."""
    try:
        yield
    except DataError as error:
        problem = _problem(error)
        if problem is None:
            raise
        raise problem from error


def _problem(error: DataError) -> ProblemError | None:  # noqa: C901, PLR0911 (a case per error)
    detail = error.detail
    match error:
        case InstrumentNotFound():
            return ProblemError(
                404,
                "instrument-not-found",
                "Instrument not found",
                detail,
                extensions={"instrument": error.instrument},
            )
        case PeriodOutOfRange():
            return ProblemError(
                422,
                "period-out-of-range",
                "Period out of range",
                detail,
                extensions={
                    "instrument": error.instrument,
                    "timeframe": error.timeframe,
                    "available_from": error.available_from,
                    "available_to": error.available_to,
                },
            )
        case TooManyCandles():
            return ProblemError(
                422,
                "too-many-candles",
                "Too many candles",
                detail,
                extensions={
                    "requested": error.requested,
                    "maximum": error.maximum,
                    "suggestion": error.suggestion,
                },
            )
        case WarmUpOutOfRange():
            return ProblemError(
                422,
                "warm-up-out-of-range",
                "Not enough candles before the period",
                detail,
                extensions={
                    "instrument": error.instrument,
                    "timeframe": error.timeframe,
                    "candles": error.candles,
                    "earliest_start": error.earliest_start,
                },
            )
        case InvalidRequest():
            return ProblemError(422, "validation", "Invalid request", detail)
        case SourceUnavailable():
            headers = {}
            if error.retry_after is not None:
                headers["Retry-After"] = str(max(1, error.retry_after))
            return ProblemError(
                503,
                "source-unavailable",
                "Data source unavailable",
                detail,
                headers=headers,
                extensions={"source": error.source},
            )
        case DataIntegrityError():
            # What failed is for the logs; the client learns which source and what to do.
            logger.error("Invalid data from %s: %s", error.source or "a source", detail)
            title = _SOURCE_TITLES.get(error.source or "", "The data source")
            return ProblemError(
                502,
                "source-data-invalid",
                "Invalid data from the source",
                f"{title} sent data for this request that failed validation, so it was not "
                "used. Try again later, or ask for another period.",
                extensions={"source": error.source},
            )
    return None


async def client_rate_limit(
    request: Request, settings: SettingsDep, limiter: RateLimiterDep
) -> None:
    """``CLIENT_RATE_LIMIT`` requests per minute (UTC) and client address to the endpoints that
    can call a source (candles and instrument detail); 429 beyond it.

    The counter is ``data:rl:client:<address>:<minute>``; without Redis the limiter lets
    requests through.
    """
    limit = settings.client_rate_limit
    wait = await limiter.hit(f"data:rl:client:{client_address(request)}", limit)
    if wait > 0:
        retry = max(1, math.ceil(wait))
        raise ProblemError(
            429,
            "rate-limited",
            "Too many requests",
            f"More than {limit} candle and instrument requests in one minute from this "
            f"address. Retry in {retry} seconds.",
            headers={"Retry-After": str(retry)},
            extensions={"limit": limit},
        )


def _errors(descriptions: dict[int, str]) -> dict[int | str, dict[str, Any]]:
    """OpenAPI error responses of a route, each a problem with the given description."""
    return {
        status: {"model": Problem, "description": description}
        for status, description in descriptions.items()
    }


_SOURCE_ERRORS = {
    502: "The source sent data that failed validation (`source-data-invalid`).",
    503: "The source is down or our request budget for it is spent (`source-unavailable`); "
    "`Retry-After` when waiting helps.",
}
_RATE_LIMITED = (
    "More candle and instrument requests from this address than the per-minute limit "
    "(`rate-limited`); see `Retry-After`."
)


@market.get(
    "/instruments",
    summary="Search instruments",
    responses=_errors({422: "Invalid parameters (`validation`).", **_SOURCE_ERRORS}),
)
async def search_instruments(  # noqa: PLR0913 (a query parameter each, and the service)
    service: DataServiceDep,
    q: Annotated[
        str | None,
        Query(
            min_length=1,
            max_length=50,
            description="Symbol or name, case-insensitive; separators in symbols are ignored "
            "(`btc/usdt` finds `BTCUSDT`, `brkb` finds `BRK.B`). Without it, every instrument "
            "in symbol order.",
            examples=["btc"],
        ),
    ] = None,
    market: Annotated[Market | None, Query(description="Only this market.")] = None,
    *,
    exchange: Annotated[
        Exchange | None,
        Query(
            description="Only instruments that trade on this exchange: `BINANCE` lists the "
            "crypto pairs, the others stocks, so with the other `market` nothing matches.",
        ),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Most items to return.")] = 20,
    offset: Annotated[
        int, Query(ge=0, description="Matches to skip; past the end, `items` is empty.")
    ] = 0,
    sort: Annotated[
        InstrumentSort,
        Query(
            description="Order: `volume` (24 h volume in USD, highest first), `change` (24 h "
            "change, highest first: the top gainers), `-change` (lowest first: the top losers) "
            "or `symbol`. Instruments without stats come last in the first three. With `q` the "
            "ranking stays first and the order only breaks its ties.",
        ),
    ] = InstrumentSort.VOLUME,
) -> InstrumentSearchOut:
    """Searches the instruments of both markets: every Binance spot pair that is trading and
    every active tradable US stock and ETF at Alpaca (without OTC). Each item carries its last
    price, 24 h change and 24 h volume in USD, refreshed every few minutes, its last 10 daily
    candles (`spark`), refreshed once a day (null without them), and the open time of its first
    candle (`available_from`, null while not known yet).

    Ranking: the top pair of a crypto base asset equal to the query (`btc` finds `BTCUSDT`
    first), exact symbol, the other pairs of that base asset, symbol prefix, prefix of a word
    in the name, substring of the symbol, substring of the name; ties go to `sort`, then to the
    shorter symbol, then alphabetically. Without `q`, every instrument comes back in `sort`:
    the most traded first by default.
    `exchange` narrows any of these to the instruments that trade on it.
    `total` counts all matches: page through them with `offset` and `limit`.

    When the instrument list of one market cannot be loaded, the other market is still
    searched and `unavailable` names the missing one; 503 when no market can be searched.

    Needs a signed-in account.
    """
    with problems():
        found = await service.search(q, market, limit, offset, exchange=exchange, sort=sort)
    return InstrumentSearchOut(
        items=[
            InstrumentItemOut.of_item(item, stats, spark, first)
            for item, stats, spark, first in zip(
                found.items, found.stats, found.sparks, found.firsts, strict=True
            )
        ],
        count=len(found.items),
        total=found.total,
        offset=offset,
        unavailable=found.unavailable,
    )


@market.get(
    "/instruments/{instrument_id}",
    summary="Instrument detail",
    dependencies=[Depends(client_rate_limit)],
    responses=_errors(
        {
            404: "The instrument is not in the catalog (`instrument-not-found`).",
            422: "Malformed instrument id (`validation`).",
            429: _RATE_LIMITED,
            **_SOURCE_ERRORS,
        }
    ),
)
async def instrument_detail(
    service: DataServiceDep,
    instrument_id: Annotated[
        InstrumentParam,
        Path(description="Instrument id, `<market>:<symbol>`.", examples=["crypto:BTCUSDT"]),
    ],
) -> InstrumentDetailOut:
    """An instrument with its timeframes, available period and the candle limit: what a valid
    candle request can ask for. Counts against the per-minute limit of requests per client
    address, like candle requests. Needs a signed-in account."""
    with problems():
        info = await service.instrument(InstrumentId.parse(instrument_id))
    return InstrumentDetailOut.of_info(info)


@market.get(
    "/instruments/{instrument_id}/candles",
    summary="Candles",
    response_model=CandlesOut,
    dependencies=[Depends(client_rate_limit)],
    responses=_errors(
        {
            404: "The instrument is not in the catalog (`instrument-not-found`).",
            422: "Invalid parameters (`validation`), a period outside the available data "
            "(`period-out-of-range`) or more candles than `max_candles` (`too-many-candles`).",
            429: _RATE_LIMITED,
            **_SOURCE_ERRORS,
        }
    ),
)
async def candles(  # noqa: PLR0913 (a query parameter each, the settings and the response)
    service: DataServiceDep,
    settings: SettingsDep,
    response: Response,
    instrument_id: Annotated[
        InstrumentParam,
        Path(description="Instrument id, `<market>:<symbol>`.", examples=["crypto:BTCUSDT"]),
    ],
    timeframe: Annotated[Timeframe, Query(examples=["1h"])],
    *,
    start: Annotated[
        TimeParam | None,
        Query(
            description="Start of the period, inclusive: UTC epoch seconds or ISO 8601 "
            "(`2024-06-03`, `2024-06-03T13:30:00Z`; without an offset it is UTC). Without it, "
            "the period starts where the last `limit` candles before `end` begin; "
            "`meta.start` tells where. Not together with `limit`.",
        ),
    ] = None,
    end: Annotated[
        TimeParam | None,
        Query(
            description="End of the period, exclusive, in the same formats; default now.",
            examples=["2024-06-04"],
        ),
    ] = None,
    limit: Annotated[
        int | None,
        Query(
            ge=1,
            le=MAX_LIMIT,
            description="Without `start`: how many of the newest candles before `end` to "
            f"return, default {DEFAULT_LIMIT} (`max_candles` when that is lower). Stocks count "
            "only candles of trading sessions (nights, weekends and holidays are skipped), "
            "crypto counts every candle. Fewer "
            "come back when the data starts later. Not together with `start` (422 "
            f"`validation`). Above {MAX_LIMIT} it is a 422 `validation`; when the server's "
            "`max_candles` (see the instrument detail) is below that, a `limit` above it is "
            "`too-many-candles`.",
        ),
    ] = None,
) -> Response:
    """Closed candles of one instrument and timeframe: those with `start <= t < end`, or, without
    `start`, the last `limit` before `end`.

    Stocks have regular-session candles only (09:30-16:00 New York), aligned to the session
    open. Missing candles are never filled; `meta.gaps` reports them. Limits: `max_candles`
    expected candles per request (see the instrument detail) and a per-minute number of
    candle and instrument requests per client address. Needs a signed-in account.
    """
    if start is not None and limit is not None:
        raise RequestValidationError(
            [
                {
                    "type": "limit_with_start",
                    "loc": ("query", "limit"),
                    "msg": "limit cannot be combined with start: send start and end, or limit "
                    "and end",
                    "input": limit,
                }
            ]
        )
    now = end is None
    if end is None:
        end = int(_time())
    if start is not None and start >= end:
        raise RequestValidationError(
            [
                {
                    "type": "period",
                    "loc": ("query", "end"),
                    "msg": f"end ({end}{', now' if now else ''}) must be after start ({start})",
                    "input": end,
                }
            ]
        )
    with problems():
        if start is None:
            found = await service.last_candles(
                InstrumentId.parse(instrument_id),
                timeframe,
                end,
                limit or min(DEFAULT_LIMIT, settings.candles_max),
            )
        else:
            found = await service.candles(InstrumentId.parse(instrument_id), timeframe, start, end)
    sent = Response(candles_json(found), media_type="application/json")
    # The session cookie the sign-in check renewed on `response`, which FastAPI drops from a
    # returned response.
    for cookie in response.headers.getlist("set-cookie"):
        sent.headers.append("set-cookie", cookie)
    return sent


@data.get(
    "/top-instruments",
    summary="Top instruments",
    responses=_errors(_SOURCE_ERRORS),
)
async def top_instruments(service: DataServiceDep, response: Response) -> TopInstrumentsOut:
    """The five most traded instruments, as the first five items of the list without a query,
    for the public home page. Public, unlike the list: only these few rows, the same for
    everyone, kept a minute on the server and in the browser (`Cache-Control`).

    503 when no market's instrument list can be loaded."""
    with problems():
        found = await service.top()
    response.headers["Cache-Control"] = f"public, max-age={TOP_SECONDS}"
    return TopInstrumentsOut(
        items=[
            InstrumentItemOut.of_item(item, stats, spark, first)
            for item, stats, spark, first in zip(
                found.items, found.stats, found.sparks, found.firsts, strict=True
            )
        ]
    )


_IMAGE = {"schema": {"type": "string", "format": "binary"}}


@data.get(
    "/instruments/{instrument_id}/icon",
    summary="Instrument icon",
    response_class=Response,
    responses={
        200: {
            "description": "The logo (PNG, JPEG or WebP), or the fallback image (SVG).",
            "content": {
                "image/png": _IMAGE,
                "image/jpeg": _IMAGE,
                "image/webp": _IMAGE,
                "image/svg+xml": _IMAGE,
            },
        },
        **_errors(
            {
                404: "The instrument is not in the catalog (`instrument-not-found`).",
                422: "Malformed instrument id (`validation`).",
            }
        ),
    },
)
async def instrument_icon(
    service: DataServiceDep,
    instrument_id: Annotated[
        InstrumentParam,
        Path(description="Instrument id, `<market>:<symbol>`.", examples=["stock:AAPL"]),
    ],
) -> Response:
    """The instrument's logo from logo.dev (a stock by its ticker, a crypto pair by its base
    asset), or one fallback image when there is none, so an `<img>` always gets a picture.

    Logos are fetched once and kept for days: `Cache-Control` lets a browser keep a logo a day,
    the fallback an hour (a minute when logo.dev could not be asked, so a logo shows up as soon
    as it answers again). Not counted against the per-minute limit of candle and instrument
    requests: a page of the list asks for one icon per row. Public: logos are not market data.
    """
    with problems():
        icon = await service.icon(InstrumentId.parse(instrument_id))
    return Response(
        icon.content,
        media_type=icon.media_type,
        headers={"Cache-Control": f"public, max-age={icon.max_age}"},
    )


@health.get(
    "/api/health/sources",
    summary="Health of the data sources",
    responses={503: {"model": SourcesHealthOut, "description": "A source is unreachable"}},
)
async def sources_health(service: DataServiceDep, response: Response) -> SourcesHealthOut:
    """Reachability of Binance (`GET /api/v3/ping`) and Alpaca (`GET /v2/clock`), checked at
    most once a minute. 200 when both answer, 503 with the same body when one does not."""
    body = SourcesHealthOut.of(await service.sources_health())
    if body.status != "ok":
        response.status_code = 503
    return body


router.include_router(data)
router.include_router(health)
