"""Crypto candles through DataService: archives, REST, the cache and its failures."""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
import respx
from redis.asyncio import Redis

import candlestack.data.service as service_module
import candlestack.data.sources.binance as binance_module
from candlestack.core import RateLimiter, Settings, create_redis
from candlestack.data import (
    DataIntegrityError,
    DataService,
    InstrumentId,
    InstrumentNotFound,
    InvalidRequest,
    LoadProgress,
    PeriodOutOfRange,
    SourceUnavailable,
    Timeframe,
    TooManyCandles,
    WarmUpOutOfRange,
    build_data_service,
    normalise,
    watch_load,
)
from candlestack.data.cache import encode_chunk
from candlestack.data.sources.binance import IP_WEIGHT_CEILING, parse_archive
from candlestack.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

BTC = InstrumentId.parse("crypto:BTCUSDT")
ETH = InstrumentId.parse("crypto:ETHUSDT")
LISTED_MS = 1502942400000  # the first BTCUSDT 1m candle: 2017-08-17T04:00Z
HOUR = 3600
MINUTE_MS = 60_000


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def freeze(monkeypatch: pytest.MonkeyPatch, now: int) -> None:
    monkeypatch.setattr(service_module, "_time", lambda: now)


def freeze_binance(monkeypatch: pytest.MonkeyPatch, now: float) -> None:
    """The wall clock of the Binance source, which times its REST pauses."""
    monkeypatch.setattr(
        binance_module, "time", SimpleNamespace(time=lambda: now, perf_counter=time.perf_counter)
    )


async def get_api(service: DataService, redis: Redis, path: str) -> httpx.Response:
    """``GET path`` from the API app, answered by ``service``."""
    app = create_app(Settings(app_env="test"))
    app.state.data_service = service
    app.state.rate_limiter = RateLimiter(redis)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        return await client.get(path)


async def test_closed_month_from_its_archive_then_from_the_cache(service: DataService, upstream):
    catalog = upstream.exchange_info()
    first = upstream.first_kline(LISTED_MS)
    archive, _ = upstream.fixture_archive("monthly", "BTCUSDT-1h-2024-01.zip")

    fetched: list[LoadProgress] = []
    cached: list[LoadProgress] = []

    with watch_load(fetched.append):
        candles = await service.candles(BTC, Timeframe.H1, utc("2024-01-01"), utc("2024-02-01"))
    with watch_load(cached.append):
        again = await service.candles(BTC, Timeframe.H1, utc("2024-01-10"), utc("2024-01-11"))

    assert candles.frame.height == 744
    assert candles.frame.row(0) == (
        utc("2024-01-01"),
        42283.58,
        42554.57,
        42261.02,
        42475.23,
        1271.68108,
    )
    assert (candles.gaps, candles.gaps_total) == ([], 0)
    assert (candles.source, candles.feed) == ("binance", "spot")
    assert candles.fingerprint.startswith("sha256:")
    assert again.frame["ts"].to_list() == list(range(utc("2024-01-10"), utc("2024-01-11"), HOUR))
    assert (catalog.call_count, first.call_count, archive.call_count) == (1, 1, 1)
    # A watched load reports its chunks, and which came from the cache.
    assert fetched == [
        LoadProgress(total=1),
        LoadProgress(total=1, loaded=1, cached=0, until=utc("2024-02-01")),
    ]
    assert cached[-1] == LoadProgress(total=1, loaded=1, cached=1, until=utc("2024-02-01"))


