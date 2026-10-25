"""Stock candles through DataService: sessions, 1m bars, 1Day bars and Alpaca's failures."""

import json
import logging
from datetime import UTC, datetime

import httpx
import polars as pl
import pytest
from redis.asyncio import Redis

import candlestack.core.ratelimit as ratelimit_module
from candlestack.core import RateLimiter, Settings
from candlestack.data import (
    DataIntegrityError,
    DataService,
    InstrumentId,
    InstrumentNotFound,
    Market,
    SourceUnavailable,
    Timeframe,
    build_data_service,
)
from candlestack.data import service as service_module
from candlestack.data.sources import alpaca as alpaca_module
from candlestack.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

AAPL = InstrumentId.parse("stock:AAPL")
HOUR = 3600
# 2024-06-03 is a normal day (13:30-20:00 UTC); 2024-11-29 closes early (14:30-18:00 UTC).
JUNE_3 = ("2024-06-03T13:30", "2024-06-03T20:00")
NOVEMBER_29 = ("2024-11-29T14:30", "2024-11-29T18:00")


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def period(times: tuple[str, str]) -> tuple[int, int]:
    return utc(times[0]), utc(times[1])


async def get_api(service: DataService, redis: Redis, path: str) -> httpx.Response:
    """``GET path`` from the API app, answered by ``service``."""
    app = create_app(Settings(app_env="test"))
    app.state.data_service = service
    app.state.rate_limiter = RateLimiter(redis)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as client:
        return await client.get(path)


@pytest.fixture
def stocks(upstream) -> None:
    """The Alpaca catalog, the 2024 calendar and the first bar of AAPL."""
    upstream.assets()
    upstream.calendar()
    upstream.first_bar()


@pytest.mark.usefixtures("stocks")
async def test_normal_day_from_1m_bars(service: DataService, upstream):
    june = upstream.bars(
        "1Min",
        "2024-06-01T00:00:00Z",
        "bars-AAPL-1Min-2024-06-03-p0.json",
        "bars-AAPL-1Min-2024-06-03-p1.json",
    )

    minutes = await service.candles(AAPL, Timeframe.M1, *period(JUNE_3))
    hours = await service.candles(AAPL, Timeframe.H1, *period(JUNE_3))
    halves = await service.candles(AAPL, Timeframe.H4, *period(JUNE_3))

    assert minutes.frame.height == 320
    assert minutes.gaps_total == 70  # the recording (IEX) has no trade in 70 of the 390
    assert (minutes.source, minutes.feed) == ("alpaca", "sip")
    assert hours.frame["ts"].to_list() == [utc("2024-06-03T13:30") + i * HOUR for i in range(7)]
    assert hours.gaps_total == 0
    assert hours.frame["open"][0] == 191.16  # the first minute's open
    assert hours.frame["close"][-1] == 192.16  # 15:59's close, the last candle is 30 minutes
    assert halves.frame["ts"].to_list() == [utc("2024-06-03T13:30"), utc("2024-06-03T17:30")]
    morning = minutes.frame.filter(pl.col("ts") < utc("2024-06-03T17:30"))
    assert halves.frame.row(0)[1:] == (
        morning["open"][0],
        morning["high"].max(),
        morning["low"].min(),
        morning["close"][-1],
        pytest.approx(morning["volume"].sum()),
    )
    assert june.call_count == 2  # two pages, then the month comes from the cache
    first, second = (call.request.url.params for call in june.calls)
    assert (first["end"], first["feed"], first["adjustment"], first["limit"]) == (
        "2024-06-30T23:59:59Z",
        "sip",
        "all",
        "10000",
    )
    assert second["page_token"] == "QUFQTHxNfDE3MTc0NDM2MDAwMDAwMDAwMDA="


