"""Parsing of source responses and the chunk plan, on recorded responses (tests/fixtures)."""

import json
import logging
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, override

import httpx
import polars as pl
import pytest
import respx

from candlestack.core import RateLimiter, Settings
from candlestack.data import (
    CANDLE_SCHEMA,
    DataIntegrityError,
    Instrument,
    InstrumentId,
    Market,
    Session,
    SourceUnavailable,
    Timeframe,
)
from candlestack.data.cache import Cache, Fetch
from candlestack.data.models import SPARK_SCHEMA, STATS_SCHEMA
from candlestack.data.sources import alpaca as alpaca_module
from candlestack.data.sources import base
from candlestack.data.sources.alpaca import (
    AlpacaSource,
    parse_assets,
    parse_bars,
    parse_daily_bars,
    parse_snapshots,
    regular_candles,
    relabel_daily,
    snapshot_stats,
)
from candlestack.data.sources.base import DAY, Period, plan_periods, spend
from candlestack.data.sources.binance import (
    off_grid_span,
    parse_archive,
    parse_exchange_info,
    parse_klines,
    parse_ticker,
    to_seconds,
    usd_prices,
)

FIXTURES = Path(__file__).parents[2] / "fixtures"
HOUR = 3600


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


def fixture_bytes(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


def fixture_json(path: str) -> dict[str, Any]:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def aapl_bars(*paths: str) -> list[dict[str, Any]]:
    return [bar for path in paths for bar in fixture_json(f"alpaca/{path}")["bars"]["AAPL"]]


# Binance


def test_archive_with_millisecond_open_times() -> None:
    name = "BTCUSDT-1h-2024-01.zip"

    frame = parse_archive(fixture_bytes(f"binance/archive/monthly/{name}"), name)

    assert frame.schema == CANDLE_SCHEMA
    assert frame.height == 744
    assert frame.row(0) == (utc("2024-01-01"), 42283.58, 42554.57, 42261.02, 42475.23, 1271.68108)
    assert frame["ts"][-1] == utc("2024-01-31T23:00")


def test_archive_with_microsecond_open_times() -> None:
    name = "BTCUSDT-1h-2025-01.zip"

    frame = parse_archive(fixture_bytes(f"binance/archive/monthly/{name}"), name)

    assert frame.height == 744
    assert frame.row(0) == (utc("2025-01-01"), 93576.0, 94509.42, 93489.03, 94401.14, 755.9901)
    assert frame["ts"][-1] == utc("2025-01-31T23:00")


def test_archive_with_candles_off_the_grid() -> None:
    # After the maintenance of 2018-02-08 the 1h candles open at hh:28:14 for two days.
    name = "BTCUSDT-1h-2018-02.zip"

    frame = parse_archive(fixture_bytes(f"binance/archive/monthly/{name}"), name)

    assert frame.height == 640
    assert frame.filter(pl.col("ts") % HOUR != 0)["ts"][0] == utc("2018-02-09T09:28:14")
    assert off_grid_span(frame, Timeframe.H1) == (utc("2018-02-09T09:00"), utc("2018-02-11T04:00"))


def test_archive_on_the_grid_has_no_span_to_replace() -> None:
    name = "BTCUSDT-1h-2024-01.zip"

    frame = parse_archive(fixture_bytes(f"binance/archive/monthly/{name}"), name)

    assert off_grid_span(frame, Timeframe.H1) is None
    assert off_grid_span(frame.head(0), Timeframe.H1) is None


def test_open_time_unit_is_decided_per_value() -> None:
    frame = pl.DataFrame({"t": [1735603200000, 1735689600000000, 1502928000000]})

    assert frame.select(to_seconds("t"))["t"].to_list() == [1735603200, 1735689600, 1502928000]


def test_broken_archive_is_a_data_integrity_error() -> None:
    with pytest.raises(DataIntegrityError, match=r"BTCUSDT-1h-2024-01\.zip cannot be read") as info:
        parse_archive(b"not a zip file", "BTCUSDT-1h-2024-01.zip")

    assert info.value.source == "binance"


def test_klines_are_in_seconds_and_drop_the_candle_still_open() -> None:
    rows = fixture_json("binance/rest/klines-BTCUSDT-1h-limit5.json")
    fetched_at = 1790376162  # the newest row opened at 22:00 and closes at 22:59:59.999

    frame = parse_klines(rows, end=fetched_at)

    assert frame.schema == CANDLE_SCHEMA
    assert frame["ts"].to_list() == [1790359200, 1790362800, 1790366400, 1790370000]
    assert frame.row(0)[1:] == (83785.11, 84081.41, 83785.11, 83968.0, 341.90397)
    assert parse_klines(rows).height == 5


@pytest.mark.parametrize(
    "rows",
    [
        {"code": -1121, "msg": "Invalid symbol."},
        [[1790359200000, "x"]],
        [[1, "1"] * 3],
        [None],
        [{"code": -1121, "msg": "Invalid symbol."}],
        [[1790359200000, "83785.11", "84081.41", "83785.11", "83968.0", "n/a", 1790362799999]],
    ],
    ids=["error-object", "short-row", "text-prices", "null-row", "object-row", "text-volume"],
)
def test_malformed_klines_are_a_data_integrity_error(rows: object) -> None:
    with pytest.raises(DataIntegrityError, match="Binance klines cannot be read") as info:
        parse_klines(rows)

    assert info.value.source == "binance"


def test_exchange_info_lists_the_trading_pairs() -> None:
    instruments = parse_exchange_info(fixture_bytes("binance/rest/exchangeInfo-trading.json"))

    assert [str(item.id) for item in instruments] == [
        "crypto:BTCUSDT",
        "crypto:ETHUSDT",
        "crypto:SOLUSDT",
        "crypto:ETHBTC",
        "crypto:币安人生USDT",
    ]
    btc = instruments[0]
    assert (btc.name, btc.base, btc.quote, btc.source, btc.feed) == (
        "BTC/USDT",
        "BTC",
        "USDT",
        "binance",
        "spot",
    )
    assert {item.exchange for item in instruments} == {"BINANCE"}


def test_exchange_info_skips_pairs_that_are_not_trading() -> None:
    body = {
        "symbols": [
            {"symbol": "NEOBTC", "status": "BREAK", "baseAsset": "NEO", "quoteAsset": "BTC"},
            {"symbol": "BAD-PAIR", "status": "TRADING", "baseAsset": "B", "quoteAsset": "P"},
        ]
    }

    assert parse_exchange_info(json.dumps(body).encode()) == []


@pytest.mark.parametrize(
    "body",
    [
        b"<html><body><h1>502 Bad Gateway</h1></body></html>",
        b"[]",
        b'{"code": -1003, "msg": "Too many requests."}',
        b'{"symbols": null}',
        b'{"symbols": [{"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT"}]}',
        b'{"symbols": ["BTCUSDT"]}',
    ],
    ids=["not-json", "array", "error-object", "null-symbols", "no-status", "text-symbols"],
)
def test_malformed_exchange_info_is_a_data_integrity_error(body: bytes) -> None:
    with pytest.raises(DataIntegrityError, match="Binance exchangeInfo cannot be read") as info:
        parse_exchange_info(body)

    assert info.value.source == "binance"


# Alpaca


def test_assets_keep_tradable_stocks_outside_otc() -> None:
    instruments = parse_assets(fixture_bytes("alpaca/assets.json"))

    assert [(str(item.id), item.exchange) for item in instruments] == [
        ("stock:AAPL", "NASDAQ"),
        ("stock:SPY", "ARCA"),
        ("stock:BRK.B", "NYSE"),
    ]
    assert instruments[0].name == "Apple Inc. Common Stock"


def test_assets_skip_symbols_that_are_not_instrument_symbols(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asset = {"name": "Some Corp", "exchange": "NYSE", "tradable": True}
    body = [
        {**asset, "symbol": "AAPL"},
        {**asset, "symbol": "BAD/SYMBOL"},  # not a valid symbol at all
        {**asset, "symbol": "brk.a"},  # valid only upper-cased: not the source's spelling
    ]

    with caplog.at_level(logging.WARNING):
        instruments = parse_assets(json.dumps(body).encode())

    assert [str(item.id) for item in instruments] == ["stock:AAPL"]
    assert "Skipping Alpaca symbol 'BAD/SYMBOL'" in caplog.text
    assert "Skipping Alpaca symbol 'brk.a'" in caplog.text


def test_assets_warn_about_exchanges_outside_the_filter(caplog: pytest.LogCaptureFixture) -> None:
    asset = {"name": "Some Corp", "tradable": True}
    body = [
        {**asset, "symbol": "AAPL", "exchange": "NASDAQ"},
        {**asset, "symbol": "NEWA", "exchange": "NEWX"},
        {**asset, "symbol": "NEWB", "exchange": "NEWX"},
        {**asset, "symbol": "IEXA", "exchange": "IEX"},
    ]

    with caplog.at_level(logging.WARNING):
        instruments = parse_assets(json.dumps(body).encode())

    assert [item.exchange for item in instruments] == ["NASDAQ", None, None, None]
    assert "exchanges outside Exchange: IEX (1), NEWX (2)" in caplog.text


def test_assets_warn_about_a_stock_on_binance(caplog: pytest.LogCaptureFixture) -> None:
    # BINANCE is an Exchange, but of the crypto pairs: the stock gets none, so exchange=BINANCE
    # never finds it.
    body = [{"name": "Some Corp", "tradable": True, "symbol": "BNBX", "exchange": "BINANCE"}]

    with caplog.at_level(logging.WARNING):
        instruments = parse_assets(json.dumps(body).encode())

    assert [item.exchange for item in instruments] == [None]
    assert "exchanges outside Exchange: BINANCE (1)" in caplog.text


def test_assets_warn_about_a_null_exchange_next_to_a_named_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asset = {"name": "Some Corp", "tradable": True}
    body = [
        {**asset, "symbol": "NULA", "exchange": None},
        {**asset, "symbol": "NEWA", "exchange": "NEWX"},
    ]

    with caplog.at_level(logging.WARNING):
        instruments = parse_assets(json.dumps(body).encode())

    assert [item.exchange for item in instruments] == [None, None]
    assert "exchanges outside Exchange: NEWX (1), None (1)" in caplog.text


def test_assets_do_not_count_exchanges_of_skipped_symbols(
    caplog: pytest.LogCaptureFixture,
) -> None:
    asset = {"name": "Some Corp", "tradable": True}
    body = [
        {**asset, "symbol": "AAPL", "exchange": "NASDAQ"},
        {**asset, "symbol": "brk.a", "exchange": "NEWX"},
    ]

    with caplog.at_level(logging.WARNING):
        instruments = parse_assets(json.dumps(body).encode())

    assert [str(item.id) for item in instruments] == ["stock:AAPL"]
    assert "outside Exchange" not in caplog.text


def test_assets_on_known_exchanges_do_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        parse_assets(fixture_bytes("alpaca/assets.json"))

    assert "outside Exchange" not in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        fixture_bytes("alpaca/error-no-auth.html"),
        b'{"message": "forbidden."}',
        b"null",
        b'[{"symbol": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"}]',
        b'[{"symbol": "AAPL", "tradable": true}]',
        b'["AAPL"]',
    ],
    ids=["html", "error-object", "null", "no-tradable", "no-exchange", "text-assets"],
)
def test_malformed_assets_are_a_data_integrity_error(body: bytes) -> None:
    with pytest.raises(DataIntegrityError, match="Alpaca assets cannot be read") as info:
        parse_assets(body)

    assert info.value.source == "alpaca"


