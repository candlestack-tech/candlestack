"""``DataService``: instruments and candles of both markets, the entry point of the module.

A candles request is checked (catalog, period, candle count), its period is split into the
source's chunks (closed months or years, closed days, today), every chunk comes from Redis or
the source (single-flight, a few at once), and the chunks are joined, resampled for stocks,
sliced to ``[start, end)``, checked for gaps and fingerprinted in a worker thread.

The instrument list is sorted by the 24 h stats that ``keep_stats_fresh`` refreshes in the
background (``StatsStore``), and its items carry mini charts of the last daily candles, which
the same task refreshes once a day (``SparkStore``), and their first candles as far as Redis
knows them (``Firsts``); a search only reads them.
"""

import asyncio
import bisect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from functools import partial
from itertools import batched

import httpx
import orjson
import polars as pl
from anyio import to_thread
from redis.asyncio import Redis

from candlestack.core import RateLimiter, Settings
from candlestack.data.cache import Cache, decode_chunk, decode_fetched_at, encode_chunk
from candlestack.data.candles import (
    candle_count,
    closed_only,
    expected_bins_fixed,
    expected_bins_sessions,
    find_gaps,
    fingerprint,
    first_of_last,
    latest_end,
    resample_sessions,
    session_last,
    slice_range,
    suggest,
)
from candlestack.data.catalog import normalise_query
from candlestack.data.catalog_store import CatalogStore
from candlestack.data.errors import (
    DataIntegrityError,
    InstrumentNotFound,
    InvalidRequest,
    PeriodOutOfRange,
    SourceUnavailable,
    TooManyCandles,
    WarmUpOutOfRange,
)
from candlestack.data.firsts import Firsts
from candlestack.data.icons import FALLBACK_AFTER_FAILURE, Icon, Icons
from candlestack.data.models import (
    CandleSet,
    Exchange,
    Instrument,
    InstrumentId,
    InstrumentInfo,
    InstrumentSort,
    Market,
    SearchResult,
    SourceHealth,
    SourceStats,
    Timeframe,
    empty_candles,
)
from candlestack.data.sessions import Session, sessions_between
from candlestack.data.sources.alpaca import AlpacaSource
from candlestack.data.sources.base import DAY, DataSource, Period, pause
from candlestack.data.sources.binance import BinanceSource
from candlestack.data.spark_store import SETTLE_SECONDS, SparkStore
from candlestack.data.stats_store import StatsStore

logger = logging.getLogger(__name__)

# Chunks decoded and resampled at once: a year of monthly chunks of 1m bars is about
# 100000 rows (5 MB). Fewer at once use less memory, but every resample call costs time.
RESAMPLE_CHUNKS = 12
HEALTH_TTL = 60
# How often the background task looks for stats that are due (StatsStore.REFRESH_SECONDS).
STATS_CHECK_SECONDS = 60

# Patched by tests to move the clock.
_time = time.time


@dataclass(frozen=True, slots=True)
class CandleRequest:
    """A checked candles request: ``[start, end)`` of one instrument in one timeframe."""

    instrument: InstrumentId
    timeframe: Timeframe
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class _Lookup:
    """What a candles request needs to know before it is checked."""

    instrument: Instrument
    source: DataSource
    timeframe: Timeframe
    now: int
    sessions: list[Session] | None
    available_from: int | None

    def out_of_range(self, *, start: int | None, end: int) -> PeriodOutOfRange:
        return PeriodOutOfRange(
            self.instrument.id,
            self.timeframe,
            self.available_from,
            self.now,
            start=start,
            end=end,
        )