@pytest.mark.usefixtures("stocks")
@pytest.mark.parametrize(
    ("month", "pages", "day", "count"),
    [
        (
            "2024-06-01",
            ["bars-AAPL-1Min-2024-06-03-p0.json", "bars-AAPL-1Min-2024-06-03-p1.json"],
            JUNE_3,
            13,
        ),
        ("2024-11-01", ["bars-AAPL-1Min-2024-11-29.json"], NOVEMBER_29, 7),  # early close
    ],
)
async def test_30m_candles_are_two_15m_candles(
    service: DataService, upstream, month: str, pages: list[str], day, count: int
):
    upstream.bars("1Min", f"{month}T00:00:00Z", *pages)

    halves = await service.candles(AAPL, Timeframe.M30, *period(day))
    quarters = await service.candles(AAPL, Timeframe.M15, *period(day))

    assert halves.frame.height == count
    assert halves.frame["ts"][0] == period(day)[0]
    for half in halves.frame.iter_rows(named=True):
        pair = quarters.frame.filter(pl.col("ts").is_between(half["ts"], half["ts"] + 1799))
        assert pair.height == 2
        assert half["open"] == pair["open"][0]
        assert half["high"] == pair["high"].max()
        assert half["low"] == pair["low"].min()
        assert half["close"] == pair["close"][-1]
        assert half["volume"] == pytest.approx(pair["volume"].sum())


@pytest.mark.usefixtures("stocks")
async def test_early_close_day(service: DataService, upstream):
    upstream.bars("1Min", "2024-11-01T00:00:00Z", "bars-AAPL-1Min-2024-11-29.json")
    upstream.bars_json(
        "1Day",
        "2024-01-01T00:00:00Z",
        upstream.aapl_bars("bars-AAPL-1Day-2024-06.json", "bars-AAPL-1Day-2024-11-25_12-02.json"),
    )

    hours = await service.candles(AAPL, Timeframe.H1, *period(NOVEMBER_29))
    halves = await service.candles(AAPL, Timeframe.H4, *period(NOVEMBER_29))
    days = await service.candles(AAPL, Timeframe.D1, utc("2024-11-25"), utc("2024-12-03"))

    assert hours.frame["ts"].to_list() == [utc("2024-11-29T14:30") + i * HOUR for i in range(4)]
    assert hours.gaps_total == 0
    assert halves.frame["ts"].to_list() == [utc("2024-11-29T14:30")]  # 09:30-13:00 only
    assert days.frame["ts"].to_list() == [
        utc("2024-11-25T14:30"),
        utc("2024-11-26T14:30"),
        utc("2024-11-27T14:30"),
        utc("2024-11-29T14:30"),  # Thanksgiving has no session
        utc("2024-12-02T14:30"),
    ]
    assert days.gaps_total == 0
    assert days.frame.row(3)[1:] == (233.11, 236.09, 232.28, 235.68, 549571.0)


