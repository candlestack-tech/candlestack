"""Alpaca, the source of the US stock market: SIP feed (all US exchanges), paper account keys.

The free plan serves SIP bars only up to 15 minutes ago (``SIP_DELAY``): no request asks for
anything newer, and the candles of the last 15 minutes come once they are that old. Intraday
timeframes are built from Alpaca's bars of the regular sessions: 1m, 5m, 15m and 30m from the
bars of their own timeframe, 1h and 4h from 30Min bars (``BASE_TIMEFRAMES``); 1d is Alpaca's
1Day bar (official open and close, extended-hours volume), relabelled from 00:00 New York to
the session open. Trading days
come from ``GET /v2/calendar``. Every request spends our budget (``ALPACA_RATE_LIMIT``); Alpaca
allows 200 per minute per key. The 24 h stats come from ``GET /v2/stocks/snapshots`` of the SIP
feed delayed by 15 minutes and the daily candles of the mini charts from the 1Day bars, many
symbols per request, paced to a share of the budget. The candles of a backtest wait for a share
of their own instead of failing when the budget is spent.
"""

import bisect
import logging
import math
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from itertools import batched
from typing import Any

import httpx
import orjson
import polars as pl
from anyio import to_thread

from candlestack.core import RateLimiter, Settings
from candlestack.data.cache import Cache
from candlestack.data.candles import (
    closed_only,
    normalise,
    resample_sessions,
    slice_range,
    validate,
)
from candlestack.data.errors import DataError, DataIntegrityError, InvalidRequest, SourceUnavailable
from candlestack.data.models import (
    CANDLE_SCHEMA,
    SPARK_DAYS,
    SPARK_SCHEMA,
    STATS_SCHEMA,
    Exchange,
    Feed,
    Instrument,
    InstrumentId,
    Market,
    Source,
    SourceHealth,
    SourceStats,
    Timeframe,
)
from candlestack.data.sessions import NEW_YORK, Session, parse_calendar
from candlestack.data.sources.base import (
    DAY,
    LIVE_TTL,
    Period,
    get,
    json_body,
    pause,
    plan_periods,
    spend,
    unavailable,
    utc,
)

logger = logging.getLogger(__name__)

NAME: Source = "alpaca"
TITLE = "Alpaca"
RATE_KEY = "data:rl:alpaca"
# The free plan answers SIP bars only for a query whose end is at least this old; a newer end is
# refused ("subscription does not permit querying recent SIP data").
SIP_DELAY = 900
# The latest extended-hours trade of a day is at 20:00 New York, at most 01:00 UTC the next day,
# and a 1Day bar counts its volume: a chunk of 1Day bars that ends at a UTC midnight is final
# this long after it.
DAILY_FINAL_SECONDS = 3600 + SIP_DELAY
# adjustment=all rewrites past bars after every dividend or split, so closed periods are
# refetched daily.
CLOSED_TTL = DAY
CURRENT_TTL = DAY
CALENDAR_TTL = 7 * DAY
# How long the parsed calendar is kept in memory before Redis is asked again.
CALENDAR_MEMORY_SECONDS = 3600
HISTORY_START = 2016
BARS_LIMIT = 10_000
# The bars each timeframe is built from. Alpaca's 5Min, 15Min and 30Min bars start on the clock's
# quarter and half hours, as the sessions' 09:30 open does, so they are our session bins (checked
# equal to resampled 1m bars, docs/data.md); 1Hour bars start on the clock hour (09:00 holds
# 09:30-09:59), so 1h and 4h are built from 30Min bars.
BASE_TIMEFRAMES = {
    Timeframe.M1: Timeframe.M1,
    Timeframe.M5: Timeframe.M5,
    Timeframe.M15: Timeframe.M15,
    Timeframe.M30: Timeframe.M30,
    Timeframe.H1: Timeframe.M30,
    Timeframe.H4: Timeframe.M30,
    Timeframe.D1: Timeframe.D1,
}
_ALPACA_TIMEFRAMES = {
    Timeframe.M1: "1Min",
    Timeframe.M5: "5Min",
    Timeframe.M15: "15Min",
    Timeframe.M30: "30Min",
    Timeframe.D1: "1Day",
}
# Base timeframes fetched in yearly chunks, the others in monthly ones, so that a chunk is one to
# three requests of 10000 bars with extended hours (04:00-20:00 New York): a month of 1Min bars
# is about 20000, of 5Min about 4000; a year of 15Min bars is about 16000, of 30Min about 8000,
# of 1Day about 250.
_YEARLY = frozenset({Timeframe.M15, Timeframe.M30, Timeframe.D1})
# Snapshots: symbols per request (about 27 requests for the whole catalog, each URL under
# 4 KB), and the share of our budget per minute they may take, so that chart requests keep the
# rest (prod and stage 180: 20).
SNAPSHOT_SYMBOLS = 500
STATS_RATE_KEY = "data:rl:alpaca-stats"
STATS_SHARE_MAX = 20
STATS_SHARE_DIVISOR = 5
# The daily bars of the mini charts (about 27 requests once a day after the close) have their own,
# smaller share, so they do not slow the stats refresh (prod and stage 180: 5).
SPARK_RATE_KEY = "data:rl:alpaca-spark"
SPARK_SHARE_MAX = 5
SPARK_SHARE_DIVISOR = 20
# Background loads (a backtest's candles, which can take more requests than a minute's budget)
# wait for their own share instead of failing, all of them together, and then for room in the
# whole budget.
# Half of it, so requests keep the other half minus the stats' and mini charts' shares
# (prod and stage 180: 90, requests keep 65 or more, 90 while no refresh runs).
BACKGROUND_RATE_KEY = "data:rl:alpaca-background"
BACKGROUND_SHARE_MAX = 90
BACKGROUND_SHARE_DIVISOR = 2
# A daily bar older than this is from a stock that has not traded on any exchange lately (halted,
# suspended): it has no 24 h change and no 24 h volume (0) rather than those of an old session.
# Covers a long weekend.
STALE_BAR_SECONDS = 5 * DAY

