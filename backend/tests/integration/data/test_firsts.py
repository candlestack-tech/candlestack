"""The first candles of the instrument list: read from Redis for a page, the unknown ones looked
up in the background within their caps, and what happens when a lookup fails."""

import asyncio
import logging
import time

import httpx
import pytest
import respx
from redis.asyncio import Redis

import candlestack.data.firsts as firsts_module
from candlestack.accounts import User
from candlestack.accounts.dependencies import current_user
from candlestack.core import RateLimiter, Settings, create_redis, ratelimit
from candlestack.data import (
    DataService,
    InstrumentId,
    InstrumentSort,
    Market,
    SearchResult,
    build_data_service,
)
from candlestack.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The market data is for signed-in accounts only (candlestack.main); who it is does not matter.
SIGNED_IN = User(id="usr_7f3k2m9qx4")

BTC = InstrumentId.parse("crypto:BTCUSDT")
MINUTE_MS = 60_000
# The first 1m candle of each pair of the catalog; 币安人生USDT has none yet.
FIRSTS_MS = {
    "BTCUSDT": 1502942400000,  # 2017-08-17T04:00Z
    "ETHBTC": 1500004800000,  # 2017-07-14T04:00Z
    "ETHUSDT": 1502942400000,
    "SOLUSDT": 1597125600000,  # 2020-08-11T06:00Z
}


@pytest.fixture
def no_first_lookups() -> None:
    """The lookups stay on here (the conftest turns them off for the other tests)."""