def test_bars_of_two_pages() -> None:
    frame = parse_bars(
        aapl_bars("bars-AAPL-1Min-2024-06-03-p0.json", "bars-AAPL-1Min-2024-06-03-p1.json")
    )

    assert frame.schema == CANDLE_SCHEMA
    assert frame.height == 320
    assert frame.row(0) == (utc("2024-06-03T13:30"), 191.16, 191.47, 191.09, 191.43, 18889.0)
    assert frame["ts"][-1] == utc("2024-06-03T19:59")


def test_malformed_bars_are_a_data_integrity_error() -> None:
    with pytest.raises(DataIntegrityError, match="Alpaca bars cannot be read"):
        parse_bars([{"t": "yesterday", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}])


def test_daily_bars_are_labelled_with_the_session_open(sessions: list[Session]) -> None:
    frame = relabel_daily(
        parse_bars(
            aapl_bars("bars-AAPL-1Day-2024-06.json", "bars-AAPL-1Day-2024-11-25_12-02.json")
        ),
        sessions,
    )

    assert frame.height == 24
    assert frame["ts"][0] == utc("2024-06-03T13:30")  # 09:30 EDT
    early_close = frame.filter(pl.col("ts") == utc("2024-11-29T14:30"))  # 09:30 EST
    assert early_close.row(0)[1:] == (233.11, 236.09, 232.28, 235.68, 549571.0)