async def test_30m_candles_open_on_the_utc_half_hours(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    name = "BTCUSDT-30m-2024-01.zip"
    half_hour = 1800
    opens = [(utc("2024-01-01") + k * half_hour) * 1000 for k in range(48 * 31)]
    upstream.archive("monthly", name, upstream.make_archive(name, opens, half_hour * 1000))

    candles = await service.candles(BTC, Timeframe.M30, utc("2024-01-10"), utc("2024-01-11"))

    assert candles.frame["ts"].to_list() == list(
        range(utc("2024-01-10"), utc("2024-01-11"), half_hour)
    )
    assert (candles.gaps, candles.gaps_total) == ([], 0)
    assert (candles.source, candles.feed) == ("binance", "spot")


async def test_last_candles_before_end(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    upstream.fixture_archive("monthly", "BTCUSDT-1h-2024-01.zip")
    end = utc("2024-01-15T10:00")

    last = await service.last_candles(BTC, Timeframe.H1, end, 30)
    same = await service.candles(BTC, Timeframe.H1, utc("2024-01-14T04:00"), end)

    assert last.start == utc("2024-01-14T04:00")
    assert last.frame["ts"].to_list() == list(range(utc("2024-01-14T04:00"), end, HOUR))
    assert last.fingerprint == same.fingerprint
    assert (last.gaps, last.gaps_total) == ([], 0)


async def test_last_candles_up_to_now_are_closed_ones(
    settings: Settings, redis, http: httpx.AsyncClient, upstream, monkeypatch: pytest.MonkeyPatch
):
    # A limit equal to the maximum works though the open candle makes the period one longer.
    service = build_data_service(settings.model_copy(update={"candles_max": 4}), redis, http)
    now = utc("2025-02-03T02:30")
    freeze(monkeypatch, now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    upstream.fixture_archive("monthly", "BTCUSDT-1h-2025-01.zip")
    name = "BTCUSDT-1h-2025-02-01.zip"
    opens = [(utc("2025-02-01") + hour * HOUR) * 10**6 for hour in range(24)]
    upstream.archive("daily", name, upstream.make_archive(name, opens, HOUR * 10**6))
    upstream.archive("daily", "BTCUSDT-1h-2025-02-02.zip", None)
    upstream.klines(
        utc("2025-02-02") * 1000,
        [upstream.kline((utc("2025-02-02") + hour * HOUR) * 1000) for hour in range(24)],
    )
    upstream.klines(
        utc("2025-02-03") * 1000,
        [upstream.kline((utc("2025-02-03") + hour * HOUR) * 1000) for hour in range(3)],
    )

    candles = await service.last_candles(BTC, Timeframe.H1, now, 4)

    # The 02:00 candle is still open at 02:30.
    assert candles.frame["ts"].to_list() == [utc("2025-02-02T22:00") + i * HOUR for i in range(4)]
    assert candles.start == utc("2025-02-02T22:00")


async def test_last_candles_count_what_the_live_chunk_has(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    first_now, now = utc("2025-02-03T02:30"), utc("2025-02-03T03:10")
    freeze(monkeypatch, first_now)
    freeze_binance(monkeypatch, first_now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    upstream.fixture_archive("monthly", "BTCUSDT-1h-2025-01.zip")
    name = "BTCUSDT-1h-2025-02-01.zip"
    opens = [(utc("2025-02-01") + hour * HOUR) * 10**6 for hour in range(24)]
    upstream.archive("daily", name, upstream.make_archive(name, opens, HOUR * 10**6))
    upstream.archive("daily", "BTCUSDT-1h-2025-02-02.zip", None)
    upstream.klines(
        utc("2025-02-02") * 1000,
        [upstream.kline((utc("2025-02-02") + hour * HOUR) * 1000) for hour in range(24)],
    )
    upstream.klines(
        utc("2025-02-03") * 1000,
        [upstream.kline((utc("2025-02-03") + hour * HOUR) * 1000) for hour in range(3)],
    )
    await service.last_candles(BTC, Timeframe.H1, first_now, 4)  # caches the live chunk
    freeze(monkeypatch, now)  # the 02:00 candle has closed since; the chunk does not have it

    candles = await service.last_candles(BTC, Timeframe.H1, now, 4)

    assert candles.frame["ts"].to_list() == [utc("2025-02-02T22:00") + i * HOUR for i in range(4)]
    assert candles.start == utc("2025-02-02T22:00")


async def test_last_candles_after_the_live_chunk_is_fetched_again(
    service: DataService, upstream, redis, monkeypatch: pytest.MonkeyPatch
):
    first_now, now = utc("2025-02-03T02:30"), utc("2025-02-03T03:10")
    freeze(monkeypatch, first_now)
    freeze_binance(monkeypatch, first_now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    upstream.fixture_archive("monthly", "BTCUSDT-1h-2025-01.zip")
    name = "BTCUSDT-1h-2025-02-01.zip"
    opens = [(utc("2025-02-01") + hour * HOUR) * 10**6 for hour in range(24)]
    upstream.archive("daily", name, upstream.make_archive(name, opens, HOUR * 10**6))
    upstream.archive("daily", "BTCUSDT-1h-2025-02-02.zip", None)
    upstream.klines(
        utc("2025-02-02") * 1000,
        [upstream.kline((utc("2025-02-02") + hour * HOUR) * 1000) for hour in range(24)],
    )
    upstream.klines(
        utc("2025-02-03") * 1000,
        [upstream.kline((utc("2025-02-03") + hour * HOUR) * 1000) for hour in range(3)],
    )
    await service.last_candles(BTC, Timeframe.H1, first_now, 4)
    await redis.flushdb()  # the chunk expired: it is fetched again, now with the 02:00 candle
    freeze(monkeypatch, now)
    freeze_binance(monkeypatch, now)

    candles = await service.last_candles(BTC, Timeframe.H1, now, 4)

    assert candles.frame["ts"].to_list() == [utc("2025-02-02T23:00") + i * HOUR for i in range(4)]


async def test_last_candles_never_ask_for_more_than_counted(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    monkeypatch.setattr(service_module, "first_of_last", lambda *_, **__: utc("2024-01-01"))

    with pytest.raises(RuntimeError, match="holds 336"):
        await service.last_candles(BTC, Timeframe.H1, utc("2024-01-15"), 3)


async def test_last_candles_over_the_available_history(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    name = "BTCUSDT-1h-2017-08.zip"
    opens = [(utc("2017-08-17T04:00") + hour * HOUR) * 1000 for hour in range(14 * 24 + 20)]
    upstream.archive("monthly", name, upstream.make_archive(name, opens, HOUR * 1000))

    candles = await service.last_candles(BTC, Timeframe.H1, utc("2017-08-19"), 500)

    assert candles.start == utc("2017-08-17T04:00")  # the first candle there is
    assert candles.frame.height == 44


async def test_last_candles_checks(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)

    with pytest.raises(PeriodOutOfRange, match="Set end after 1502942400") as early:
        await service.last_candles(BTC, Timeframe.H1, utc("2017-08-17T04:00"), 10)
    with pytest.raises(PeriodOutOfRange, match="Set end to") as late:
        await service.last_candles(BTC, Timeframe.H1, utc("2100-01-01"), 10)
    with pytest.raises(TooManyCandles, match="Use a limit of 50000 or less") as many:
        await service.last_candles(BTC, Timeframe.H1, utc("2024-01-01"), 50001)
    # The checks keep their order: the instrument, the period, then the limit.
    with pytest.raises(InstrumentNotFound):
        await service.last_candles(InstrumentId.parse("crypto:NOPEUSDT"), Timeframe.H1, 3600, 10**6)
    with pytest.raises(PeriodOutOfRange):
        await service.last_candles(BTC, Timeframe.H1, utc("2100-01-01"), 10**6)

    assert early.value.available_from == utc("2017-08-17T04:00")
    assert "Set start" not in early.value.detail
    assert late.value.available_to <= utc("2100-01-01")
    assert (many.value.requested, many.value.maximum) == (50001, 50000)


async def test_concurrent_requests_fetch_each_value_once(
    service: DataService, upstream, settings: Settings, redis, http: httpx.AsyncClient
):
    other_process = build_data_service(settings, redis, http)
    catalog = upstream.exchange_info()
    first = upstream.first_kline(LISTED_MS)
    archive, _ = upstream.fixture_archive("monthly", "BTCUSDT-1h-2024-01.zip")

    results = await asyncio.gather(
        *(
            each.candles(BTC, Timeframe.H1, utc("2024-01-01"), utc("2024-02-01"))
            for each in [service, other_process] * 3
        )
    )

    assert {result.fingerprint for result in results} == {results[0].fingerprint}
    assert (catalog.call_count, first.call_count, archive.call_count) == (1, 1, 1)


async def test_month_boundary_and_the_current_month(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    now = utc("2025-02-03T02:30")
    freeze(monkeypatch, now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    upstream.fixture_archive("monthly", "BTCUSDT-1h-2025-01.zip")  # microsecond times
    name = "BTCUSDT-1h-2025-02-01.zip"
    opens = [(utc("2025-02-01") + hour * HOUR) * 10**6 for hour in range(24)]
    upstream.archive("daily", name, upstream.make_archive(name, opens, HOUR * 10**6))
    upstream.archive("daily", "BTCUSDT-1h-2025-02-02.zip", None)  # not published yet
    yesterday = upstream.klines(
        utc("2025-02-02") * 1000,
        [upstream.kline((utc("2025-02-02") + hour * HOUR) * 1000) for hour in range(24)],
    )
    today = upstream.klines(  # the 02:00 candle is still open at 02:30
        utc("2025-02-03") * 1000,
        [upstream.kline((utc("2025-02-03") + hour * HOUR) * 1000) for hour in range(3)],
    )

    candles = await service.candles(BTC, Timeframe.H1, utc("2025-01-31T20:00"), now)

    assert candles.frame["ts"].to_list() == list(
        range(utc("2025-01-31T20:00"), utc("2025-02-03T02:00"), HOUR)
    )
    assert candles.gaps_total == 0
    assert yesterday.calls.last.request.url.params["endTime"] == str(utc("2025-02-03") * 1000 - 1)
    assert today.calls.last.request.url.params["endTime"] == str(now * 1000 - 1)


async def test_month_without_its_archive_is_built_from_daily_ones(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    freeze(monkeypatch, utc("2024-04-02T12:00"))  # the monthly archive of March is not out yet
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    monthly, _ = upstream.archive("monthly", "BTCUSDT-1h-2024-03.zip", None)
    daily = []
    for day in range(1, 32):
        name = f"BTCUSDT-1h-2024-03-{day:02}.zip"
        if day == 10:
            daily.append(upstream.fixture_archive("daily", name))
        else:
            opens = [(utc(f"2024-03-{day:02}") + hour * HOUR) * 1000 for hour in range(24)]
            daily.append(
                upstream.archive("daily", name, upstream.make_archive(name, opens, HOUR * 1000))
            )

    candles = await service.candles(BTC, Timeframe.H1, utc("2024-03-09"), utc("2024-03-11"))

    assert candles.frame["ts"].to_list() == list(range(utc("2024-03-09"), utc("2024-03-11"), HOUR))
    assert candles.frame.row(24) == (
        utc("2024-03-10"),
        68313.28,
        68449.18,
        68200.0,
        68343.12,
        1024.01076,
    )
    assert monthly.call_count == 1
    assert all(zip_route.call_count == 1 for zip_route, _ in daily)


async def test_archive_candles_off_the_grid_are_taken_from_rest(service: DataService, upstream):
    # The 2018-02 archive has 1h candles at hh:28:14 from 02-09T09:28:14 to 02-11T03:28:14;
    # REST has that span on the grid, from 02-09T10:00.
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    archive, _ = upstream.fixture_archive("monthly", "BTCUSDT-1h-2018-02.zip")
    rest = upstream.klines(
        utc("2018-02-09T09:00") * 1000,
        json.loads(
            upstream.fixture_bytes("binance/rest/klines-BTCUSDT-1h-2018-02-09-maintenance.json")
        ),
    )

    candles = await service.candles(BTC, Timeframe.H1, utc("2018-02-07"), utc("2018-02-11"))
    again = await service.candles(BTC, Timeframe.H1, utc("2018-02-09"), utc("2018-02-10"))

    opens = candles.frame["ts"].to_list()
    assert [t for t in opens if t % HOUR] == []
    assert (len(opens), candles.gaps_total) == (63, 33)  # 96 bins, the maintenance is the gap
    assert candles.gaps == [(utc("2018-02-08T01:00"), utc("2018-02-09T10:00"))]
    assert candles.frame.row(opens.index(utc("2018-02-09T10:00"))) == (
        utc("2018-02-09T10:00"),
        7789.9,
        8390.0,
        7789.9,
        8269.84,
        2131.59828,
    )
    assert again.frame.height == 14
    assert rest.calls.last.request.url.params["endTime"] == str(utc("2018-02-11T04:00") * 1000 - 1)
    assert (archive.call_count, rest.call_count) == (1, 1)  # the repaired month is cached


async def test_chunks_cached_before_the_off_grid_repair_are_not_read(
    service: DataService, upstream, redis
):
    # Such chunks hold the archive's candles as delivered, off the grid; they are under
    # data:v1:candles and expire there, the repaired ones are under data:v2:candles.
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    name = "BTCUSDT-1h-2018-02.zip"
    archive, _ = upstream.fixture_archive("monthly", name)
    upstream.klines(
        utc("2018-02-09T09:00") * 1000,
        json.loads(
            upstream.fixture_bytes("binance/rest/klines-BTCUSDT-1h-2018-02-09-maintenance.json")
        ),
    )
    content = upstream.fixture_bytes(f"binance/archive/monthly/{name}")
    stale = normalise(parse_archive(content, name))
    await redis.set(
        "data:v1:candles:crypto:BTCUSDT:1h:2018-02", encode_chunk(stale, utc("2018-03-01"))
    )

    candles = await service.candles(BTC, Timeframe.H1, utc("2018-02-09"), utc("2018-02-12"))

    assert [t for t in candles.frame["ts"].to_list() if t % HOUR] == []
    assert archive.call_count == 1
    assert await redis.exists("data:v2:candles:crypto:BTCUSDT:1h:2018-02") == 1


async def test_archive_with_a_wrong_checksum_is_never_cached(service: DataService, upstream, redis):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    name = "BTCUSDT-1h-2024-01.zip"
    archive, _ = upstream.archive(
        "monthly",
        name,
        upstream.fixture_bytes(f"binance/archive/monthly/{name}"),
        upstream.checksum(b"something else", name),
    )
    failure = "data:fail:v2:candles:crypto:BTCUSDT:1h:2024-01"

    for attempt in range(3):
        if attempt == 2:
            await redis.delete(failure)  # as if the kept error had expired
        with pytest.raises(DataIntegrityError, match="does not match its published") as info:
            await service.candles(BTC, Timeframe.H1, utc("2024-01-01"), utc("2024-02-01"))
        assert info.value.source == "binance"
        assert 0 < await redis.ttl(failure) <= 10

    assert archive.call_count == 2  # the second attempt got the kept error
    assert await redis.exists("data:v2:candles:crypto:BTCUSDT:1h:2024-01") == 0


async def test_a_failed_fetch_ends_every_request_waiting_for_it(
    service: DataService, upstream, redis
):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    calls = 0

    async def forbidden(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.3)  # the other requests wait for the lock meanwhile
        return httpx.Response(403)

    upstream.router.get(url__startswith=upstream.BINANCE_DATA).mock(side_effect=forbidden)
    await service.instrument(BTC)  # the catalog and the first candle

    results = await asyncio.gather(
        *(
            service.candles(BTC, Timeframe.H1, utc("2024-01-01"), utc("2024-02-01"))
            for _ in range(4)
        ),
        return_exceptions=True,
    )

    assert all(isinstance(result, SourceUnavailable) for result in results)
    assert {result.detail for result in results if isinstance(result, SourceUnavailable)} == {
        "Binance archive rejected our request with HTTP 403. Try again later."
    }
    assert calls == 2  # the archive and its checksum, once: not once per waiting request


async def test_rate_limit_of_binance_pauses_rest_calls(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    now = utc("2025-02-03T02:30")
    freeze(monkeypatch, now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    today = upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines",
        params={"startTime": str(utc("2025-02-03") * 1000)},
    ).respond(429, headers={"Retry-After": "30"})

    for _ in range(2):
        with pytest.raises(SourceUnavailable) as info:
            await service.candles(BTC, Timeframe.H1, utc("2025-02-03"), now)
        assert info.value.retry_after in (29, 30)
        assert info.value.source == "binance"

    assert today.call_count == 1  # the second request did not reach Binance


@pytest.mark.parametrize(
    ("error", "detail", "calls"),
    [
        (
            httpx.ReadTimeout("The read operation timed out"),
            "Binance did not answer in time. Try again in a minute.",
            1,
        ),
        (
            httpx.ConnectError("[Errno 111] Connection refused"),
            "Binance could not be reached (ConnectError). Try again in a minute.",
            3,  # with its two retries
        ),
    ],
    ids=["timeout", "connection-error"],
)
async def test_binance_not_answering_is_unavailable_and_remembered(
    service: DataService,
    upstream,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    error: httpx.HTTPError,
    detail: str,
    calls: int,
):
    now = utc("2025-02-03T02:30")
    freeze(monkeypatch, now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    today = upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines",
        params={"startTime": str(utc("2025-02-03") * 1000)},
    ).mock(side_effect=error)
    chunk = "candles:crypto:BTCUSDT:1h:2025-02-03-live"

    with pytest.raises(SourceUnavailable) as info:
        await service.candles(BTC, Timeframe.H1, utc("2025-02-03"), now)
    response = await get_api(
        service,
        redis,
        "/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=1h"
        f"&start={utc('2025-02-03')}&end={now}",
    )

    assert (info.value.source, info.value.detail, info.value.retry_after) == (
        "binance",
        detail,
        None,
    )
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert "retry-after" not in response.headers
    assert response.json() == {
        "type": "https://candlestack.tech/problems/source-unavailable",
        "title": "Data source unavailable",
        "status": 503,
        "detail": detail,
        "source": "binance",
    }
    assert today.call_count == calls  # the second request got the kept error
    assert 0 < await redis.ttl(f"data:fail:v2:{chunk}") <= 10
    assert await redis.exists(f"data:v2:{chunk}") == 0


async def test_live_day_of_1m_candles_comes_in_two_rest_pages(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    now = utc("2025-02-03T20:00")
    freeze(monkeypatch, now)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    opens = [utc("2025-02-03") * 1000 + minute * MINUTE_MS for minute in range(1200)]
    rows = [upstream.kline(opens_at, MINUTE_MS) for opens_at in opens]
    first = upstream.klines(opens[0], rows[:1000])  # a full page: there may be more
    second = upstream.klines(opens[999] + 1, rows[1000:])  # from after the last open time

    candles = await service.candles(BTC, Timeframe.M1, utc("2025-02-03"), now)

    assert candles.frame["ts"].to_list() == list(range(utc("2025-02-03"), now, 60))
    assert (candles.gaps, candles.gaps_total) == ([], 0)
    assert (first.call_count, second.call_count) == (1, 1)
    for page in (first, second):
        params = page.calls.last.request.url.params
        assert (params["interval"], params["limit"], params["endTime"]) == (
            "1m",
            "1000",
            str(now * 1000 - 1),
        )


@pytest.mark.parametrize("status", [418, 429], ids=["ip-banned", "rate-limited"])
async def test_ban_or_rate_limit_of_binance_pauses_every_rest_call(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch, status: int
):
    now = utc("2025-02-03T02:30")
    freeze(monkeypatch, now)
    upstream.exchange_info()
    first = upstream.first_kline(LISTED_MS)
    today = upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines",
        params={"startTime": str(utc("2025-02-03") * 1000)},
    ).respond(status, headers={"Retry-After": "120"})

    with pytest.raises(SourceUnavailable) as limited:
        await service.candles(BTC, Timeframe.H1, utc("2025-02-03"), now)
    # Nothing of ETHUSDT is cached: its first-candle lookup is the next REST call.
    with pytest.raises(SourceUnavailable) as paused:
        await service.candles(ETH, Timeframe.H1, utc("2025-02-03"), now)

    assert (limited.value.retry_after, limited.value.detail) == (
        120,
        "Binance is rate limiting our requests. Try again in 120 seconds.",
    )
    assert paused.value.source == "binance"
    assert paused.value.retry_after is not None
    assert abs(paused.value.retry_after - 120) <= 1
    assert paused.value.detail == (
        "Binance's limit for our server is reached. "
        f"Try again in {paused.value.retry_after} seconds."
    )
    assert (today.call_count, first.call_count) == (1, 1)  # ETHUSDT's lookup was not sent


def first_klines(upstream, used_weight: str) -> tuple[respx.Route, respx.Route]:
    """The first-candle lookups of BTCUSDT, whose answer reports ``used_weight`` of our IP
    this minute, and of ETHUSDT."""
    klines = f"{upstream.BINANCE_API}/api/v3/klines"
    btc = upstream.router.get(klines, params={"symbol": "BTCUSDT", "startTime": "0"}).respond(
        200,
        json=[upstream.kline(LISTED_MS, MINUTE_MS)],
        headers={"X-MBX-USED-WEIGHT-1M": used_weight},
    )
    eth = upstream.router.get(klines, params={"symbol": "ETHUSDT", "startTime": "0"}).respond(
        200, json=[upstream.kline(LISTED_MS, MINUTE_MS)]
    )
    return btc, eth


async def test_weight_of_our_ip_at_the_ceiling_pauses_rest_calls(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    freeze_binance(monkeypatch, 1_800_000_030.25)  # 30 s into a minute
    upstream.exchange_info()
    _, eth = first_klines(upstream, str(IP_WEIGHT_CEILING))

    await service.instrument(BTC)
    with pytest.raises(SourceUnavailable) as paused:
        await service.candles(ETH, Timeframe.H1, utc("2024-01-01"), utc("2024-01-02"))

    assert paused.value.detail.startswith("Binance's limit for our server is reached.")
    assert paused.value.retry_after is not None
    assert 1 <= paused.value.retry_after <= 61  # until the minute is over
    assert eth.call_count == 0


@pytest.mark.parametrize("used_weight", [str(IP_WEIGHT_CEILING - 1), "", "n/a"])
async def test_weight_below_the_ceiling_or_unreadable_does_not_pause(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch, used_weight: str
):
    freeze_binance(monkeypatch, 1_800_000_030.25)
    upstream.exchange_info()
    _, eth = first_klines(upstream, used_weight)

    await service.instrument(BTC)
    info = await service.instrument(ETH)

    assert info.available_from == LISTED_MS // 1000
    assert eth.call_count == 1


async def test_instrument_detail_when_the_first_candle_cannot_be_asked(
    service: DataService,
    upstream,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    freeze(monkeypatch, utc("2026-09-26T12:34:56"))
    upstream.exchange_info()
    first = upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines", params={"startTime": "0"}
    ).respond(503)

    with caplog.at_level(logging.WARNING):
        response = await get_api(service, redis, "/api/v1/data/instruments/crypto:BTCUSDT")

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["available_from"], body["available_to"]) == (
        "crypto:BTCUSDT",
        None,
        utc("2026-09-26T12:33"),
    )
    assert (
        "First candle of crypto:BTCUSDT unknown: Binance failed with HTTP 503. "
        "Try again in a minute." in caplog.text
    )
    assert first.call_count == 3  # one lookup with its retries


async def test_works_without_redis(
    settings: Settings, http: httpx.AsyncClient, upstream, caplog: pytest.LogCaptureFixture
):
    dead_redis = create_redis("redis://127.0.0.1:1/0")
    service = build_data_service(settings, dead_redis, http)
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    archive, _ = upstream.fixture_archive("monthly", "BTCUSDT-1h-2024-01.zip")

    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            candles = await service.candles(BTC, Timeframe.H1, utc("2024-01-01"), utc("2024-02-01"))
            assert candles.frame.height == 744
    await dead_redis.aclose()

    assert archive.call_count == 2  # nothing was cached
    assert "working without the cache" in caplog.text


async def test_checks_before_any_candle_is_fetched(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)

    with pytest.raises(InstrumentNotFound):
        await service.candles(InstrumentId.parse("crypto:NOPEUSDT"), Timeframe.H1, 0, 3600)
    with pytest.raises(PeriodOutOfRange, match="from 2017-08-17T04:00:00Z") as early:
        await service.candles(BTC, Timeframe.H1, utc("2017-08-17T03:00"), utc("2017-08-18"))
    with pytest.raises(PeriodOutOfRange, match="Set end to") as late:
        await service.candles(BTC, Timeframe.H1, utc("2024-01-01"), utc("2100-01-01"))
    with pytest.raises(TooManyCandles, match="Use 5m or a larger timeframe") as many:
        await service.candles(BTC, Timeframe.M1, utc("2024-01-01"), utc("2024-04-01"))

    assert early.value.available_from == utc("2017-08-17T04:00")
    assert late.value.available_to <= utc("2100-01-01")
    assert (many.value.requested, many.value.maximum) == (131040, 50000)
    # The first 1d candle opens at 00:00, before the first trade.
    with pytest.raises(PeriodOutOfRange) as daily:
        await service.candles(BTC, Timeframe.D1, utc("2017-08-16"), utc("2017-08-18"))
    assert daily.value.available_from == utc("2017-08-17")


async def test_instrument_info(service: DataService, upstream, monkeypatch: pytest.MonkeyPatch):
    freeze(monkeypatch, utc("2026-09-26T12:34:56"))
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)

    info = await service.instrument(BTC)

    assert str(info.instrument.id) == "crypto:BTCUSDT"
    assert info.timeframes == tuple(Timeframe)
    assert (info.available_from, info.available_to) == (
        utc("2017-08-17T04:00"),
        utc("2026-09-26T12:33"),
    )
    assert info.max_candles == 50000


# --- The start of a load with a model's warm-up ---------------------------------------------


async def test_the_warm_up_starts_its_candles_before_the_period(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    start = utc("2024-01-15T10:00")

    with_warm_up = await service.warm_up_start(BTC, Timeframe.H1, start, start + 10 * HOUR, 24)
    without = await service.warm_up_start(BTC, Timeframe.H1, start, start + 10 * HOUR, 0)

    assert with_warm_up == start - 24 * HOUR
    assert without == start


async def test_the_warm_up_start_checks_the_period_and_the_warm_up(service: DataService, upstream):
    upstream.exchange_info()
    upstream.first_kline(LISTED_MS)
    listed = utc("2017-08-17T04:00")
    start = utc("2018-01-01")

    with pytest.raises(WarmUpOutOfRange, match="Set start to 2017-08-18T04:00:00Z") as early:
        await service.warm_up_start(BTC, Timeframe.H1, listed + 3 * HOUR, listed + 30 * HOUR, 24)
    with pytest.raises(TooManyCandles) as many:
        await service.warm_up_start(BTC, Timeframe.H1, start, start + 49_990 * HOUR, 24)
    with pytest.raises(PeriodOutOfRange, match="from 2017-08-17T04:00:00Z"):
        await service.warm_up_start(BTC, Timeframe.H1, listed - HOUR, listed + HOUR, 0)
    with pytest.raises(PeriodOutOfRange, match="Set end to"):
        await service.warm_up_start(BTC, Timeframe.H1, start, utc("2100-01-01"), 24)
    with pytest.raises(InvalidRequest, match="must be before end"):
        await service.warm_up_start(BTC, Timeframe.H1, start, start, 24)

    assert early.value.earliest_start == listed + 24 * HOUR
    assert (early.value.candles, early.value.timeframe) == (24, "1h")
    # The warm-up counts against the maximum.
    assert many.value.requested == 50_014
