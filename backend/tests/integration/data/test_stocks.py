"""Stock candles through DataService: sessions, 1m bars, 1Day bars and Alpaca's failures."""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
import polars as pl
import pytest
import respx
from redis.asyncio import Redis

import candlestack.core.ratelimit as ratelimit_module
from candlestack.accounts import User
from candlestack.accounts.dependencies import current_user
from candlestack.core import RateLimiter, Settings
from candlestack.data import (
    DataIntegrityError,
    DataService,
    InstrumentId,
    InstrumentNotFound,
    LoadProgress,
    Market,
    SourceUnavailable,
    Timeframe,
    build_data_service,
    watch_load,
)
from candlestack.data import progress as progress_module
from candlestack.data import service as service_module
from candlestack.data.sources import alpaca as alpaca_module
from candlestack.data.sources import base as base_module
from candlestack.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

# The market data is for signed-in accounts only (candlestack.main); who it is does not matter.
SIGNED_IN = User(id="usr_7f3k2m9qx4")

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
    app.dependency_overrides[current_user] = lambda: SIGNED_IN
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
async def test_normal_day_from_1m_and_30m_bars(service: DataService, upstream):
    june = upstream.bars(
        "1Min",
        "2024-06-01T00:00:00Z",
        "bars-AAPL-1Min-2024-06-03-p0.json",
        "bars-AAPL-1Min-2024-06-03-p1.json",
    )
    # 1h and 4h are built from 30Min bars, a month of them in one chunk.
    halves_month = upstream.native_bars(
        "30Min",
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
    assert halves_month.call_count == 1  # 1h and 4h share the chunk
    assert halves_month.calls[0].request.url.params["end"] == "2024-06-30T23:59:59Z"


@pytest.mark.usefixtures("stocks")
@pytest.mark.parametrize(
    ("pages", "month", "day", "count"),
    [
        (
            ["bars-AAPL-1Min-2024-06-03-p0.json", "bars-AAPL-1Min-2024-06-03-p1.json"],
            "2024-06-01T00:00:00Z",
            JUNE_3,
            13,
        ),
        # early close
        (["bars-AAPL-1Min-2024-11-29.json"], "2024-11-01T00:00:00Z", NOVEMBER_29, 7),
    ],
)
async def test_30m_candles_are_two_15m_candles(
    service: DataService, upstream, pages: list[str], month: str, day, count: int
):
    halves_bars = upstream.native_bars("30Min", month, *pages)
    quarters_bars = upstream.native_bars("15Min", month, *pages)

    halves = await service.candles(AAPL, Timeframe.M30, *period(day))
    quarters = await service.candles(AAPL, Timeframe.M15, *period(day))

    # Each from Alpaca's bars of its own timeframe, a month at a time.
    assert (halves_bars.call_count, quarters_bars.call_count) == (1, 1)

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
async def test_5m_candles_come_from_a_month_of_5min_bars(service: DataService, upstream):
    june = upstream.native_bars(
        "5Min",
        "2024-06-01T00:00:00Z",
        "bars-AAPL-1Min-2024-06-03-p0.json",
        "bars-AAPL-1Min-2024-06-03-p1.json",
    )

    fives = await service.candles(AAPL, Timeframe.M5, *period(JUNE_3))

    assert fives.frame["ts"][0] == utc("2024-06-03T13:30")
    assert fives.frame.height + fives.gaps_total == 78
    assert june.call_count == 1
    assert june.calls[0].request.url.params["end"] == "2024-06-30T23:59:59Z"


@pytest.mark.usefixtures("stocks")
async def test_early_close_day(service: DataService, upstream):
    upstream.native_bars("30Min", "2024-11-01T00:00:00Z", "bars-AAPL-1Min-2024-11-29.json")
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
    upstream.native_bars("30Min", "2016-01-01T00:00:00Z", "bars-AAPL-1Min-2016-01-04.json")
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

    minutes = await service.candles(AAPL, Timeframe.M1, *period(NOVEMBER_29))

    assert minutes.frame.height == 209
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
        await service.candles(AAPL, Timeframe.M1, *period(NOVEMBER_29))

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
        await service.candles(AAPL, Timeframe.M1, start, end)
    response = await get_api(
        service,
        redis,
        f"/api/v1/data/instruments/stock:AAPL/candles?timeframe=1m&start={start}&end={end}",
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
        await service.candles(AAPL, Timeframe.M1, start, end)
    with caplog.at_level(logging.ERROR):
        response = await get_api(
            service,
            redis,
            f"/api/v1/data/instruments/stock:AAPL/candles?timeframe=1m&start={start}&end={end}",
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


# Background loads (a backtest's candles) wait for their share of the Alpaca budget.

FIRST_DAYS_2024 = {"01": "02", "02": "01", "03": "01", "04": "01", "05": "01", "06": "03"}


def paged_bars(upstream, minutes: list[int], first: Sequence[httpx.Response] = ()) -> respx.Route:
    """1m bars of AAPL in two pages for any month of the first half of 2024: a bar at 15:00 UTC
    of the month's first session, then one at 15:01; ``minutes`` gets the minute of our budget
    (``ratelimit._time() // 60``) each page was asked in. ``first`` are answered before."""
    before = iter(first)

    def answer(request: httpx.Request) -> httpx.Response:
        if (response := next(before, None)) is not None:
            return response
        minutes.append(int(ratelimit_module._time() // 60))
        month = request.url.params["start"][5:7]
        second = "page_token" in request.url.params
        bar = {
            "t": f"2024-{month}-{FIRST_DAYS_2024[month]}T15:0{int(second)}:00Z",
            "o": 100.0,
            "h": 101.0,
            "l": 99.0,
            "c": 100.5,
            "v": 1000,
        }
        return httpx.Response(
            200, json={"bars": {"AAPL": [bar]}, "next_page_token": None if second else "next"}
        )

    return upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars", params={"timeframe": "1Min"}
    ).mock(side_effect=answer)


@pytest.fixture
def budget_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The clock of our budget's minutes, 59 s before a minute ends, moved by every budget wait
    instead of sleeping; ``budget_clock[1:]`` are the waits."""
    clock = [1_800_000_001.0]
    monkeypatch.setattr(ratelimit_module, "_time", lambda: clock[0])
    monkeypatch.setattr(alpaca_module, "_jitter", lambda: 0.0)
    return clock


@pytest.mark.usefixtures("stocks")
async def test_a_background_load_waits_for_its_share_and_requests_are_served_meanwhile(
    settings: Settings,
    redis: Redis,
    http: httpx.AsyncClient,
    upstream,
    monkeypatch: pytest.MonkeyPatch,
    budget_clock: list[float],
):
    # A budget of 10 a minute: the background loads' share is 5.
    service = build_data_service(settings.model_copy(update={"alpaca_rate_limit": 10}), redis, http)
    service._sources[Market.STOCK].chunk_concurrency = 1  # one page after another
    november = upstream.bars("1Min", "2024-11-01T00:00:00Z", "bars-AAPL-1Min-2024-11-29.json")
    minutes: list[int] = []
    pages = paged_bars(upstream, minutes)
    served = []

    async def sleep(delay: float) -> None:
        if not served:
            # A chart while the load waits: the 3 requests of the lookup and the 5 of the share
            # leave it room in the budget.
            served.append(await service.candles(AAPL, Timeframe.M1, *period(NOVEMBER_29)))
        budget_clock[0] += delay

    monkeypatch.setattr(base_module, "_sleep", sleep)

    # Six months of two pages each: 12 requests, more than one minute's share.
    january_to_june = await service.candles(
        AAPL, Timeframe.M1, utc("2024-01-02T14:30"), utc("2024-07-01"), background=True
    )

    assert january_to_june.frame.height == 12
    assert pages.call_count == 12
    first = minutes[0]
    assert [minutes.count(first + n) for n in range(3)] == [5, 5, 2]
    assert november.call_count == 1
    assert served[0].frame.height == 209


@pytest.mark.usefixtures("stocks")
async def test_a_background_load_waits_for_room_in_the_whole_budget(
    settings: Settings,
    redis: Redis,
    http: httpx.AsyncClient,
    upstream,
    monkeypatch: pytest.MonkeyPatch,
    budget_clock: list[float],
):
    service = build_data_service(settings.model_copy(update={"alpaca_rate_limit": 10}), redis, http)
    upstream.bars("1Min", "2024-11-01T00:00:00Z", "bars-AAPL-1Min-2024-11-29.json")
    minutes: list[int] = []
    paged_bars(upstream, minutes)
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)
        budget_clock[0] += delay

    monkeypatch.setattr(base_module, "_sleep", sleep)
    await service.candles(AAPL, Timeframe.M1, *period(NOVEMBER_29))  # the lookup, cached
    minute = int(budget_clock[0] // 60)
    # Requests spent the rest of this minute's budget: the share has room, the budget none.
    await redis.set(f"data:rl:alpaca:{minute}", 10)
    monkeypatch.setattr(progress_module, "_time", lambda: 1_800_000_001.0)
    seen: list[LoadProgress] = []

    with watch_load(seen.append):
        january = await service.candles(
            AAPL, Timeframe.M1, utc("2024-01-02T14:30"), utc("2024-02-01"), background=True
        )

    assert january.frame.height == 2
    assert waits == [59.0]
    # A watched load says how long it waits, and that it goes on.
    assert seen == [
        LoadProgress(total=1),
        LoadProgress(total=1, waiting_until=1_800_000_060.0),
        LoadProgress(total=1),
        LoadProgress(total=1, loaded=1, cached=0, until=utc("2024-02-01")),
    ]
    assert minutes == [minute + 1, minute + 1]
    # The share is taken again in the minute the request is sent.
    assert await redis.get(f"data:rl:alpaca-background:{minute}") == b"1"
    assert await redis.get(f"data:rl:alpaca-background:{minute + 1}") == b"2"


@pytest.mark.usefixtures("stocks")
async def test_a_background_load_does_not_take_a_kept_budget_error(
    settings: Settings,
    redis: Redis,
    http: httpx.AsyncClient,
    upstream,
    monkeypatch: pytest.MonkeyPatch,
    budget_clock: list[float],
):
    service = build_data_service(settings.model_copy(update={"alpaca_rate_limit": 10}), redis, http)
    upstream.bars("1Min", "2024-11-01T00:00:00Z", "bars-AAPL-1Min-2024-11-29.json")
    minutes: list[int] = []
    pages = paged_bars(upstream, minutes)

    async def sleep(delay: float) -> None:
        budget_clock[0] += delay

    monkeypatch.setattr(base_module, "_sleep", sleep)
    await service.candles(AAPL, Timeframe.M1, *period(NOVEMBER_29))  # the lookup, cached
    minute = int(budget_clock[0] // 60)
    await redis.set(f"data:rl:alpaca:{minute}", 10)
    january = (utc("2024-01-02T14:30"), utc("2024-02-01"))
    # A chart finds the budget spent; its error is kept for the chunk.
    with pytest.raises(SourceUnavailable, match="budget"):
        await service.candles(AAPL, Timeframe.M1, *january)
    assert await redis.exists("data:fail:v3:candles:stock:AAPL:1m:2024-01") == 1

    candles = await service.candles(AAPL, Timeframe.M1, *january, background=True)

    assert candles.frame.height == 2
    assert pages.call_count == 2
    assert minutes == [minute + 1, minute + 1]


@pytest.mark.usefixtures("stocks")
async def test_a_background_load_takes_a_kept_error_of_alpaca_failing(
    service: DataService, upstream
):
    january = upstream.router.get(
        f"{upstream.ALPACA_DATA}/v2/stocks/bars", params={"timeframe": "1Min"}
    ).respond(503)
    period_of_january = (utc("2024-01-02T14:30"), utc("2024-02-01"))

    with pytest.raises(SourceUnavailable) as first:
        await service.candles(AAPL, Timeframe.M1, *period_of_january, background=True)
    with pytest.raises(SourceUnavailable) as second:
        await service.candles(AAPL, Timeframe.M1, *period_of_january, background=True)

    assert first.value.detail == "Alpaca failed with HTTP 503. Try again in a minute."
    assert second.value.detail == first.value.detail
    assert january.call_count == 3  # one request with its two retries, then the kept error


@pytest.mark.usefixtures("stocks")
async def test_a_background_load_waits_while_alpaca_rate_limits_it(
    service: DataService, upstream, monkeypatch: pytest.MonkeyPatch, caplog
):
    limited = [
        httpx.Response(429, headers={"Retry-After": "30"}),  # longer than a retry waits
        # Without Retry-After: retried twice at once, then until the next minute.
        httpx.Response(429),
        httpx.Response(429),
        httpx.Response(429),
    ]
    pages = paged_bars(upstream, [], limited)
    monkeypatch.setattr(alpaca_module, "_time", lambda: 1_800_000_015.0)  # 45 s to the minute
    monkeypatch.setattr(alpaca_module, "_jitter", lambda: 0.0)
    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(base_module, "_sleep", sleep)

    monkeypatch.setattr(progress_module, "_time", lambda: 1_800_000_015.0)
    seen: list[LoadProgress] = []

    with caplog.at_level(logging.WARNING), watch_load(seen.append):
        candles = await service.candles(
            AAPL, Timeframe.M1, utc("2024-01-02T14:30"), utc("2024-02-01"), background=True
        )

    assert candles.frame.height == 2
    assert pages.call_count == 6  # four 429s, then the two pages
    assert waits == [30.0, 45.0]
    waiting = [progress.waiting_until for progress in seen]
    assert waiting == [None, 1_800_000_045.0, None, 1_800_000_060.0, None, None]
    assert "Alpaca rate limited a background load: asking again in 30 s" in caplog.text