def test_daily_bars_without_a_session_are_dropped(sessions: list[Session]) -> None:
    holiday = {"t": "2024-07-04T04:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}

    assert relabel_daily(parse_bars([holiday]), sessions).height == 0


def test_regular_candles_drop_prints_outside_the_session(sessions: list[Session]) -> None:
    march = Period("closed", utc("2024-03-01"), utc("2024-04-01"), "2024-03", DAY)

    frame = regular_candles(
        parse_bars(aapl_bars("bars-AAPL-1Min-2024-03-08-open.json")), Timeframe.M1, march, sessions
    )

    assert frame.height == 62
    assert frame["ts"][0] == utc("2024-03-08T14:30")  # the 14:29 print before the open is gone


def test_regular_candles_of_the_live_tail_are_closed(sessions: list[Session]) -> None:
    live = Period("live", utc("2024-06-03"), utc("2024-06-03T14:00:30"), "2024-06-03-live", 60)
    bars = parse_bars(aapl_bars("bars-AAPL-1Min-2024-06-03-p0.json"))

    frame = regular_candles(bars, Timeframe.M1, live, sessions)

    assert frame["ts"][-1] == utc("2024-06-03T13:59")  # 13:59 closed at 14:00, 14:00 is open


def test_regular_daily_candle_waits_for_the_close(sessions: list[Session]) -> None:
    bars = parse_bars(aapl_bars("bars-AAPL-1Day-2024-11-25_12-02.json"))
    during = Period("live", utc("2024-11-29"), utc("2024-11-29T17:59"), "2024-11-29-live", 60)
    after = Period("live", utc("2024-11-29"), utc("2024-11-29T18:00"), "2024-11-29-live", 60)

    assert regular_candles(bars, Timeframe.D1, during, sessions).height == 0
    assert regular_candles(bars, Timeframe.D1, after, sessions)["ts"].to_list() == [
        utc("2024-11-29T14:30")
    ]


# plan_periods


def plan(start: str, end: str, now: str, **policy: bool) -> list[tuple[str, str, int, int, int]]:
    periods = plan_periods(
        utc(start),
        utc(end),
        utc(now),
        closed_ttl=30 * DAY,
        current_ttl=DAY,
        **{"yearly": False, "daily": True, **policy},
    )
    return [(p.kind, p.label, p.start, p.end, p.ttl) for p in periods]


def test_plan_closed_months_days_of_the_current_month_and_today() -> None:
    now = "2026-09-03T12:00"

    assert plan("2026-07-31T22:00", now, now) == [
        ("closed", "2026-07", utc("2026-07-01"), utc("2026-08-01"), 30 * DAY),
        ("closed", "2026-08", utc("2026-08-01"), utc("2026-09-01"), 30 * DAY),
        ("current", "2026-09-01", utc("2026-09-01"), utc("2026-09-02"), DAY),
        ("current", "2026-09-02", utc("2026-09-02"), utc("2026-09-03"), DAY),
        ("live", "2026-09-03-live", utc("2026-09-03"), utc(now), 60),
    ]


def test_plan_covers_only_what_the_request_overlaps() -> None:
    now = "2026-09-26T12:00"

    assert [p[1] for p in plan("2026-08-31T23:00", "2026-09-02T01:00", now)] == [
        "2026-08",
        "2026-09-01",
        "2026-09-02",
    ]
    assert [p[1] for p in plan("2026-09-26T01:00", "2026-09-26T02:00", now)] == ["2026-09-26-live"]


def test_plan_current_month_as_one_chunk() -> None:
    now = "2026-09-26T12:00"

    assert plan("2026-08-10", now, now, daily=False) == [
        ("closed", "2026-08", utc("2026-08-01"), utc("2026-09-01"), 30 * DAY),
        ("current", "2026-09-01..2026-09-26", utc("2026-09-01"), utc("2026-09-26"), DAY),
        ("live", "2026-09-26-live", utc("2026-09-26"), utc(now), 60),
    ]


def test_plan_years() -> None:
    now = "2026-09-26T12:00"

    assert [p[:2] for p in plan("2024-12-31", now, now, yearly=True, daily=False)] == [
        ("closed", "2024"),
        ("closed", "2025"),
        ("current", "2026-01-01..2026-09-26"),
        ("live", "2026-09-26-live"),
    ]


def test_plan_on_the_first_day_of_a_month() -> None:
    now = "2026-10-01T05:00"

    assert [p[1] for p in plan("2026-09-30", now, now)] == ["2026-09", "2026-10-01-live"]
    assert [p[1] for p in plan("2026-09-30", now, now, daily=False)] == [
        "2026-09",
        "2026-10-01-live",
    ]


# spend


class ScriptedLimiter(RateLimiter):
    """Answers hits with the given waits, without Redis."""

    def __init__(self, *waits: float) -> None:
        self.waits = list(waits)

    @override
    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        return self.waits.pop(0)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(base, "_sleep", sleep)
    return delays


@pytest.mark.anyio
async def test_spend_waits_for_the_next_window_when_it_is_near(sleeps: list[float]) -> None:
    limiter = ScriptedLimiter(3.2, 0.0)

    await spend(limiter, "alpaca", "Alpaca request budget (60 per minute)", "data:rl:alpaca", 60)

    assert sleeps == [3.2]


@pytest.mark.anyio
async def test_spend_refuses_when_the_next_window_is_far(sleeps: list[float]) -> None:
    limiter = ScriptedLimiter(42.3)

    with pytest.raises(SourceUnavailable) as info:
        await spend(
            limiter, "binance", "Binance weight budget (1000 per minute)", "k", 1000, cost=2
        )

    assert info.value.detail == (
        "Our Binance weight budget (1000 per minute) is spent. Try again in 43 seconds."
    )
    assert (info.value.source, info.value.retry_after) == ("binance", 43)
    assert sleeps == []


# Alpaca's calendar and bars as the source reads them: malformed bodies are
# DataIntegrityError naming the source, never a KeyError or TypeError.

ALPACA_API, ALPACA_DATA = "https://api.alpaca.test", "https://data.alpaca.test"
JUNE = Period("closed", utc("2024-06-01"), utc("2024-07-01"), "2024-06", DAY)


class Unlimited(RateLimiter):
    """Lets every request through, without Redis."""

    def __init__(self) -> None:
        pass

    @override
    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        return 0.0


class NoCache(Cache):
    """Fetches every value, without Redis."""

    def __init__(self) -> None:
        pass

    @override
    async def get_or_fetch(self, name: str, fetch: Fetch) -> bytes:
        return (await fetch())[0]


@pytest.fixture
def alpaca_api() -> Iterator[respx.MockRouter]:
    with respx.mock() as router:
        yield router


@pytest.fixture
async def alpaca(alpaca_api: respx.MockRouter) -> AsyncIterator[AlpacaSource]:
    settings = Settings(
        app_env="test",
        alpaca_api_url=ALPACA_API,
        alpaca_data_url=ALPACA_DATA,
        alpaca_key_id="test-key-id",
        alpaca_secret_key="test-secret",
    )
    async with httpx.AsyncClient() as http:
        yield AlpacaSource(settings, http, Unlimited(), NoCache())


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"<html><body>504 Gateway Time-out</body></html>", "not valid JSON"),
        (b'{"message": "internal server error"}', "calendar cannot be read: TypeError"),
        (b"[null]", "calendar cannot be read: TypeError"),
        (b'[{"date": "2024-06-03", "open": "09:30"}]', "calendar cannot be read: KeyError"),
        (b'[{"date": "2024-06-03", "open": "9:30", "close": "16:00"}]', "Malformed trading"),
        (b'[{"date": "2024-06-03", "open": "16:00", "close": "09:30"}]', "closes before it opens"),
    ],
    ids=["html", "error-object", "null-day", "no-close", "bad-time", "close-before-open"],
)
async def test_malformed_calendar_is_a_data_integrity_error(
    alpaca: AlpacaSource, alpaca_api: respx.MockRouter, body: bytes, error: str
) -> None:
    alpaca_api.get(f"{ALPACA_API}/v2/calendar").respond(200, content=body)

    with pytest.raises(DataIntegrityError, match=error) as info:
        await alpaca.sessions()

    assert info.value.source == "alpaca"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"<!DOCTYPE html><title>503 Service Unavailable</title>", "not valid JSON"),
        (b"[]", "bars response is not an object"),
        (b"null", "bars response is not an object"),
        (b'{"bars": ["AAPL"]}', "bars response has no list of bars"),
        (b'{"bars": {"AAPL": {"t": "2024-06-03T13:30:00Z"}}}', "bars response has no list of bars"),
        (b'{"bars": {"AAPL": [1]}}', "bars cannot be read"),
        (b'{"bars": {"AAPL": [{"t": "2024-06-03T13:30:00Z", "o": "n/a"}]}}', "bars cannot be read"),
    ],
    ids=["html", "array", "null", "bars-array", "bar-object", "number-bar", "text-price"],
)
async def test_malformed_bars_response_is_a_data_integrity_error(
    alpaca: AlpacaSource, alpaca_api: respx.MockRouter, body: bytes, error: str
) -> None:
    alpaca_api.get(f"{ALPACA_DATA}/v2/stocks/bars").respond(200, content=body)

    with pytest.raises(DataIntegrityError, match=error) as info:
        await alpaca.fetch_period("AAPL", Timeframe.M1, JUNE)

    assert info.value.source == "alpaca"


