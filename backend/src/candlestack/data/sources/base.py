"""What the service needs from a source, and the helpers both sources share."""

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
import orjson
import polars as pl

from candlestack.core import RateLimiter, request_with_retry, retry_after
from candlestack.data.errors import DataIntegrityError, SourceUnavailable
from candlestack.data.models import (
    Feed,
    Instrument,
    Market,
    Source,
    SourceHealth,
    SourceStats,
    Timeframe,
)
from candlestack.data.sessions import Session

DAY = 86_400
LIVE_TTL = 60
# The longest we wait for our own request budget before answering 503.
MAX_BUDGET_WAIT = 5.0

# Patched by tests to avoid real waiting.
_sleep = asyncio.sleep


@dataclass(frozen=True, slots=True)
class Period:
    """A span of candles fetched and cached as one chunk: ``[start, end)`` in UTC epoch seconds.

    ``kind`` is ``closed`` for a closed calendar month or year, ``current`` for a closed part
    of the current one (a day, or the days so far) and ``live`` for today, which ends at the
    fetch time. ``label`` names the chunk in its cache key (``2024-06``, ``2026-09-25``,
    ``2026-09-26-live``) and ``ttl`` is how long it is cached.
    """

    kind: Literal["closed", "current", "live"]
    start: int
    end: int
    label: str
    ttl: int


class DataSource(Protocol):
    """One market's source. Symbols are the source's own spelling (``BTCUSDT``, ``BRK.B``)."""

    name: Source
    feed: Feed
    market: Market
    chunk_concurrency: int
    """Chunks of one request fetched at once."""

    async def list_instruments(self) -> list[Instrument]:
        """The catalog: every instrument the source serves now."""
        ...

    async def sessions(self) -> list[Session] | None:
        """Trading sessions for session bins, or ``None`` for a market that trades around the
        clock (fixed UTC bins)."""
        ...

    async def first_timestamp(self, symbol: str) -> int | None:
        """Open time of the symbol's first candle, ``None`` when it has none."""
        ...

    def base_timeframe(self, timeframe: Timeframe) -> Timeframe:
        """The timeframe fetched from the source to build ``timeframe``."""
        ...

    def periods(self, timeframe: Timeframe, start: int, end: int, now: int) -> list[Period]:
        """The chunks of a base timeframe that cover ``[start, end)`` at ``now``."""
        ...

    async def fetch_period(
        self, symbol: str, timeframe: Timeframe, period: Period, *, background: bool = False
    ) -> pl.DataFrame:
        """Candles of the period in ``CANDLE_SCHEMA``: normalised, validated, closed only. A
        ``background`` load (a backtest's) may wait for its share of the request budget instead
        of failing when the budget is spent."""
        ...

    async def health(self) -> SourceHealth:
        """Whether the source answers its cheapest request."""
        ...

    async def stats(self, instruments: Sequence[Instrument]) -> SourceStats:
        """The 24 h stats (``STATS_SCHEMA``) of the given instruments of the catalog, one row
        per instrument the source has numbers for, and the symbols it could not ask for. Runs
        in the background, never for a request: it may wait for its share of the request
        budget. Raises the source's error when it got nothing."""
        ...

    async def daily_candles(self, instruments: Sequence[Instrument], closed_at: int) -> SourceStats:
        """The last ``SPARK_DAYS`` daily candles (``SPARK_SCHEMA``) that closed by ``closed_at``
        of the given instruments of the catalog, and the symbols it could not ask for. Runs in
        the background, never for a request: it is paced to a share of the request budget.
        Raises the source's error when it got nothing."""
        ...


def utc(ts: int) -> datetime:
    return datetime.fromtimestamp(ts, UTC)


def epoch(moment: datetime) -> int:
    return int(moment.timestamp())


def _span_start(ts: int, *, yearly: bool) -> int:
    moment = utc(ts)
    return epoch(datetime(moment.year, 1 if yearly else moment.month, 1, tzinfo=UTC))


def _next_span(start: int, *, yearly: bool) -> int:
    moment = utc(start)
    if yearly or moment.month == 12:
        return epoch(datetime(moment.year + 1, 1, 1, tzinfo=UTC))
    return epoch(datetime(moment.year, moment.month + 1, 1, tzinfo=UTC))