# The values of Exchange a stock can have; BINANCE is the crypto pairs'.
STOCK_EXCHANGES = frozenset(str(each) for each in Exchange if each.market is Market.STOCK)

# Patched by tests to move the clock.
_time = time.time

_COLUMNS = list(CANDLE_SCHEMA)
_BAR_SCHEMA = {
    "t": pl.String,
    "o": pl.Float64,
    "h": pl.Float64,
    "l": pl.Float64,
    "c": pl.Float64,
    "v": pl.Float64,
}


def parse_assets(content: bytes) -> list[Instrument]:
    """The tradable, non-OTC US equities (stocks and ETFs) of a ``GET /v2/assets`` body."""
    try:
        assets = [
            (item["symbol"], item.get("name") or item["symbol"], item["exchange"])
            for item in orjson.loads(content)
            if item["tradable"] and item["exchange"] != "OTC"
        ]
    except (orjson.JSONDecodeError, KeyError, TypeError) as exc:
        raise DataIntegrityError(
            f"Alpaca assets cannot be read: {type(exc).__name__} {exc}", source=NAME
        ) from None
    instruments = []
    # Counted by name, so that any value Alpaca sends (even null) sorts for the warning.
    unknown: Counter[str] = Counter()
    for symbol, name, exchange in assets:
        try:
            instrument_id = InstrumentId.parse(f"stock:{symbol}")
        except InvalidRequest:
            instrument_id = None
        if instrument_id is None or instrument_id.symbol != symbol:
            logger.warning("Skipping Alpaca symbol %r: not a valid instrument symbol", symbol)
            continue
        known = str(exchange) in STOCK_EXCHANGES
        if not known:
            unknown[str(exchange)] += 1
        instruments.append(
            Instrument(
                id=instrument_id,
                name=name,
                source=NAME,
                feed="sip",
                exchange=exchange if known else None,
            )
        )
    if unknown:
        # Still listed, without an exchange: the filter cannot find them until Exchange has the
        # value as a stock exchange, and a stock "on BINANCE" never passes for a crypto pair.
        logger.warning(
            "Alpaca lists stocks on exchanges outside Exchange: %s",
            ", ".join(f"{exchange} ({count})" for exchange, count in sorted(unknown.items())),
        )
    return instruments