@pytest.mark.anyio
@pytest.mark.parametrize(("budget", "share"), [(200, 90), (180, 90), (100, 50), (60, 30), (1, 1)])
async def test_background_loads_get_half_the_alpaca_budget_up_to_90(
    budget: int, share: int
) -> None:
    settings = Settings(app_env="test", alpaca_rate_limit=budget)
    async with httpx.AsyncClient() as http:
        source = AlpacaSource(settings, http, Unlimited(), NoCache())

    assert source._background_limit == share


# --- SIP delay --------------------------------------------------------------------------------


def alpaca_plan(
    alpaca: AlpacaSource, timeframe: Timeframe, start: str, now: str
) -> list[tuple[str, str, int, int]]:
    periods = alpaca.periods(timeframe, utc(start), utc(now), utc(now))
    return [(p.kind, p.label, p.end, p.ttl) for p in periods]


@pytest.mark.anyio
async def test_alpaca_chunks_end_15_minutes_before_now(alpaca: AlpacaSource) -> None:
    assert alpaca_plan(alpaca, Timeframe.M1, "2024-05-31", "2024-06-03T15:00") == [
        ("closed", "2024-05", utc("2024-06-01"), DAY),
        ("current", "2024-06-01..2024-06-03", utc("2024-06-03"), DAY),
        ("live", "2024-06-03-live", utc("2024-06-03T14:45"), 60),
    ]
    # A day joins the cached chunk of the month only once it ended 15 minutes ago.
    assert alpaca_plan(alpaca, Timeframe.M1, "2024-06-02", "2024-06-04T00:10") == [
        ("current", "2024-06-01..2024-06-03", utc("2024-06-03"), DAY),
        ("live", "2024-06-03-live", utc("2024-06-03T23:55"), 60),
    ]
    assert alpaca_plan(alpaca, Timeframe.M1, "2024-06-02", "2024-06-04T00:20") == [
        ("current", "2024-06-01..2024-06-04", utc("2024-06-04"), DAY),
        ("live", "2024-06-04-live", utc("2024-06-04T00:05"), 60),
    ]