class DataService:
    def __init__(  # noqa: PLR0913 (keyword-only: its parts, each built once)
        self,
        *,
        sources: Mapping[Market, DataSource],
        cache: Cache,
        catalogs: CatalogStore,
        icons: Icons,
        candles_max: int,
        stats: StatsStore,
        sparks: SparkStore,
        firsts: Firsts,
    ) -> None:
        self._sources = sources
        self._cache = cache
        self._catalogs = catalogs
        self._icons = icons
        self._candles_max = candles_max
        self._stats = stats
        self._sparks = sparks
        self._firsts = firsts

    async def search(
        self,
        q: str | None,
        market: Market | None = None,
        limit: int = 20,
        offset: int = 0,
        *,
        exchange: Exchange | None = None,
        sort: InstrumentSort = InstrumentSort.VOLUME,
    ) -> SearchResult:
        """``limit`` instruments from ``offset`` on that match ``q``, best first (ranking in
        ``candlestack.data.catalog``, ties in ``sort``), or of every instrument in ``sort``
        without ``q``, in the markets whose catalog can be loaded; ``unavailable`` names the
        others. Raises the source's error when none can be loaded. ``exchange`` keeps only the
        instruments that trade there. The stats and the mini charts are what the background
        refresh last stored: instruments without stats sort last. The first candles are those
        Redis knows; a few unknown ones are looked up in the background (``Firsts.known``)."""
        if q is not None and not normalise_query(q):
            return SearchResult([], [], 0, [], [], [])
        if exchange is not None:
            # An exchange lists one market only: the other market's catalog is not needed.
            if market not in (None, exchange.market):
                return SearchResult([], [], 0, [], [], [])
            market = exchange.market
        catalog = await self._catalogs.catalog(market)
        # Taken with the catalog, before anything else is awaited: a market that finishes loading
        # meanwhile is not in this catalog, so it must still count as unavailable.
        unavailable = self._catalogs.missing(market)
        stats = await self._stats.table()
        # Sorting about 15k instruments takes a few ms: in a thread, off the event loop.
        found = await to_thread.run_sync(
            partial(
                catalog.page, q, market, limit, offset, exchange=exchange, sort=sort, stats=stats
            )
        )
        sparks = await self._sparks.sparks(found.items)
        firsts = await self._firsts.known(found.items)
        return SearchResult(found.items, unavailable, found.total, found.stats, sparks, firsts)

    async def close(self) -> None:
        """Stops what requests left running in the background (on shutdown)."""
        await self._firsts.close()

    async def keep_stats_fresh(self) -> None:
        """Refreshes the stats and the daily candles of every market when they are due, each
        checking once a minute on its own (the daily candles take minutes and must not hold up
        the stats), until cancelled (the app's lifespan runs it in prod and stage,
        ``STATS_REFRESH``)."""
        async with asyncio.TaskGroup() as group:
            group.create_task(_every(STATS_CHECK_SECONDS, self.refresh_stats))
            group.create_task(_every(STATS_CHECK_SECONDS, self.refresh_daily_candles))

    async def refresh_stats(self) -> None:
        """One round: each market whose stats are due is fetched from its source for the
        instruments of its catalog (``StatsStore.refresh``), all markets at once, so crypto does
        not wait for the paced stock requests. A market whose refresh fails unexpectedly is
        logged for a developer, and the others still run."""
        await asyncio.gather(*(self._refresh_market(market) for market in self._sources))

    async def _refresh_market(self, market: Market) -> None:
        try:
            await self._stats.refresh(market, partial(self._fetch_stats, market))
        except Exception:
            logger.exception("Refreshing the %s stats failed", market)

    async def _fetch_stats(self, market: Market) -> SourceStats:
        catalog = await self._catalogs.catalog(market)
        return await self._sources[market].stats(catalog.instruments(market))

    async def refresh_daily_candles(self) -> None:
        """One round: each market whose daily candles are due (after its daily close) is fetched
        from its source for the instruments of its catalog (``SparkStore.refresh``), all markets
        at once. A market whose refresh fails unexpectedly is logged for a developer, and the
        others still run."""
        await asyncio.gather(*(self._refresh_daily(market) for market in self._sources))

    async def _refresh_daily(self, market: Market) -> None:
        try:
            source = self._sources[market]
            sessions = await source.sessions()
            closed_at = daily_close(int(_time()) - SETTLE_SECONDS, sessions)
            if closed_at is None:
                return
            fetch = partial(self._fetch_daily, market, closed_at)
            await self._sparks.refresh(market, fetch, closed_at, next_close(closed_at, sessions))
        except Exception:
            logger.exception("Refreshing the %s daily candles failed", market)

    async def _fetch_daily(
        self, market: Market, closed_at: int, only: frozenset[str] | None
    ) -> SourceStats:
        catalog = await self._catalogs.catalog(market)
        instruments = catalog.instruments(market)
        if only is not None:
            instruments = [i for i in instruments if i.symbol in only]
        return await self._sources[market].daily_candles(instruments, closed_at)

    async def instrument(self, instrument_id: InstrumentId) -> InstrumentInfo:
        """The instrument and its available period. ``available_from`` is ``None`` when the
        source cannot be asked right now."""
        instrument = await self._find(instrument_id)
        source = self._sources[instrument_id.market]
        sessions = await source.sessions()
        try:
            first = await self._firsts.first(instrument)
        except SourceUnavailable as exc:
            logger.warning("First candle of %s unknown: %s", instrument_id, exc.detail)
            first = None
        return InstrumentInfo(
            instrument=instrument,
            timeframes=tuple(Timeframe),
            available_from=first,
            available_to=newest_minute(int(_time()), sessions),
            max_candles=self._candles_max,
        )

    async def icon(self, instrument_id: InstrumentId) -> Icon:
        """The instrument's logo, or the fallback image. Raises ``InstrumentNotFound`` only:
        when the catalog cannot be loaded, the fallback stands in, as an image must."""
        try:
            instrument = await self._find(instrument_id)
        except (SourceUnavailable, DataIntegrityError) as exc:
            logger.warning("Icon of %s without the catalog: %s", instrument_id, exc.detail)
            return FALLBACK_AFTER_FAILURE
        return await self._icons.icon(instrument)

    async def candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, start: int, end: int
    ) -> CandleSet:
        """Closed candles of ``[start, end)`` with their gaps and fingerprint.

        Raises ``InvalidRequest``, ``InstrumentNotFound``, ``PeriodOutOfRange``,
        ``TooManyCandles``, ``SourceUnavailable`` and ``DataIntegrityError``.
        """
        if start >= end:
            raise InvalidRequest(f"start ({start}) must be before end ({end}).")
        lookup = await self._lookup(instrument_id, timeframe)
        if end > lookup.now or (
            lookup.available_from is not None and start < lookup.available_from
        ):
            raise lookup.out_of_range(start=start, end=end)
        requested = candle_count(start, end, timeframe, lookup.sessions)
        if requested > self._candles_max:
            hint = suggest(start, end, timeframe, self._candles_max, lookup.sessions)
            raise TooManyCandles(requested, self._candles_max, hint)
        return await self._load(lookup, start, end)

    async def warm_up_start(
        self, instrument_id: InstrumentId, timeframe: Timeframe, start: int, end: int, candles: int
    ) -> int:
        """Where a load of ``[start, end)`` with ``candles`` candles before ``start`` (a model's
        warm-up) begins: the open of the earliest of them, ``start`` without any. Checks the
        period as ``candles`` does, with the warm-up counted against the maximum, so ``candles``
        of ``[the returned start, end)`` then loads them.

        Raises ``InvalidRequest``, ``InstrumentNotFound``, ``PeriodOutOfRange``,
        ``WarmUpOutOfRange`` when the data has fewer candles before ``start``,
        ``TooManyCandles`` and ``SourceUnavailable``.
        """
        if start >= end:
            raise InvalidRequest(f"start ({start}) must be before end ({end}).")
        lookup = await self._lookup(instrument_id, timeframe)
        if end > lookup.now or (
            lookup.available_from is not None and start < lookup.available_from
        ):
            raise lookup.out_of_range(start=start, end=end)
        first = start
        if candles > 0:
            earliest = first_of_last(
                start, lookup.now, timeframe, candles, lookup.sessions, lower=lookup.available_from
            )
            if (
                earliest is None
                or candle_count(earliest, start, timeframe, lookup.sessions) < candles
            ):
                raise WarmUpOutOfRange(
                    instrument_id, timeframe, candles, self._after_warm_up(lookup, candles)
                )
            first = earliest
        requested = candle_count(first, end, timeframe, lookup.sessions)
        if requested > self._candles_max:
            hint = suggest(first, end, timeframe, self._candles_max, lookup.sessions)
            raise TooManyCandles(requested, self._candles_max, hint)
        return first

    @staticmethod
    def _after_warm_up(lookup: _Lookup, candles: int) -> int | None:
        """The first start with ``candles`` candles before it: the open of the candle after the
        first ``candles`` ones; None when the first candle is not known."""
        if lookup.available_from is None:
            return None
        return latest_end(
            lookup.available_from, lookup.now, lookup.timeframe, candles, lookup.sessions
        )

    async def last_candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, end: int, limit: int
    ) -> CandleSet:
        """The last ``limit`` closed candles that open before ``end``, like ``candles`` from
        the open of the earliest of them. Stocks count only session candles, so nights,
        weekends and holidays are skipped. When the data starts later the set starts at the
        first candle there is; when no candle opens before ``end`` it is empty and starts at
        ``end``.

        Raises ``InstrumentNotFound``, ``PeriodOutOfRange`` when ``end`` is in the future or
        not after the first candle, ``TooManyCandles`` when ``limit`` is above the maximum, and
        the other errors of ``candles``.
        """
        lookup = await self._lookup(instrument_id, timeframe)
        if end > lookup.now or (lookup.available_from is not None and end <= lookup.available_from):
            raise lookup.out_of_range(start=None, end=end)
        if limit > self._candles_max:
            raise TooManyCandles(
                limit, self._candles_max, f"Use a limit of {self._candles_max} or less."
            )
        if lookup.available_from is None:  # no candle yet: an empty period
            return await self._load(lookup, end, end)
        # Candles that closed after the live chunk was fetched are not in it, so they are not
        # counted: the window is counted up to the chunk's fetch time, as ``assemble`` does.
        known = await self._live_chunk(lookup, end)
        as_of = min([lookup.now, *(decode_fetched_at(blob) for blob in known.values())])
        return await self._load(lookup, self._window(lookup, end, limit, as_of), end, known)

    async def _live_chunk(self, lookup: _Lookup, end: int) -> dict[str, bytes]:
        """The live chunk (today up to now) by its label when the period before ``end`` ends in
        it, else nothing."""
        source, base = lookup.source, lookup.source.base_timeframe(lookup.timeframe)
        live = [p for p in source.periods(base, end - 1, end, lookup.now) if p.kind == "live"]
        blobs = await self._chunks(source, lookup.instrument, base, live)
        return {period.label: blob for period, blob in zip(live, blobs, strict=True)}

    def _window(self, lookup: _Lookup, end: int, count: int, as_of: int) -> int:
        """Start of the last ``count`` candles before ``end`` that closed by ``as_of``; ``end``
        when there are none."""
        start = first_of_last(
            end, as_of, lookup.timeframe, count, lookup.sessions, lower=lookup.available_from
        )
        if start is None:
            return end
        # The period holds the counted candles and at most one that is still open; the size
        # check of ``candles`` does not apply, so a window of another size is a bug.
        requested = candle_count(start, min(end, as_of), lookup.timeframe, lookup.sessions)
        if requested > count + 1:
            msg = f"The window of the last {count} candles holds {requested} (start {start})"
            raise RuntimeError(msg)
        return start

    async def _lookup(self, instrument_id: InstrumentId, timeframe: Timeframe) -> _Lookup:
        instrument = await self._find(instrument_id)
        source = self._sources[instrument_id.market]
        now = int(_time())
        sessions = await source.sessions()
        first = await self._firsts.first(instrument)
        return _Lookup(
            instrument,
            source,
            timeframe,
            now,
            sessions,
            None if first is None else bin_open(first, timeframe, sessions),
        )

    async def _load(
        self, lookup: _Lookup, start: int, end: int, known: Mapping[str, bytes] | None = None
    ) -> CandleSet:
        """The candles of a checked period ``[start, end)`` (its size is checked by the caller);
        ``known`` holds chunks already read, by label."""
        source, timeframe, sessions = lookup.source, lookup.timeframe, lookup.sessions
        base = source.base_timeframe(timeframe)
        periods = (
            [] if lookup.available_from is None else source.periods(base, start, end, lookup.now)
        )
        blobs = await self._chunks(source, lookup.instrument, base, periods, known or {})
        return await to_thread.run_sync(
            partial(
                assemble,
                source,
                CandleRequest(lookup.instrument.id, timeframe, start, end),
                list(zip(periods, blobs, strict=True)),
                base=base,
                now=lookup.now,
                sessions=sessions,
            )
        )

    async def sources_health(self) -> dict[str, SourceHealth]:
        """Reachability of every source, each checked at most once a minute."""
        sources = list(self._sources.values())
        results = await asyncio.gather(*(self._health(source) for source in sources))
        return {source.name: health for source, health in zip(sources, results, strict=True)}

    async def _find(self, instrument_id: InstrumentId) -> Instrument:
        catalog = await self._catalogs.catalog(instrument_id.market)
        instrument = catalog.get(instrument_id)
        if instrument is None:
            raise InstrumentNotFound(instrument_id)
        return instrument

    async def _chunks(
        self,
        source: DataSource,
        instrument: Instrument,
        timeframe: Timeframe,
        periods: Sequence[Period],
        known: Mapping[str, bytes] = {},
    ) -> list[bytes]:
        limit = asyncio.Semaphore(source.chunk_concurrency)

        async def fetch(period: Period) -> tuple[bytes, int]:
            frame = await source.fetch_period(instrument.symbol, timeframe, period)
            blob = await to_thread.run_sync(encode_chunk, frame, period.end)
            return blob, period.ttl

        async def chunk(period: Period) -> bytes:
            if period.label in known:
                return known[period.label]
            async with limit:
                name = f"candles:{instrument.id}:{timeframe}:{period.label}"
                return await self._cache.get_or_fetch(name, partial(fetch, period))

        return await asyncio.gather(*(chunk(period) for period in periods))

    async def _health(self, source: DataSource) -> SourceHealth:
        async def fetch() -> tuple[bytes, int]:
            return orjson.dumps(asdict(await source.health())), HEALTH_TTL

        blob = await self._cache.get_or_fetch(f"health:{source.name}", fetch)
        return SourceHealth(**orjson.loads(blob))