@pytest.fixture(autouse=True)
def one_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every rate-limit counter of a test counts in the same minute."""
    monkeypatch.setattr(ratelimit, "_time", lambda: 1_800_000_030.0)


def first_klines(upstream, hold: asyncio.Event | None = None) -> respx.Route:
    """The first-candle lookups of every pair; with ``hold`` each waits until it is set."""

    async def answer(request: httpx.Request) -> httpx.Response:
        if hold is not None:
            await hold.wait()
        first = FIRSTS_MS.get(request.url.params["symbol"])
        rows = [] if first is None else [upstream.kline(first, MINUTE_MS)]
        return httpx.Response(200, json=rows)

    return upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines",
        params={"interval": "1m", "startTime": "0", "limit": "1"},
    ).mock(side_effect=answer)


def asked(route: respx.Route) -> list[str]:
    return [call.request.url.params["symbol"] for call in route.calls]


def firsts(found: SearchResult) -> dict[str, int | None]:
    return {item.symbol: first for item, first in zip(found.items, found.firsts, strict=True)}


async def crypto_page(service: DataService) -> SearchResult:
    """Every pair, by symbol: BTCUSDT, ETHBTC, ETHUSDT, SOLUSDT, 币安人生USDT."""
    return await service.search(None, Market.CRYPTO, sort=InstrumentSort.SYMBOL)


async def test_a_page_shows_what_is_known_and_looks_up_a_few_others(
    service: DataService, upstream, redis: Redis, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(firsts_module, "WARM_PER_PAGE", 3)
    upstream.exchange_info()
    klines = first_klines(upstream)
    await service.instrument(BTC)

    first = await crypto_page(service)
    await service._firsts.wait()
    second = await crypto_page(service)
    await service._firsts.wait()
    third = await crypto_page(service)

    assert firsts(first) == {
        "BTCUSDT": 1502942400,
        "ETHBTC": None,
        "ETHUSDT": None,
        "SOLUSDT": None,
        "币安人生USDT": None,
    }
    # The first three unknown rows of the first page (at once, so in any order), then the last one.
    calls = asked(klines)
    assert calls[0] == "BTCUSDT"
    assert sorted(calls[1:4]) == ["ETHBTC", "ETHUSDT", "SOLUSDT"]
    assert calls[4:] == ["币安人生USDT"]
    assert firsts(second) == {
        **firsts(first),
        "ETHBTC": 1500004800,
        "ETHUSDT": 1502942400,
        "SOLUSDT": 1597125600,
    }
    assert firsts(third) == firsts(second)  # a pair without a candle is not asked again
    # A known first candle is kept for 90 days, none for an hour.
    assert await redis.ttl("data:v1:first:crypto:SOLUSDT") > 89 * 86400
    assert 0 < await redis.ttl("data:v1:first:crypto:币安人生USDT") <= 3600


async def test_concurrent_pages_look_each_instrument_up_once(
    service: DataService, upstream, settings: Settings, redis: Redis, http: httpx.AsyncClient
):
    upstream.exchange_info()
    klines = first_klines(upstream)
    other_process = build_data_service(settings, redis, http)

    await asyncio.gather(crypto_page(service), crypto_page(service), crypto_page(other_process))
    await asyncio.gather(service._firsts.wait(), other_process._firsts.wait())

    assert sorted(asked(klines)) == sorted([*FIRSTS_MS, "币安人生USDT"])
    assert firsts(await crypto_page(other_process))["SOLUSDT"] == 1597125600


async def test_lookups_running_at_once_are_capped(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(firsts_module, "WARM_RUNNING_MAX", 2)
    upstream.exchange_info()
    hold = asyncio.Event()
    klines = first_klines(upstream, hold)

    await crypto_page(service)
    await crypto_page(service)  # both lookups still run: no room for more
    hold.set()
    await service._firsts.wait()

    assert sorted(asked(klines)) == ["BTCUSDT", "ETHBTC"]


async def test_lookups_per_minute_are_a_share_of_the_budget(
    service: DataService, upstream, redis: Redis, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(firsts_module, "WARM_PER_MINUTE", {Market.CRYPTO: 2, Market.STOCK: 1})
    upstream.exchange_info()
    klines = first_klines(upstream)

    for _ in range(3):
        await crypto_page(service)
        await service._firsts.wait()

    assert len(klines.calls) == 2
    assert int(await redis.get("data:rl:first-warm:crypto:30000000") or 0) > 2


async def test_a_source_that_cannot_be_asked_pauses_its_lookups(
    service: DataService,
    upstream,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(firsts_module, "WARM_PER_PAGE", 1)
    upstream.exchange_info()
    klines = upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines", params={"startTime": "0"}
    ).respond(503)

    with caplog.at_level(logging.WARNING):
        found = await crypto_page(service)
        await service._firsts.wait()
        await crypto_page(service)  # paused: nothing is looked up
        await service._firsts.wait()

    assert firsts(found)["BTCUSDT"] is None
    assert len(klines.calls) == 3  # one lookup with its retries
    assert caplog.text.count("First candle of crypto:BTCUSDT not looked up") == 1
    assert "Binance failed with HTTP 503. Try again in a minute." in caplog.text

    # A minute later Binance answers again.
    later = time.monotonic() + firsts_module.WARM_PAUSE_SECONDS + 1
    monkeypatch.setattr(firsts_module, "_monotonic", lambda: later)
    await redis.delete("data:fail:v1:first:crypto:BTCUSDT")
    klines.respond(200, json=[upstream.kline(FIRSTS_MS["BTCUSDT"], MINUTE_MS)])
    await crypto_page(service)
    await service._firsts.wait()

    assert firsts(await crypto_page(service))["BTCUSDT"] == 1502942400


@pytest.mark.parametrize(
    ("answer", "warning"),
    [
        (httpx.Response(200, content=b"not json"), "Binance sent"),
        (httpx.Response(400, json={"code": -1121}), "Binance rejected our request with HTTP 400"),
    ],
    ids=["invalid data", "refused"],
)
async def test_an_instrument_its_source_fails_on_pauses_only_its_lookups(
    service: DataService,
    upstream,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    answer: httpx.Response,
    warning: str,
):
    monkeypatch.setattr(firsts_module, "WARM_PER_PAGE", 1)
    upstream.exchange_info()
    klines = upstream.router.get(
        f"{upstream.BINANCE_API}/api/v3/klines", params={"startTime": "0"}
    ).mock(return_value=answer)

    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            await crypto_page(service)
            await service._firsts.wait()

    # The second page skipped BTCUSDT and looked up the next pair: the market goes on.
    assert asked(klines) == ["BTCUSDT", "ETHBTC"]
    assert caplog.text.count(f"First candle of crypto:BTCUSDT not looked up: {warning}") == 1
    assert caplog.text.count(f"First candle of crypto:ETHBTC not looked up: {warning}") == 1

    # An hour later BTCUSDT is asked again; ETHBTC's pause is over too and is dropped.
    later = time.monotonic() + firsts_module.INSTRUMENT_PAUSE_SECONDS + 1
    monkeypatch.setattr(firsts_module, "_monotonic", lambda: later)
    await redis.delete("data:fail:v1:first:crypto:BTCUSDT")
    await crypto_page(service)
    await service._firsts.wait()

    assert asked(klines) == ["BTCUSDT", "ETHBTC", "BTCUSDT"]
    assert list(service._firsts._instrument_paused_until) == [BTC]


async def test_an_unexpected_failure_is_for_a_developer(
    service: DataService,
    upstream,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    monkeypatch.setattr(firsts_module, "WARM_PER_PAGE", 1)
    upstream.exchange_info()

    async def broken(symbol: str) -> int | None:
        raise RuntimeError(symbol)

    monkeypatch.setattr(service._sources[Market.CRYPTO], "first_timestamp", broken)

    await crypto_page(service)
    await service._firsts.wait()

    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert [record.getMessage() for record in errors] == [
        "Looking up the first candle of crypto:BTCUSDT failed"
    ]
    assert errors[0].exc_info is not None


async def test_without_redis_nothing_is_looked_up(
    settings: Settings, http: httpx.AsyncClient, upstream
):
    dead_redis = create_redis("redis://127.0.0.1:1/0")
    service = build_data_service(settings, dead_redis, http)
    upstream.exchange_info()
    klines = first_klines(upstream)

    found = await crypto_page(service)
    await service._firsts.wait()
    await dead_redis.aclose()

    assert set(firsts(found).values()) == {None}
    assert not klines.calls


async def test_close_stops_the_lookups(service: DataService, upstream, redis: Redis):
    upstream.exchange_info()
    first_klines(upstream, asyncio.Event())  # never answers

    await crypto_page(service)
    await service.close()
    await asyncio.sleep(0)

    assert not service._firsts._running
    assert await redis.get("data:v1:first:crypto:BTCUSDT") is None


async def test_the_list_answers_with_the_first_candles(
    service: DataService, upstream, redis: Redis
):
    upstream.exchange_info()
    first_klines(upstream)
    app = create_app(Settings(app_env="test"))
    app.state.data_service = service
    app.state.rate_limiter = RateLimiter(redis)
    app.dependency_overrides[current_user] = lambda: SIGNED_IN
    transport = httpx.ASGITransport(app=app)

    params = {"q": "btcusdt", "market": "crypto"}
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        before = await client.get("/api/v1/data/instruments", params=params)
        await service._firsts.wait()
        after = await client.get("/api/v1/data/instruments", params=params)

    assert before.json()["items"][0]["available_from"] is None
    assert after.json()["items"][0]["available_from"] == 1502942400