@pytest.mark.anyio
async def test_alpaca_daily_chunks_are_kept_only_until_extended_hours_are_final(
    alpaca: AlpacaSource,
) -> None:
    # 1Day bars count the volume of extended hours, until 01:00 UTC at the latest: a chunk
    # fetched before 01:15 UTC is kept until then.
    assert alpaca_plan(alpaca, Timeframe.D1, "2023-12-01", "2024-06-04T00:20") == [
        ("closed", "2023", utc("2024-01-01"), DAY),
        ("current", "2024-01-01..2024-06-04", utc("2024-06-04"), 3300),
        ("live", "2024-06-04-live", utc("2024-06-04T00:05"), 60),
    ]
    assert alpaca_plan(alpaca, Timeframe.D1, "2024-12-01", "2025-01-01T00:30")[0] == (
        "closed",
        "2024",
        utc("2025-01-01"),
        2700,
    )
    assert alpaca_plan(alpaca, Timeframe.D1, "2024-05-01", "2024-06-04T01:10")[0][3] == 300
    assert alpaca_plan(alpaca, Timeframe.D1, "2024-05-01", "2024-06-04T01:14:30")[0][3] == 60
    assert alpaca_plan(alpaca, Timeframe.D1, "2024-05-01", "2024-06-04T02:00")[0][3] == DAY
    # The 1m bars of the regular sessions are final by then.
    assert alpaca_plan(alpaca, Timeframe.M1, "2024-06-03", "2024-06-04T00:20")[0][3] == DAY