def build_data_service(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> DataService:
    """The service with both sources; no I/O happens until it is used."""
    limiter = RateLimiter(redis)
    cache = Cache(redis)
    sources: dict[Market, DataSource] = {
        Market.CRYPTO: BinanceSource(settings, http, limiter),
        Market.STOCK: AlpacaSource(settings, http, limiter, cache),
    }
    return DataService(
        sources=sources,
        cache=cache,
        catalogs=CatalogStore(cache, sources),
        icons=Icons(settings, http, limiter, cache),
        candles_max=settings.candles_max,
        stats=StatsStore(cache, sources),
        sparks=SparkStore(cache, sources),
        firsts=Firsts(cache, sources, limiter),
    )


async def _every(seconds: float, job: Callable[[], Awaitable[None]]) -> None:
    """Runs ``job`` and waits ``seconds`` after it, until cancelled."""
    while True:
        await job()
        await pause(seconds)


def daily_close(now: int, sessions: Sequence[Session] | None) -> int | None:
    """When the newest daily candle closed by ``now`` closed: the UTC midnight for crypto, the
    close of the latest session that ended for stocks (``None`` before the first)."""
    if sessions is None:
        return now // DAY * DAY
    latest = bisect.bisect_right(sessions, now, key=lambda session: session.close) - 1
    return sessions[latest].close if latest >= 0 else None


# Past the end of the calendar the next stock session is taken to close this long after the last.
_NEXT_CLOSE_FALLBACK = 4 * DAY


def next_close(closed_at: int, sessions: Sequence[Session] | None) -> int:
    """When the daily candle after the one that closed at ``closed_at`` closes: a day later for
    crypto, with the next session for stocks (after weekends and holidays)."""
    if sessions is None:
        return closed_at + DAY
    following = bisect.bisect_right(sessions, closed_at, key=lambda session: session.close)
    if following < len(sessions):
        return sessions[following].close
    return closed_at + _NEXT_CLOSE_FALLBACK


def newest_minute(now: int, sessions: Sequence[Session] | None) -> int:
    """Open time of the newest 1m bin closed at ``now``; for stocks within the latest session
    that has begun."""
    minute = now // 60 * 60
    if sessions:
        latest = bisect.bisect_right(sessions, now - 60, key=lambda session: session.open) - 1
        if latest >= 0:
            minute = min(minute, sessions[latest].close)
    return minute - 60


def bin_open(ts: int, timeframe: Timeframe, sessions: Sequence[Session] | None) -> int:
    """Open time of the bin of ``timeframe`` that holds the moment ``ts``."""
    step = timeframe.seconds
    if sessions is None:
        return ts // step * step
    index = bisect.bisect_right(sessions, ts, key=lambda session: session.open) - 1
    if index < 0 or ts >= sessions[index].close:
        return ts
    opens = sessions[index].open
    return opens + (ts - opens) // step * step


def assemble(
    source: DataSource,
    request: CandleRequest,
    chunks: Sequence[tuple[Period, bytes]],
    *,
    base: Timeframe,
    now: int,
    sessions: list[Session] | None,
) -> CandleSet:
    """The candle set from cached chunks of the base timeframe (CPU work, run in a thread).

    The chunks are decoded and resampled ``RESAMPLE_CHUNKS`` at a time, so a request for years
    of stock candles never holds all their 1m bars at once; a chunk holds whole sessions (its
    bounds are UTC midnights, which no session spans). Candles count as known up to when the
    live chunk was fetched: a candle that closed after that is neither returned nor a gap (it
    comes with the next fetch).
    """
    timeframe, start, end = request.timeframe, request.start, request.end
    frames, as_of = [], now
    for group in batched(chunks, RESAMPLE_CHUNKS, strict=False):
        parts = []
        for period, blob in group:
            frame, complete_until = decode_chunk(blob)
            parts.append(frame)
            if period.kind == "live":
                as_of = min(as_of, complete_until)
        frame = pl.concat(parts)
        if sessions is not None and timeframe is not base:
            within = sessions_between(sessions, group[0][0].start, group[-1][0].end)
            frame = resample_sessions(frame, timeframe, within)
        frames.append(slice_range(frame, start, end))
    frame = pl.concat(frames) if frames else empty_candles()
    frame = closed_only(frame, timeframe, as_of, sessions)
    if sessions is None:
        expected = expected_bins_fixed(start, end, timeframe, now=as_of)
    else:
        expected = expected_bins_sessions(start, end, timeframe, sessions, now=as_of)
    gaps = find_gaps(frame["ts"], expected, timeframe, sessions)
    return CandleSet(
        instrument=request.instrument,
        timeframe=timeframe,
        start=start,
        end=end,
        source=source.name,
        feed=source.feed,
        frame=frame,
        gaps=gaps.ranges,
        gaps_total=gaps.total,
        fingerprint=fingerprint(
            source.name, source.feed, request.instrument, timeframe, start, end, frame
        ),
        session_last=None if sessions is None else session_last(frame, timeframe, sessions),
    )