def parse_bars(bars: list[Any]) -> pl.DataFrame:
    """Bars of ``GET /v2/stocks/bars`` (``t`` RFC 3339, bar open) as candles."""
    try:
        frame = pl.from_dicts(bars, schema=_BAR_SCHEMA)
        return frame.select(
            ts=pl.col("t").str.to_datetime(time_zone="UTC").dt.epoch("s"),
            open="o",
            high="h",
            low="l",
            close="c",
            volume="v",
        )
    except (TypeError, ValueError, pl.exceptions.PolarsError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise DataIntegrityError(f"Alpaca bars cannot be read: {reason}", source=NAME) from None


def _positive(value: Any) -> float | None:
    """A positive finite number, else ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _part(snapshot: Any, key: str) -> dict[str, Any]:
    """A part of a snapshot (``latestTrade``, ``dailyBar``, ...): an object, ``{}`` when it is
    missing or null."""
    if not isinstance(snapshot, dict):
        raise TypeError("the snapshot is not an object")
    part = snapshot.get(key)
    if part is None:
        return {}
    if not isinstance(part, dict):
        raise TypeError(f"{key} is not an object")
    return part


def snapshot_stats(snapshot: Any, now: int) -> tuple[float, float | None, float | None] | None:
    """Price, 24 h change in percent and 24 h volume in USD of one snapshot, or ``None``
    without a price.

    The price is the latest trade (the daily bar's close without one), the change is against
    the previous daily bar's close, and the volume is the daily bar's volume times its
    volume-weighted price (its close without one), both of every US exchange, extended hours
    included.
    A daily bar older than ``STALE_BAR_SECONDS`` gives no change and a volume of 0. Raises
    ``TypeError`` for a snapshot, trade or bar that is not an object (``null`` is none).
    """
    if snapshot is None:
        return None
    trade, daily, previous = (
        _part(snapshot, key) for key in ("latestTrade", "dailyBar", "prevDailyBar")
    )
    price = _positive(trade.get("p")) or _positive(daily.get("c"))
    if price is None:
        return None
    try:
        opened = int(datetime.fromisoformat(daily["t"]).timestamp())
    except (KeyError, TypeError, ValueError):
        return price, None, None
    if now - opened > STALE_BAR_SECONDS:
        return price, None, 0.0
    close = _positive(previous.get("c"))
    change = None if close is None else (price / close - 1) * 100
    shares = daily.get("v")
    volume = (
        shares * (_positive(daily.get("vw")) or price)
        if isinstance(shares, int | float) and math.isfinite(shares) and shares >= 0
        else None
    )
    return price, change, volume


def parse_snapshots(content: bytes, now: int) -> tuple[pl.DataFrame, int]:
    """The stats (``STATS_SCHEMA``) of a ``GET /v2/stocks/snapshots`` body, an object by symbol,
    and the number of malformed snapshots skipped; symbols without a snapshot or a price have
    none (``snapshot_stats``)."""
    try:
        body = orjson.loads(content)
    except orjson.JSONDecodeError as exc:
        raise DataIntegrityError(
            f"Alpaca snapshots are not valid JSON: {exc}", source=NAME
        ) from None
    if not isinstance(body, dict):
        raise DataIntegrityError("Alpaca snapshots response is not an object.", source=NAME)
    rows, malformed = [], 0
    for symbol, snapshot in body.items():
        try:
            stats = snapshot_stats(snapshot, now)
        except TypeError:
            malformed += 1
            continue
        if stats is not None:
            rows.append((symbol, *stats))
    return pl.DataFrame(rows, schema=STATS_SCHEMA, orient="row"), malformed


def relabel_daily(frame: pl.DataFrame, sessions: list[Session]) -> pl.DataFrame:
    """1Day bars (stamped 00:00 New York) labelled with the open of that day's session; bars of
    days without a session are dropped, other columns (``symbol``) kept."""
    opens = pl.DataFrame(
        {
            "date": [session.date for session in sessions],
            "open_ts": [session.open for session in sessions],
        },
        schema={"date": pl.Date, "open_ts": pl.Int64},
    )
    moment = pl.from_epoch("ts", time_unit="s").dt.replace_time_zone("UTC")
    return (
        frame.with_columns(date=moment.dt.convert_time_zone(NEW_YORK.key).dt.date())
        .join(opens, on="date", how="inner")
        .with_columns(ts="open_ts")
        .drop("date", "open_ts")
        .sort("ts")
    )


def _daily_bar(bar: Any) -> tuple[int, float, float, float, float] | None:
    """Open time and prices of a 1Day bar, or ``None`` when it is malformed."""
    if not isinstance(bar, dict):
        return None
    prices = [bar.get(key) for key in ("o", "h", "l", "c")]
    if not all(
        isinstance(price, int | float) and not isinstance(price, bool) and math.isfinite(price)
        for price in prices
    ):
        return None
    try:
        opened = datetime.fromisoformat(bar["t"])
    except (KeyError, TypeError, ValueError):
        return None
    if opened.tzinfo is None:
        return None
    open_, high, low, close = (float(price) for price in prices)
    return int(opened.timestamp()), open_, high, low, close


def parse_daily_bars(body: dict[str, Any], days: list[Session]) -> tuple[pl.DataFrame, int]:
    """The daily candles (``SPARK_SCHEMA``) of a ``GET /v2/stocks/bars`` page of 1Day bars of
    many symbols, labelled with the open of their session in ``days`` (``relabel_daily``), and
    the number of malformed bars skipped: one bad bar costs only itself."""
    by_symbol = body.get("bars") or {}
    if not isinstance(by_symbol, dict) or not all(
        isinstance(bars, list) for bars in by_symbol.values() if bars is not None
    ):
        raise DataIntegrityError("Alpaca bars response has no list of bars.", source=NAME)
    rows, malformed = [], 0
    for symbol, bars in by_symbol.items():
        for bar in bars or ():
            parsed = _daily_bar(bar)
            if parsed is None:
                malformed += 1
            else:
                rows.append((symbol, *parsed))
    frame = pl.DataFrame(rows, schema=SPARK_SCHEMA, orient="row")
    return relabel_daily(frame, days).select(*SPARK_SCHEMA), malformed


def regular_candles(
    frame: pl.DataFrame, timeframe: Timeframe, period: Period, sessions: list[Session]
) -> pl.DataFrame:
    """Validated candles of the period from parsed bars (``parse_bars``) of a base timeframe:
    an intraday one (bars outside the regular sessions dropped; Alpaca's bins are the session
    bins, ``BASE_TIMEFRAMES``) or ``1d`` (1Day bars relabelled); only candles closed by the end of
    the period."""
    if timeframe is Timeframe.D1:
        frame = relabel_daily(frame, sessions)
    frame = resample_sessions(normalise(frame), timeframe, sessions)
    frame = closed_only(
        slice_range(frame, period.start, period.end), timeframe, period.end, sessions
    )
    try:
        validate(frame)
    except DataIntegrityError as exc:
        raise DataIntegrityError(exc.detail, source=NAME) from None
    return frame


def _rfc3339(ts: int) -> str:
    return f"{utc(ts):%Y-%m-%dT%H:%M:%SZ}"


def _span(start: int, end: int) -> dict[str, str | int]:
    """``start`` and ``end`` of a bars request for ``[start, end)``: Alpaca's end is inclusive,
    and it is never within the last ``SIP_DELAY``, which the free plan refuses."""
    end = min(end, int(_time()) - SIP_DELAY)
    return {"start": _rfc3339(start), "end": _rfc3339(end - 1)}


@dataclass(frozen=True)
class _Job:
    """What a refresh in batches asks for, as its log lines name it."""

    what: str  # what Alpaca answers: "snapshots"
    kept: str  # what the stocks have: "stats"


_SNAPSHOTS = _Job("snapshots", "stats")
_DAILY_BARS = _Job("daily bars", "daily candles")

Batch = tuple[str, ...]
"""Symbols asked for in one request of a refresh in batches."""


@dataclass
class _Batches:
    """What the requests of one refresh in batches got so far."""

    total: int
    frames: list[pl.DataFrame] = field(default_factory=list)
    malformed: int = 0
    response: httpx.Response | None = None
    error: DataError | None = None
    # Symbols Alpaca refused even alone (a 4xx for a request of just that symbol).
    refused: list[str] = field(default_factory=list)
    # Batches that failed otherwise, twice: their stocks keep what they had.
    unanswered: list[Batch] = field(default_factory=list)

    def result(self, schema: pl.Schema) -> SourceStats:
        return SourceStats(
            pl.concat(self.frames) if self.frames else pl.DataFrame(schema=schema),
            frozenset(symbol for batch in self.unanswered for symbol in batch),
        )

    def log(self, job: _Job) -> None:
        if self.unanswered and self.error is not None:
            logger.warning(
                "Alpaca %s of %d of %d stocks failed (%s): their last %s stay",
                job.what,
                sum(len(batch) for batch in self.unanswered),
                self.total,
                self.error.detail,
                job.kept,
            )
        if self.refused:
            logger.warning(
                "Alpaca refused the %s of %s: no %s for them",
                job.what,
                ", ".join(self.refused),
                job.kept,
            )
        if self.malformed:
            logger.warning("Skipped %d malformed Alpaca %s", self.malformed, job.what)


Ask = Callable[[Batch], Awaitable[tuple[pl.DataFrame, int, httpx.Response | None]]]
"""Asks for a batch: its rows, the number of malformed items skipped, and the response whose
rate-limit headers are logged (or ``None``)."""


def _refused(error: DataError) -> bool:
    """Whether Alpaca refused the request itself (a 4xx other than 429): splitting it can find
    the symbol it refuses."""
    return isinstance(error, SourceUnavailable) and error.rejected


class AlpacaSource:
    name: Source = NAME
    feed: Feed = "sip"
    market = Market.STOCK
    # A chunk of 1m bars is a month, up to about 20000 bars with extended hours (2 or 3 pages)
    # parsed from JSON while it is fetched, and months of them build one 1m request: few at once
    # keep the memory of a request small.
    chunk_concurrency = 4

    def __init__(
        self, settings: Settings, http: httpx.AsyncClient, limiter: RateLimiter, cache: Cache
    ) -> None:
        self._api = settings.alpaca_api_url.rstrip("/")
        self._data = settings.alpaca_data_url.rstrip("/")
        key_id = settings.alpaca_key_id.get_secret_value()
        secret = settings.alpaca_secret_key.get_secret_value()
        self._headers = (
            {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
            if key_id and secret
            else None
        )
        self._http = http
        self._limiter = limiter
        self._limit = settings.alpaca_rate_limit
        self._stats_limit = max(1, min(STATS_SHARE_MAX, self._limit // STATS_SHARE_DIVISOR))
        self._spark_limit = max(1, min(SPARK_SHARE_MAX, self._limit // SPARK_SHARE_DIVISOR))
        self._background_limit = max(
            1, min(BACKGROUND_SHARE_MAX, self._limit // BACKGROUND_SHARE_DIVISOR)
        )
        self._cache = cache
        self._sessions: list[Session] = []
        self._sessions_until = 0.0

    async def list_instruments(self) -> list[Instrument]:
        response = await self._request(
            f"{self._api}/v2/assets", {"status": "active", "asset_class": "us_equity"}
        )
        return await to_thread.run_sync(parse_assets, response.content)

    async def sessions(self) -> list[Session]:
        """NYSE sessions from 2016 to the end of next year: cached 7 days in Redis and parsed
        once an hour."""
        if time.monotonic() >= self._sessions_until:
            blob = await self._cache.get_or_fetch("calendar", self._fetch_calendar)
            self._sessions = await to_thread.run_sync(_parse_calendar, blob)
            self._sessions_until = time.monotonic() + CALENDAR_MEMORY_SECONDS
        return self._sessions

    async def first_timestamp(self, symbol: str) -> int | None:
        """Open of the session of the first 1Day bar (SIP data starts in January 2016)."""
        start = int(datetime(HISTORY_START, 1, 1, tzinfo=UTC).timestamp())
        body = await self._bars_page(symbol, "1Day", {**_span(start, int(_time())), "limit": 1})
        frame = parse_bars(_symbol_bars(body, symbol))
        if not frame.height:
            return None
        sessions = await self.sessions()
        first = bisect.bisect_left(sessions, frame["ts"][0], key=lambda session: session.open)
        return sessions[first].open if first < len(sessions) else None

    def base_timeframe(self, timeframe: Timeframe) -> Timeframe:
        return BASE_TIMEFRAMES[timeframe]

    def periods(self, timeframe: Timeframe, start: int, end: int, now: int) -> list[Period]:
        # A request returns up to 10000 bars: whole months or years (``_YEARLY``) keep the number
        # of requests (our budget) small. The chunks are planned as of SIP_DELAY ago: the live
        # one ends there (and is complete until then), and a day joins a cached chunk only once
        # its bars are that old.
        periods = plan_periods(
            start,
            end,
            now - SIP_DELAY,
            yearly=timeframe in _YEARLY,
            daily=False,
            closed_ttl=CLOSED_TTL,
            current_ttl=CURRENT_TTL,
        )
        if timeframe is not Timeframe.D1:
            return periods
        # The intraday bars of a regular session are final 15 minutes after its close, long
        # before the UTC midnight that ends a chunk; its 1Day bar only once extended hours ended.
        # A chunk fetched before that is kept until then, and then fetched again.
        return [
            replace(period, ttl=max(LIVE_TTL, final - now))
            if period.kind != "live" and (final := period.end + DAILY_FINAL_SECONDS) > now
            else period
            for period in periods
        ]

    async def fetch_period(
        self, symbol: str, timeframe: Timeframe, period: Period, *, background: bool = False
    ) -> pl.DataFrame:
        frame = await self._bars(symbol, timeframe, period.start, period.end, background=background)
        sessions = await self.sessions()
        return await to_thread.run_sync(regular_candles, frame, timeframe, period, sessions)

    async def health(self) -> SourceHealth:
        started = time.perf_counter()
        try:
            await self._request(f"{self._api}/v2/clock", {})
        except DataError as exc:
            return SourceHealth(ok=False, latency_ms=None, detail=exc.detail)
        return SourceHealth(
            ok=True, latency_ms=round((time.perf_counter() - started) * 1000), detail=None
        )

    async def stats(self, instruments: Sequence[Instrument]) -> SourceStats:
        """Snapshots of ``SNAPSHOT_SYMBOLS`` stocks per request (``_in_batches``). Logs Alpaca's
        rate-limit headers of the last answer next to our own count, once."""
        got = await self._in_batches(instruments, self._snapshot_batch, _SNAPSHOTS)
        await self._log_rate_limit(got)
        return got.result(STATS_SCHEMA)

    async def daily_candles(self, instruments: Sequence[Instrument], closed_at: int) -> SourceStats:
        """The 1Day bars of the last ``SPARK_DAYS`` sessions that closed by ``closed_at``, of
        ``SNAPSHOT_SYMBOLS`` stocks per request (``_in_batches``), labelled with the session
        open as the 1d candles are."""
        sessions = await self.sessions()
        last = bisect.bisect_right(sessions, closed_at, key=lambda session: session.close)
        days = sessions[max(0, last - SPARK_DAYS) : last]
        if not days:
            return SourceStats(pl.DataFrame(schema=SPARK_SCHEMA))
        # The UTC midnight of the first day: its bar is stamped 00:00 New York, a few hours later.
        start = days[0].open // DAY * DAY
        ask = partial(self._daily_batch, start, closed_at, days)
        return (await self._in_batches(instruments, ask, _DAILY_BARS)).result(SPARK_SCHEMA)

    async def _in_batches(self, instruments: Sequence[Instrument], ask: Ask, job: _Job) -> _Batches:
        """Asks for the stocks in batches of ``SNAPSHOT_SYMBOLS``, one after another, each within
        its share of the budget (waiting for it as long as needed) and our whole budget. A
        request that fails is tried once more after the others; one Alpaca still refuses (a
        4xx) is split in halves until the symbols it refuses are found, which get nothing. The
        stocks of a request that still fails otherwise keep what they had."""
        batches = list(batched((i.symbol for i in instruments), SNAPSHOT_SYMBOLS, strict=False))
        got = _Batches(total=len(instruments))
        failed = await self._batches(batches, got, ask)
        if failed:
            # The whole budget may have been spent by chart requests: wait as it says, once.
            if isinstance(got.error, SourceUnavailable) and got.error.retry_after:
                await pause(min(got.error.retry_after, 60))
            failed = await self._batches([batch for batch, _ in failed], got, ask)
        for batch, error in failed:
            got.unanswered += await self._narrow(batch, got, ask) if _refused(error) else [batch]
        if not got.frames and got.error is not None and (got.unanswered or got.refused):
            raise got.error
        got.log(job)
        return got

    async def _batches(
        self, batches: list[Batch], got: _Batches, ask: Ask
    ) -> list[tuple[Batch, DataError]]:
        """Asks for each batch into ``got``; returns the batches that failed, each with its
        error."""
        failed = []
        for symbols in batches:
            try:
                frame, malformed, response = await ask(symbols)
            except DataError as exc:
                failed.append((symbols, exc))
                got.error = exc
                continue
            got.frames.append(frame)
            got.malformed += malformed
            got.response = response or got.response
        return failed

    async def _narrow(self, batch: Batch, got: _Batches, ask: Ask) -> list[Batch]:
        """Asks for the two halves of a batch Alpaca refused, and so on down to single symbols,
        which go to ``got.refused``; returns the parts that failed for another reason."""
        if len(batch) == 1:
            got.refused.extend(batch)
            return []
        half = len(batch) // 2
        unanswered: list[Batch] = []
        for part, error in await self._batches([batch[:half], batch[half:]], got, ask):
            unanswered += await self._narrow(part, got, ask) if _refused(error) else [part]
        return unanswered

    async def _snapshot_batch(self, symbols: Batch) -> tuple[pl.DataFrame, int, httpx.Response]:
        await self._turn(STATS_RATE_KEY, self._stats_limit)
        response = await self._request(
            f"{self._data}/v2/stocks/snapshots",
            {"symbols": ",".join(symbols), "feed": "delayed_sip"},
        )
        frame, malformed = await to_thread.run_sync(parse_snapshots, response.content, int(_time()))
        return frame, malformed, response

    async def _daily_batch(
        self, start: int, end: int, days: list[Session], symbols: Batch
    ) -> tuple[pl.DataFrame, int, None]:
        """The daily candles (``SPARK_SCHEMA``) of ``[start, end)`` of the stocks, following
        ``next_page_token``."""
        params: dict[str, str | int] = {**_span(start, end), "limit": BARS_LIMIT}
        pages, malformed = [], 0
        while True:
            await self._turn(SPARK_RATE_KEY, self._spark_limit)
            body = await self._bars_page(",".join(symbols), "1Day", params)
            frame, skipped = await to_thread.run_sync(parse_daily_bars, body, days)
            pages.append(frame)
            malformed += skipped
            token = body.get("next_page_token")
            if not token:
                return pl.concat(pages), malformed, None
            params["page_token"] = token

    async def _log_rate_limit(self, got: _Batches) -> None:
        if got.response is None:
            return
        logger.info(
            "Alpaca stats refreshed in %d requests: X-RateLimit-Limit %s, "
            "X-RateLimit-Remaining %s; our count this minute %s of %d",
            len(got.frames),
            got.response.headers.get("x-ratelimit-limit", "-"),
            got.response.headers.get("x-ratelimit-remaining", "-"),
            await self._limiter.used(RATE_KEY),
            self._limit,
        )

    async def _turn(self, key: str, limit: int) -> None:
        """Waits until the share ``key`` of this minute has room for one more request."""
        while (wait := await self._limiter.hit(key, limit)) > 0:
            await pause(wait)

    async def _fetch_calendar(self) -> tuple[bytes, int]:
        year = datetime.now(UTC).year
        response = await self._request(
            f"{self._api}/v2/calendar",
            {"start": f"{HISTORY_START}-01-01", "end": f"{year + 1}-12-31"},
        )
        try:
            days = [
                {"date": day["date"], "open": day["open"], "close": day["close"]}
                for day in json_body(response, NAME, TITLE)
            ]
        except (KeyError, TypeError) as exc:
            raise DataIntegrityError(
                f"Alpaca calendar cannot be read: {type(exc).__name__} {exc}", source=NAME
            ) from None
        blob = orjson.dumps(days)
        await to_thread.run_sync(_parse_calendar, blob)  # never cache a calendar that fails
        return blob, CALENDAR_TTL

    async def _bars(
        self, symbol: str, timeframe: Timeframe, start: int, end: int, *, background: bool = False
    ) -> pl.DataFrame:
        """Every bar of ``[start, end)`` (``parse_bars``), following ``next_page_token``. Each
        page is parsed as it arrives, so the JSON objects of only one page are held at a time.
        ``background`` pages wait for the budget (``_request``)."""
        if start >= end:
            return parse_bars([])  # the live chunk in the second its day begins
        params: dict[str, str | int] = {**_span(start, end), "limit": BARS_LIMIT}
        pages = []
        while True:
            body = await self._bars_page(
                symbol,
                _ALPACA_TIMEFRAMES[timeframe],
                params,
                background=background,
            )
            pages.append(await to_thread.run_sync(parse_bars, _symbol_bars(body, symbol)))
            token = body.get("next_page_token")
            if not token:
                return pl.concat(pages)
            params["page_token"] = token

    async def _bars_page(
        self, symbol: str, timeframe: str, params: dict[str, str | int], *, background: bool = False
    ) -> dict[str, Any]:
        response = await self._request(
            f"{self._data}/v2/stocks/bars",
            {
                **params,
                "symbols": symbol,
                "timeframe": timeframe,
                "feed": "sip",
                "adjustment": "all",
                "sort": "asc",
            },
            background=background,
        )
        body = json_body(response, NAME, TITLE)
        if not isinstance(body, dict):
            raise DataIntegrityError("Alpaca bars response is not an object.", source=NAME)
        return body

    async def _background_turn(self) -> None:
        """Waits until the background loads' share of this minute and then our whole budget
        have room for one more request, as long as that takes: the share is taken again in the
        minute the whole budget had no room."""
        while True:
            await self._turn(BACKGROUND_RATE_KEY, self._background_limit)
            wait = await self._limiter.hit(RATE_KEY, self._limit)
            if wait <= 0:
                return
            await pause(wait)

    async def _request(
        self, url: str, params: dict[str, str | int], *, background: bool = False
    ) -> httpx.Response:
        """A request within our budget (a ``background`` one waits for it, others fail when it
        is spent, ``spend``); 401/403 and other failures become ``SourceUnavailable`` (error
        bodies are not trusted to be JSON: Alpaca answers 401 in HTML)."""
        if self._headers is None:
            raise SourceUnavailable(
                NAME,
                "Stock data is not available: the Alpaca keys (ALPACA_KEY_ID, "
                "ALPACA_SECRET_KEY) are not configured on this server.",
            )
        if background:
            await self._background_turn()
        else:
            await spend(
                self._limiter,
                NAME,
                f"Alpaca request budget ({self._limit} per minute)",
                RATE_KEY,
                self._limit,
            )
        response = await get(self._http, NAME, TITLE, url, params=params, headers=self._headers)
        if 400 <= response.status_code < 500 and b"subscription" in response.content:
            # The data plan does not cover the request (recent SIP data, a feed of a paid plan):
            # a developer has to look, and asking for fewer symbols would not help.
            logger.error(
                "Alpaca refused our data plan: HTTP %d: %s",
                response.status_code,
                response.text[:200],
            )
            raise SourceUnavailable(
                NAME, "Alpaca refused this server's market data plan for the request."
            )
        if response.status_code in (401, 403):
            logger.error("Alpaca rejected our API keys: HTTP %d", response.status_code)
            raise SourceUnavailable(
                NAME, "Alpaca rejected the API keys of this server. Try again later."
            )
        if response.status_code != 200:
            logger.warning(
                "Alpaca %s answered HTTP %d: %s", url, response.status_code, response.text[:200]
            )
            raise unavailable(NAME, TITLE, response)
        return response


def _symbol_bars(body: dict[str, Any], symbol: str) -> list[Any]:
    """The symbol's bars; unknown symbols come back as ``{}`` or ``null``, not as an error."""
    by_symbol = body.get("bars") or {}
    bars = (by_symbol.get(symbol) or []) if isinstance(by_symbol, dict) else None
    if not isinstance(bars, list):
        raise DataIntegrityError("Alpaca bars response has no list of bars.", source=NAME)
    return bars


def _parse_calendar(blob: bytes) -> list[Session]:
    try:
        return parse_calendar(orjson.loads(blob))
    except DataIntegrityError as exc:
        raise DataIntegrityError(exc.detail, source=NAME) from None