@pytest.fixture
def at_noon(monkeypatch: pytest.MonkeyPatch) -> int:
    """Alpaca's clock at 2024-06-03T15:00Z, during the session."""
    now = utc("2024-06-03T15:00")
    monkeypatch.setattr(alpaca_module, "_time", lambda: float(now))
    return now


@pytest.mark.anyio
async def test_alpaca_bars_never_end_within_the_last_15_minutes(
    alpaca: AlpacaSource, alpaca_api: respx.MockRouter, at_noon: int
) -> None:
    alpaca_api.get(f"{ALPACA_API}/v2/calendar").respond(
        200, content=fixture_bytes("alpaca/calendar-2024.json")
    )
    bars = alpaca_api.get(f"{ALPACA_DATA}/v2/stocks/bars").respond(
        200, content=fixture_bytes("alpaca/bars-AAPL-1Min-2024-06-03-p1.json")
    )
    # A period up to now, as a caller that forgot the delay would plan it.
    live = Period("live", utc("2024-06-03"), at_noon, "2024-06-03-live", 60)

    await alpaca.fetch_period("AAPL", Timeframe.M1, live)
    await alpaca.first_timestamp("AAPL")

    for call in bars.calls:
        assert call.request.url.params["end"] == "2024-06-03T14:44:59Z"
        assert call.request.url.params["feed"] == "sip"


@pytest.mark.anyio
async def test_an_empty_live_chunk_asks_for_no_bars(
    alpaca: AlpacaSource, alpaca_api: respx.MockRouter
) -> None:
    # The live chunk in the second its day begins (15 minutes after UTC midnight).
    calendar = alpaca_api.get(f"{ALPACA_API}/v2/calendar").respond(
        200, content=fixture_bytes("alpaca/calendar-2024.json")
    )
    live = Period("live", utc("2024-06-04"), utc("2024-06-04"), "2024-06-04-live", 60)

    frame = await alpaca.fetch_period("AAPL", Timeframe.M1, live)

    assert (frame.schema, frame.height) == (CANDLE_SCHEMA, 0)
    assert alpaca_api.calls.call_count == calendar.call_count == 1


@pytest.mark.anyio
@pytest.mark.parametrize("status", [403, 422])
async def test_a_refused_data_plan_is_an_error_for_a_developer(
    alpaca: AlpacaSource,
    alpaca_api: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
    status: int,
) -> None:
    alpaca_api.get(f"{ALPACA_DATA}/v2/stocks/bars").respond(
        status,
        json={"code": 42210000, "message": "subscription does not permit querying recent SIP data"},
    )

    with caplog.at_level(logging.ERROR), pytest.raises(SourceUnavailable) as info:
        await alpaca.fetch_period("AAPL", Timeframe.M1, JUNE)

    assert info.value.detail == "Alpaca refused this server's market data plan for the request."
    assert not info.value.rejected  # not a symbol's fault: no splitting of a batch
    assert f"Alpaca refused our data plan: HTTP {status}: " in caplog.text
    assert "subscription does not permit" in caplog.text