# The request, then the caller's chunking policy (keyword-only).
def plan_periods(  # noqa: PLR0913
    start: int,
    end: int,
    now: int,
    *,
    yearly: bool,
    daily: bool,
    closed_ttl: int,
    current_ttl: int,
) -> list[Period]:
    """Chunks that cover ``[start, end)`` at ``now``, in time order; nothing after ``now``
    (an ``end`` after it adds nothing).

    - each closed calendar span, a month (a year with ``yearly``): ``2024-06`` or ``2024``,
      cached ``closed_ttl``;
    - the part of the current span before today: each day with ``daily`` (``2026-09-25``),
      otherwise one chunk (``2026-09-01..2026-09-26``), cached ``current_ttl``;
    - today up to ``now``, the live tail (``2026-09-26-live``), cached ``LIVE_TTL``.
    """
    today = now // DAY * DAY
    current = _span_start(today, yearly=yearly)
    span_format = "%Y" if yearly else "%Y-%m"
    periods = []
    span = _span_start(start, yearly=yearly)
    while span < min(end, current):
        following = _next_span(span, yearly=yearly)
        label = utc(span).strftime(span_format)
        periods.append(Period("closed", span, following, label, closed_ttl))
        span = following
    if start < today and end > current:
        if daily:
            day = max(start // DAY * DAY, current)
            while day < min(end, today):
                label = f"{utc(day):%Y-%m-%d}"
                periods.append(Period("current", day, day + DAY, label, current_ttl))
                day += DAY
        elif current < today:
            label = f"{utc(current):%Y-%m-%d}..{utc(today):%Y-%m-%d}"
            periods.append(Period("current", current, today, label, current_ttl))
    if end > today:
        periods.append(Period("live", today, now, f"{utc(today):%Y-%m-%d}-live", LIVE_TTL))
    return periods


async def pause(seconds: float) -> None:
    """Waits ``seconds``: background work waiting for its share of a budget."""
    await _sleep(seconds)


async def spend(
    limiter: RateLimiter, source: Source, what: str, key: str, limit: int, *, cost: int = 1
) -> None:
    """Takes ``cost`` from our budget at a source; waits for the next window when that is at
    most ``MAX_BUDGET_WAIT`` away, else raises ``SourceUnavailable`` with ``retry_after``.
    ``what`` names the budget for the message, e.g. ``Alpaca request budget (60 per minute)``.
    """
    wait = await limiter.hit(key, limit, cost)
    if wait > 0 and wait <= MAX_BUDGET_WAIT:
        await _sleep(wait)
        wait = await limiter.hit(key, limit, cost)
    if wait > 0:
        seconds = math.ceil(wait)
        raise SourceUnavailable(
            source,
            f"Our {what} is spent. Try again in {seconds} seconds.",
            retry_after=seconds,
        )


async def get(
    client: httpx.AsyncClient, source: Source, title: str, url: str, **kwargs: Any
) -> httpx.Response:
    """GET with retries (``request_with_retry``); a timeout or connection failure becomes
    ``SourceUnavailable``. The caller checks the status."""
    try:
        return await request_with_retry(client, "GET", url, **kwargs)
    except httpx.TimeoutException:
        raise SourceUnavailable(
            source, f"{title} did not answer in time. Try again in a minute."
        ) from None
    except httpx.HTTPError as exc:
        raise SourceUnavailable(
            source, f"{title} could not be reached ({type(exc).__name__}). Try again in a minute."
        ) from None


def json_body(response: httpx.Response, source: Source, title: str) -> Any:
    """The parsed JSON body; ``DataIntegrityError`` when it is not JSON."""
    try:
        return orjson.loads(response.content)
    except orjson.JSONDecodeError as exc:
        raise DataIntegrityError(
            f"{title} sent a response that is not valid JSON: {exc}", source=source
        ) from None


def unavailable(source: Source, title: str, response: httpx.Response) -> SourceUnavailable:
    """The error for a response that is no answer: 5xx after retries, 429 or anything else
    unexpected. Error bodies are not trusted to be JSON (Alpaca answers 401 in HTML)."""
    status = response.status_code
    if status == 429:
        seconds = math.ceil(retry_after(response) or 60)
        return SourceUnavailable(
            source,
            f"{title} is rate limiting our requests. Try again in {seconds} seconds.",
            retry_after=seconds,
        )
    if status >= 500:
        return SourceUnavailable(
            source, f"{title} failed with HTTP {status}. Try again in a minute."
        )
    return SourceUnavailable(
        source,
        f"{title} rejected our request with HTTP {status}. Try again later.",
        rejected=400 <= status < 500,
    )