async def test_sip_history_starts_in_2016(service: DataService, upstream):
    upstream.assets()
    calendar = [
        *json.loads(upstream.fixture_bytes("alpaca/calendar-2016-01.json")),
        *json.loads(upstream.fixture_bytes("alpaca/calendar-2024.json")),
    ]
    upstream.router.get(f"{upstream.ALPACA_API}/v2/calendar").respond(200, json=calendar)
    first = upstream.first_bar()
    month = upstream.bars("1Min", "2016-01-01T00:00:00Z", "bars-AAPL-1Min-2016-01-04.json")
    upstream.bars_json(
        "1Day", "2016-01-01T00:00:00Z", upstream.aapl_bars("bars-AAPL-1Day-earliest.json")
    )
    session = (utc("2016-01-04T14:30"), utc("2016-01-04T21:00"))

    info = await service.instrument(AAPL)
    minutes = await service.candles(AAPL, Timeframe.M1, *session)
    hours = await service.candles(AAPL, Timeframe.H1, *session)
    days = await service.candles(AAPL, Timeframe.D1, session[0], utc("2016-01-05"))

    assert info.available_from == utc("2016-01-04T14:30")  # 09:30 EST
    assert first.calls[0].request.url.params["feed"] == "sip"
    # The pre-market and after-hours bars SIP has are dropped.
    assert minutes.frame["ts"].to_list() == [
        utc("2016-01-04T14:30"),
        utc("2016-01-04T14:31"),
        utc("2016-01-04T14:32"),
        utc("2016-01-04T15:30"),
        utc("2016-01-04T20:59"),
    ]
    assert minutes.gaps_total == 390 - 5
    assert hours.frame["ts"].to_list() == [
        utc("2016-01-04T14:30"),
        utc("2016-01-04T15:30"),
        utc("2016-01-04T20:30"),
    ]
    assert hours.frame.row(0)[1:] == (23.65, 23.74, 23.51, 23.58, 3127000 + 1504000 + 1210500)
    # 1d is the 1Day bar: its volume counts extended hours too.
    assert days.frame.rows() == [(utc("2016-01-04T14:30"), 23.65, 24.29, 23.51, 24.28, 270597548)]
    assert month.call_count == 1


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The service's and Alpaca's wall clock, moved by the tests."""
    now = [0.0]
    monkeypatch.setattr(service_module, "_time", lambda: now[0])
    monkeypatch.setattr(alpaca_module, "_time", lambda: now[0])
    return now


@pytest.mark.usefixtures("stocks")
async def test_the_latest_candles_come_15_minutes_late(
    service: DataService, upstream, redis: Redis, clock: list[float]
):
    pages = ["bars-AAPL-1Min-2024-06-03-p0.json", "bars-AAPL-1Min-2024-06-03-p1.json"]
    today = upstream.bars("1Min", "2024-06-03T00:00:00Z", *pages, *pages)
    clock[0] = utc("2024-06-03T15:00")  # 11:00 New York

    before = await service.candles(AAPL, Timeframe.M1, utc("2024-06-03T13:30"), int(clock[0]))
    # 16 minutes later, once the live chunk expired (60 s).
    clock[0] += 16 * 60
    await redis.delete("data:v3:candles:stock:AAPL:1m:2024-06-03-live")
    after = await service.candles(AAPL, Timeframe.M1, utc("2024-06-03T13:30"), int(clock[0]))

    assert [call.request.url.params["end"] for call in today.calls] == [
        "2024-06-03T14:44:59Z",
        "2024-06-03T14:44:59Z",  # the second page
        "2024-06-03T15:00:59Z",
        "2024-06-03T15:00:59Z",
    ]
    # Candles of the last 15 minutes are neither returned nor gaps yet.
    assert before.frame["ts"][-1] == utc("2024-06-03T14:44")
    assert all(end <= utc("2024-06-03T14:45") for _, end in before.gaps)
    assert after.frame["ts"][-1] == utc("2024-06-03T15:00")
    assert after.frame.height > before.frame.height


@pytest.mark.usefixtures("stocks")
async def test_a_daily_chunk_is_fetched_again_once_extended_hours_ended(
    service: DataService, upstream, redis: Redis, clock: list[float]
):
    upstream.bars_json(
        "1Day", "2024-01-01T00:00:00Z", upstream.aapl_bars("bars-AAPL-1Day-2024-06.json")
    )
    clock[0] = utc("2024-06-04T00:20")  # 20:20 New York: the 1Day bar of 06-03 is complete

    days = await service.candles(AAPL, Timeframe.D1, utc("2024-06-03"), utc("2024-06-04"))

    assert days.frame["ts"].to_list() == [utc("2024-06-03T13:30")]
    # Kept until 01:15 UTC, when the extended hours of a winter day would have ended too.
    ttl = await redis.ttl("data:v3:candles:stock:AAPL:1d:2024-01-01..2024-06-04")
    assert 3200 < ttl <= 3300


@pytest.mark.usefixtures("stocks")
async def test_last_candles_skip_weekends_and_holidays(
    service: DataService, upstream, redis: Redis
):
    upstream.bars_json(
        "1Day",
        "2024-01-01T00:00:00Z",
        upstream.aapl_bars("bars-AAPL-1Day-2024-11-25_12-02.json"),
    )

    candles = await service.last_candles(AAPL, Timeframe.D1, utc("2024-12-03"), 3)
    response = await get_api(
        service,
        redis,
        "/api/v1/data/instruments/stock:AAPL/candles?timeframe=1d&end=2024-12-03&limit=3",
    )

    # Wednesday, Friday (Thanksgiving, Thursday, has no session), then Monday.
    assert candles.frame["ts"].to_list() == [
        utc("2024-11-27T14:30"),
        utc("2024-11-29T14:30"),
        utc("2024-12-02T14:30"),
    ]
    assert candles.start == utc("2024-11-27T14:30")
    assert candles.gaps_total == 0
    assert response.status_code == 200
    assert response.json()["meta"]["start"] == utc("2024-11-27T14:30")
    assert response.json()["meta"]["count"] == 3


@pytest.mark.usefixtures("stocks")
async def test_last_candles_over_the_available_history(service: DataService, upstream):
    upstream.bars_json(
        "1Day",
        "2024-01-01T00:00:00Z",
        upstream.aapl_bars("bars-AAPL-1Day-2024-11-25_12-02.json"),
    )

    candles = await service.last_candles(AAPL, Timeframe.D1, utc("2024-12-03"), 5000)
    info = await service.instrument(AAPL)

    assert info.available_from is not None
    assert candles.start == info.available_from
    assert candles.frame["ts"][-1] == utc("2024-12-02T14:30")


@pytest.mark.usefixtures("stocks")
async def test_last_candles_of_a_stock_without_any_bar_yet(service: DataService, upstream):
    spy = InstrumentId.parse("stock:SPY")
    upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars", params={"symbols": "SPY", "limit": "1"}
    ).respond(200, content=upstream.fixture_bytes("alpaca/error-unknown-symbol-multi.json"))

    candles = await service.last_candles(spy, Timeframe.D1, utc("2024-06-05"), 10)

    # No candle to count from: an empty period that starts where it ends.
    assert candles.frame.height == 0
    assert (candles.start, candles.end) == (utc("2024-06-05"), utc("2024-06-05"))
    assert (candles.gaps, candles.gaps_total) == ([], 0)


@pytest.mark.usefixtures("stocks")
async def test_unknown_stock_is_not_asked_for_bars(service: DataService, upstream):
    with pytest.raises(InstrumentNotFound):
        await service.candles(
            InstrumentId.parse("stock:NOTAREALSYM"),
            Timeframe.D1,
            utc("2024-06-03"),
            utc("2024-06-04"),
        )

    assert all(call.request.url.host != "data.alpaca.test" for call in upstream.router.calls)


@pytest.mark.usefixtures("stocks")
async def test_stock_without_any_bar_yet(service: DataService, upstream):
    spy = InstrumentId.parse("stock:SPY")
    first = upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars", params={"symbols": "SPY", "limit": "1"}
    ).respond(200, content=upstream.fixture_bytes("alpaca/error-unknown-symbol-multi.json"))

    info = await service.instrument(spy)
    candles = await service.candles(spy, Timeframe.D1, utc("2024-06-03"), utc("2024-06-05"))

    assert info.available_from is None
    assert candles.frame.height == 0
    assert (candles.gaps, candles.gaps_total) == (
        [(utc("2024-06-03T13:30"), utc("2024-06-04T20:00"))],
        2,
    )
    assert first.call_count == 1  # "no bar yet" is remembered for an hour


@pytest.mark.usefixtures("stocks")
async def test_rate_limited_request_is_retried(service: DataService, upstream, sleeps: list[float]):
    november = upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars",
        params={"timeframe": "1Min", "start": "2024-11-01T00:00:00Z"},
    ).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(
                200, content=upstream.fixture_bytes("alpaca/bars-AAPL-1Min-2024-11-29.json")
            ),
        ]
    )

    hours = await service.candles(AAPL, Timeframe.H1, *period(NOVEMBER_29))

    assert hours.frame.height == 4
    assert november.call_count == 2
    assert sleeps == [0.5]


@pytest.mark.usefixtures("stocks")
async def test_our_request_budget(
    settings: Settings, redis, http: httpx.AsyncClient, upstream, monkeypatch: pytest.MonkeyPatch
):
    service = build_data_service(settings.model_copy(update={"alpaca_rate_limit": 3}), redis, http)
    monkeypatch.setattr(ratelimit_module, "_time", lambda: 1_800_000_001.0)  # 59 s to go
    november = upstream.bars("1Min", "2024-11-01T00:00:00Z", "bars-AAPL-1Min-2024-11-29.json")

    # Assets, calendar and the first bar use the budget of 3; the bars would be the 4th.
    with pytest.raises(SourceUnavailable) as info:
        await service.candles(AAPL, Timeframe.H1, *period(NOVEMBER_29))

    assert info.value.detail == (
        "Our Alpaca request budget (3 per minute) is spent. Try again in 59 seconds."
    )
    assert info.value.retry_after == 59
    assert november.call_count == 0


@pytest.mark.usefixtures("stocks")
@pytest.mark.parametrize(
    ("error", "detail", "calls"),
    [
        (
            httpx.ReadTimeout("The read operation timed out"),
            "Alpaca did not answer in time. Try again in a minute.",
            1,
        ),
        (
            httpx.ConnectError("[Errno 111] Connection refused"),
            "Alpaca could not be reached (ConnectError). Try again in a minute.",
            3,  # with its two retries
        ),
    ],
    ids=["timeout", "connection-error"],
)
async def test_alpaca_not_answering_is_unavailable_and_remembered(
    service: DataService,
    upstream,
    redis: Redis,
    error: httpx.HTTPError,
    detail: str,
    calls: int,
):
    november = upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars",
        params={"timeframe": "1Min", "start": "2024-11-01T00:00:00Z"},
    ).mock(side_effect=error)
    start, end = period(NOVEMBER_29)
    chunk = "candles:stock:AAPL:1m:2024-11"

    with pytest.raises(SourceUnavailable) as info:
        await service.candles(AAPL, Timeframe.H1, start, end)
    response = await get_api(
        service,
        redis,
        f"/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&start={start}&end={end}",
    )

    assert (info.value.source, info.value.detail, info.value.retry_after) == (
        "alpaca",
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
        "source": "alpaca",
    }
    assert november.call_count == calls  # the second request got the kept error
    assert 0 < await redis.ttl(f"data:fail:v3:{chunk}") <= 10
    assert await redis.exists(f"data:v3:{chunk}") == 0


@pytest.mark.usefixtures("stocks")
async def test_answer_that_is_not_json_is_invalid_data(
    service: DataService, upstream, redis: Redis, caplog: pytest.LogCaptureFixture
):
    # A 200 with a page instead of JSON, as a proxy in front of Alpaca might send.
    november = upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars",
        params={"timeframe": "1Min", "start": "2024-11-01T00:00:00Z"},
    ).respond(
        200,
        content=b"<html><body><h1>Service Unavailable</h1></body></html>",
        headers={"Content-Type": "text/html"},
    )
    start, end = period(NOVEMBER_29)

    with pytest.raises(DataIntegrityError) as info:
        await service.candles(AAPL, Timeframe.H1, start, end)
    with caplog.at_level(logging.ERROR):
        response = await get_api(
            service,
            redis,
            f"/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&start={start}&end={end}",
        )

    assert info.value.source == "alpaca"
    assert info.value.detail.startswith("Alpaca sent a response that is not valid JSON")
    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://candlestack.tech/problems/source-data-invalid",
        "title": "Invalid data from the source",
        "status": 502,
        "detail": "Alpaca sent data for this request that failed validation, so it was not "
        "used. Try again later, or ask for another period.",
        "source": "alpaca",
    }
    assert "Invalid data from alpaca: Alpaca sent a response that is not valid JSON" in (
        caplog.text
    )
    assert november.call_count == 1  # the second request got the kept error
    assert await redis.exists("data:v3:candles:stock:AAPL:1m:2024-11") == 0


async def test_rejected_keys(service: DataService, upstream):
    upstream.router.get(f"{upstream.ALPACA_API}/v2/assets").respond(
        401,
        content=upstream.fixture_bytes("alpaca/error-no-auth.html"),
        headers={"Content-Type": "text/html"},
    )

    with pytest.raises(SourceUnavailable, match="Alpaca rejected the API keys of this server"):
        await service.search("apple", Market.STOCK)


async def test_without_keys_stocks_are_unavailable(redis, http: httpx.AsyncClient, upstream):
    service = build_data_service(Settings(app_env="test"), redis, http)

    with pytest.raises(SourceUnavailable, match=r"Alpaca keys .* are not configured"):
        await service.instrument(AAPL)

    assert not upstream.router.calls


@pytest.mark.usefixtures("stocks")
async def test_the_warm_up_counts_session_candles_only(service: DataService):
    monday = utc("2024-12-02T14:30")

    first = await service.warm_up_start(AAPL, Timeframe.D1, monday, utc("2024-12-04"), 2)

    # Friday, then Wednesday: Thanksgiving and the weekend have no session.
    assert first == utc("2024-11-27T14:30")