# --- 24 h stats -------------------------------------------------------------------------------


def stats_rows(frame: pl.DataFrame) -> dict[str, tuple[Any, ...]]:
    assert frame.schema == STATS_SCHEMA
    return {symbol: tuple(rest) for symbol, *rest in frame.iter_rows()}


def test_ticker_gives_the_stats_of_the_catalog_pairs_in_usd() -> None:
    pairs = parse_exchange_info(fixture_bytes("binance/rest/exchangeInfo-trading.json"))

    stats = stats_rows(parse_ticker(fixture_bytes("binance/rest/ticker-24hr-trading.json"), pairs))

    assert stats["BTCUSDT"] == (83162.0, 1.592, pytest.approx(1231893002.5523036))
    # ETH/BTC: the volume in BTC at the price of BTCUSDT
    assert stats["ETHBTC"] == (0.03005, 0.502, pytest.approx(296.85208692 * 83162.0))
    assert set(stats) == {"BTCUSDT", "ETHUSDT", "SOLUSDT", "ETHBTC", "币安人生USDT"}


def test_ticker_converts_quote_assets_both_ways_and_skips_what_it_cannot() -> None:
    pairs = [
        crypto_pair("BTCUSDT", "BTC", "USDT"),
        crypto_pair("USDTTRY", "USDT", "TRY"),
        crypto_pair("BTCTRY", "BTC", "TRY"),
        crypto_pair("XYZABC", "XYZ", "ABC"),  # ABC has no USD price
        crypto_pair("DEADUSDT", "DEAD", "USDT"),  # no price: no stats
        crypto_pair("ODDUSDT", "ODD", "USDT"),  # change and volume are not numbers
        crypto_pair("GONEUSDT", "GONE", "USDT"),  # not in the ticker
    ]
    body = json.dumps(
        [
            ticker_row("BTCUSDT", "80000", "1.5", "1000000"),
            ticker_row("USDTTRY", "40", "0.1", "500"),
            ticker_row("BTCTRY", "3200000", "-0.5", "80000"),
            ticker_row("XYZABC", "2", "3", "100"),
            ticker_row("DEADUSDT", "0", "0", "0"),
            ticker_row("ODDUSDT", "1", "NaN", "inf"),
            ticker_row("NOTLISTED", "1", "1", "1"),
        ]
    ).encode()

    stats = stats_rows(parse_ticker(body, pairs))

    assert stats == {
        "BTCUSDT": (80000.0, 1.5, 1000000.0),
        "USDTTRY": (40.0, 0.1, 500 / 40),
        "BTCTRY": (3200000.0, -0.5, 80000 / 40),
        "XYZABC": (2.0, 3.0, None),
        "ODDUSDT": (1.0, None, None),
    }


def test_usd_prices() -> None:
    assert usd_prices([("BTC", "USDT", 80000.0), ("USDT", "TRY", 40.0), ("ETH", "BTC", 0.03)]) == {
        "USDT": 1.0,
        "BTC": 80000.0,
        "TRY": 1 / 40,
    }


@pytest.mark.parametrize("body", [b'{"code": -1121}', b"[{", b"not json"])
def test_malformed_ticker_is_a_data_integrity_error(body: bytes) -> None:
    with pytest.raises(DataIntegrityError, match="24h ticker"):
        parse_ticker(body, [])


SNAPSHOTS_AT = utc("2026-10-14T00:00")


def test_snapshots_give_price_change_and_volume_in_usd() -> None:
    frame, malformed = parse_snapshots(fixture_bytes("alpaca/snapshots-sample.json"), SNAPSHOTS_AT)
    stats = stats_rows(frame)

    assert stats == {
        "AAPL": (247.85, pytest.approx(0.458, abs=1e-4), pytest.approx(48213500 * 247.42)),
        # no volume-weighted price: the daily volume at the last price
        "SPY": (668.9, pytest.approx(0.3496, abs=1e-4), pytest.approx(61240000 * 668.9)),
        # the last daily bar is six weeks old (a halted stock): no change, no volume
        "BRK.B": (488.1, None, 0.0),
        # no trade: the daily close; no previous bar: no change
        "UUU": (2.15, None, pytest.approx(300 * 2.12)),
    }
    assert malformed == 0


@pytest.mark.parametrize(
    ("snapshot", "stats"),
    [
        ({"latestTrade": {"p": 0}}, None),
        ({"latestTrade": {"p": True}}, None),
        ({"latestTrade": {"p": "1.5"}}, None),
        ({"latestTrade": {"p": 10.0}}, (10.0, None, None)),  # no daily bar
        ({"latestTrade": {"p": 10.0}, "dailyBar": {"t": "yesterday"}}, (10.0, None, None)),
        (
            {"latestTrade": {"p": 10.0}, "dailyBar": {"t": "2026-10-13T04:00:00Z", "v": -1}},
            (10.0, None, None),
        ),
        (
            {
                "latestTrade": {"p": 10.0},
                "dailyBar": {"t": "2026-10-13T04:00:00Z", "v": 5, "vw": float("nan")},
                "prevDailyBar": {"c": 8.0},
            },
            (10.0, 25.0, 50.0),
        ),
        (None, None),
    ],
)
def test_snapshot_stats_of_odd_snapshots(snapshot: Any, stats: tuple[Any, ...] | None) -> None:
    assert snapshot_stats(snapshot, SNAPSHOTS_AT) == stats


@pytest.mark.parametrize("body", [b"[]", b"{"])
def test_malformed_snapshots_are_a_data_integrity_error(body: bytes) -> None:
    with pytest.raises(DataIntegrityError, match="Alpaca snapshots"):
        parse_snapshots(body, SNAPSHOTS_AT)


def test_empty_snapshots_have_no_stats() -> None:
    frame, malformed = parse_snapshots(b"{}", SNAPSHOTS_AT)
    assert (frame.schema, frame.height, malformed) == (STATS_SCHEMA, 0, 0)


@pytest.mark.parametrize(
    "snapshot",
    [
        [],
        "AAPL",
        {"latestTrade": [], "dailyBar": None},
        {"latestTrade": {"p": 1.0}, "dailyBar": "today"},
        {"latestTrade": {"p": 1.0}, "prevDailyBar": 5},
    ],
)
def test_a_malformed_snapshot_is_an_error_of_its_own(snapshot: Any) -> None:
    with pytest.raises(TypeError, match="not an object"):
        snapshot_stats(snapshot, SNAPSHOTS_AT)


def test_malformed_snapshots_are_skipped_and_counted() -> None:
    body = json.dumps(
        {
            "GOOD": {"latestTrade": {"p": 10.0}},
            "LIST": [],
            "BADBAR": {"latestTrade": {"p": 10.0}, "dailyBar": "x"},
            "NONE": None,
        }
    ).encode()

    frame, malformed = parse_snapshots(body, SNAPSHOTS_AT)

    assert stats_rows(frame) == {"GOOD": (10.0, None, None)}
    assert malformed == 2


def crypto_pair(symbol: str, base_asset: str, quote: str) -> Instrument:
    return Instrument(
        InstrumentId(Market.CRYPTO, symbol),
        f"{base_asset}/{quote}",
        "binance",
        "spot",
        "BINANCE",
        base_asset,
        quote,
    )


def ticker_row(symbol: str, price: str, change: str, quote_volume: str) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "priceChange": "0",
        "priceChangePercent": change,
        "lastPrice": price,
        "volume": "1",
        "quoteVolume": quote_volume,
        "count": 1,
    }


def _daily_bar(t: str, close: float | None = 10.0) -> dict[str, Any]:
    return {"t": t, "o": 9.0, "h": 11.0, "l": 8.0, "c": close, "v": 100, "n": 5, "vw": 10.0}


def test_parse_daily_bars_of_many_symbols(sessions: list[Session]) -> None:
    days = [s for s in sessions if s.date.isoformat() in ("2024-11-14", "2024-11-15")]
    body = {
        "bars": {
            "AAPL": [_daily_bar("2024-11-14T05:00:00Z"), _daily_bar("2024-11-15T05:00:00Z", 12.5)],
            # A day outside the asked sessions, and malformed bars that cost only themselves.
            "SPY": [
                _daily_bar("2024-11-13T05:00:00Z"),
                _daily_bar("2024-11-15T05:00:00Z", None),
                {**_daily_bar("2024-11-15T05:00:00Z"), "o": "9"},
                {**_daily_bar("2024-11-15T05:00:00Z"), "h": True},
                {**_daily_bar("2024-11-15T05:00:00Z"), "l": float("nan")},
                _daily_bar("not a time"),
                _daily_bar("2024-11-15T05:00:00"),  # without a time zone
                {"c": 1},
                "bar",
            ],
            "BRK.B": None,
        },
        "next_page_token": None,
    }

    frame, malformed = parse_daily_bars(body, days)

    assert frame.schema == SPARK_SCHEMA
    assert frame.sort("ts").rows() == [
        ("AAPL", days[0].open, 9.0, 11.0, 8.0, 10.0),
        ("AAPL", days[1].open, 9.0, 11.0, 8.0, 12.5),
    ]
    assert malformed == 8
    empty, malformed = parse_daily_bars({"bars": {}}, days)
    assert (empty.schema, malformed) == (SPARK_SCHEMA, 0)


@pytest.mark.parametrize("bars", [["AAPL"], {"AAPL": {"t": "x"}}])
def test_parse_daily_bars_refuses_a_body_without_lists(bars: Any, sessions: list[Session]) -> None:
    with pytest.raises(DataIntegrityError, match="no list of bars"):
        parse_daily_bars({"bars": bars}, sessions)
