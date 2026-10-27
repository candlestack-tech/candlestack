"""The data API over HTTP with a fake DataService: parameters, response shapes, error mapping,
the client rate limit and who may call it. The client is signed in unless a test signs out."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import orjson
import polars as pl
import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from candlestack.accounts import User
from candlestack.accounts.dependencies import current_user
from candlestack.core import Settings
from candlestack.core.ratelimit import get_rate_limiter
from candlestack.data import (
    CANDLE_SCHEMA,
    CandleSet,
    DataError,
    DataIntegrityError,
    Exchange,
    Instrument,
    InstrumentId,
    InstrumentInfo,
    InstrumentNotFound,
    InstrumentSort,
    InstrumentStats,
    InvalidRequest,
    Market,
    PeriodOutOfRange,
    SearchResult,
    SourceHealth,
    SourceUnavailable,
    Timeframe,
    TooManyCandles,
)
from candlestack.data.api import get_data_service
from candlestack.data.icons import FALLBACK, Icon
from candlestack.data.schemas import CandlesOut, candles_json

PROBLEM = "application/problem+json"
PROBLEMS = "https://candlestack.tech/problems/"

BTCUSDT = Instrument(
    InstrumentId(Market.CRYPTO, "BTCUSDT"),
    "BTC/USDT",
    "binance",
    "spot",
    "BINANCE",
    base="BTC",
    quote="USDT",
)
AAPL = Instrument(
    InstrumentId(Market.STOCK, "AAPL"), "Apple Inc. Common Stock", "alpaca", "sip", "NASDAQ"
)
BTCUSDT_STATS = InstrumentStats(83249.35, 1.51, 1258811866.0)
# Two days of BTCUSDT's mini chart, the first without a candle.
BTCUSDT_SPARK = [None, (1760313600, 115271.8, 115950.1, 113000.4, 115166.0)]
BTCUSDT_FIRST = 1502942400  # 2017-08-17T04:00Z; AAPL's is not known yet
AAPL_LOGO = Icon(b"\x89PNG\r\n\x1a\n-aapl", "image/png", 86400)
# 2024-06-03T00:00:00Z and 2024-06-04T00:00:00Z
DAY_START, DAY_END = 1717372800, 1717459200
USER = User(
    id="usr_7f3k2m9qx4",
    display_name="Ada",
    email="ada@example.com",
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    last_sign_in_at=datetime(2026, 10, 1, tzinfo=UTC),
)


def candle_set(
    instrument_id: InstrumentId, timeframe: Timeframe, start: int, end: int
) -> CandleSet:
    frame = pl.DataFrame(
        {
            "ts": [start, start + 3600, start + 3 * 3600],
            "open": [67491.0, 67612.5, 67580.1],
            "high": [67700.0, 67650.0, 67640.0],
            "low": [67420.2, 67510.0, 67488.8],
            "close": [67612.4, 67540.3, 67601.9],
            "volume": [512.31, 398.07, 0.0],
        },
        schema=CANDLE_SCHEMA,
    )
    return CandleSet(
        instrument=instrument_id,
        timeframe=timeframe,
        start=start,
        end=end,
        source="binance",
        feed="spot",
        frame=frame,
        gaps=[(start + 2 * 3600, start + 3 * 3600)],
        gaps_total=1,
        fingerprint="sha256:" + "ab" * 32,
    )


@dataclass
class FakeDataService:
    """Answers like the DataService; ``error`` is raised by every call when set."""

    error: DataError | None = None
    result: CandleSet | None = None
    health: dict[str, SourceHealth] = field(
        default_factory=lambda: {
            "binance": SourceHealth(ok=True, latency_ms=84, detail=None),
            "alpaca": SourceHealth(ok=True, latency_ms=131, detail=None),
        }
    )
    calls: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def _called(self, name: str, *args: Any) -> None:
        self.calls.append((name, args))
        if self.error is not None:
            raise self.error

    unavailable: list[Market] = field(default_factory=list)

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
        self._called("search", q, market, limit, offset, exchange, sort)
        found = [BTCUSDT, AAPL]
        stats = [BTCUSDT_STATS, None]
        sparks = [BTCUSDT_SPARK, None]
        firsts = [BTCUSDT_FIRST, None]
        return SearchResult(
            found[offset : offset + limit],
            self.unavailable,
            len(found),
            stats[offset : offset + limit],
            sparks[offset : offset + limit],
            firsts[offset : offset + limit],
        )

    async def top(self) -> SearchResult:
        self._called("top")
        return SearchResult([BTCUSDT], [], 1, [BTCUSDT_STATS], [BTCUSDT_SPARK], [BTCUSDT_FIRST])

    async def instrument(self, instrument_id: InstrumentId) -> InstrumentInfo:
        self._called("instrument", instrument_id)
        if instrument_id != AAPL.id:
            raise InstrumentNotFound(instrument_id)
        return InstrumentInfo(AAPL, tuple(Timeframe), 1451917800, 1790372340, 50000)

    async def icon(self, instrument_id: InstrumentId) -> Icon:
        self._called("icon", instrument_id)
        if instrument_id == AAPL.id:
            return AAPL_LOGO
        if instrument_id == BTCUSDT.id:
            return FALLBACK
        raise InstrumentNotFound(instrument_id)

    async def candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, start: int, end: int
    ) -> CandleSet:
        self._called("candles", instrument_id, timeframe, start, end)
        return self.result or candle_set(instrument_id, timeframe, start, end)

    async def last_candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, end: int, limit: int
    ) -> CandleSet:
        self._called("last_candles", instrument_id, timeframe, end, limit)
        start = end - limit * timeframe.seconds  # the server's pick, from the request
        return self.result or candle_set(instrument_id, timeframe, start, end)

    async def sources_health(self) -> dict[str, SourceHealth]:
        self._called("sources_health")
        return self.health


@dataclass
class FakeLimiter:
    """``RateLimiter.hit`` that answers ``wait`` (0 when the limit is off), recording calls."""

    wait: float = 0.0
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        self.calls.append((key, limit))
        return self.wait if limit > 0 else 0.0


@pytest.fixture
def service() -> FakeDataService:
    return FakeDataService()


@pytest.fixture
def limiter() -> FakeLimiter:
    return FakeLimiter()


@pytest.fixture
def app(app: FastAPI, service: FakeDataService, limiter: FakeLimiter) -> FastAPI:
    app.dependency_overrides[get_data_service] = lambda: service
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[current_user] = lambda: USER
    return app


@pytest.fixture
def signed_out(app: FastAPI) -> FastAPI:
    del app.dependency_overrides[current_user]
    return app


def problem(response: Any, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == PROBLEM
    body = response.json()
    assert body["type"] == PROBLEMS + slug
    assert body["status"] == status
    return body


# --- who may call it -------------------------------------------------------------------------

MARKET_DATA = [
    "/api/v1/data/instruments",
    "/api/v1/data/instruments?q=btc",
    "/api/v1/data/instruments/stock:AAPL",
    "/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=1h&start=2024-06-03",
]


@pytest.mark.usefixtures("signed_out")
@pytest.mark.parametrize("path", MARKET_DATA)
def test_market_data_needs_a_session(
    client: TestClient, service: FakeDataService, limiter: FakeLimiter, path: str
) -> None:
    response = client.get(path)

    problem(response, 401, "not-signed-in")
    assert service.calls == []
    assert limiter.calls == []


@pytest.mark.parametrize("path", MARKET_DATA)
def test_market_data_answers_a_signed_in_account(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


@pytest.mark.usefixtures("signed_out")
def test_the_top_instruments_the_icon_and_the_sources_health_need_no_session(
    client: TestClient,
) -> None:
    assert client.get("/api/v1/data/top-instruments").status_code == 200
    assert client.get("/api/v1/data/instruments/stock:AAPL/icon").status_code == 200
    assert client.get("/api/health/sources").status_code == 200


def test_candles_slide_the_session_on(app: FastAPI, client: TestClient) -> None:
    def renewed(response: Response) -> User:
        response.set_cookie("session", "token", max_age=2592000)
        return USER

    app.dependency_overrides[current_user] = renewed

    response = client.get(CANDLES)

    assert response.status_code == 200
    assert "Max-Age=2592000" in response.headers["set-cookie"]


# --- search ---------------------------------------------------------------------------------


def test_search(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments", params={"q": "btc"})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "crypto:BTCUSDT",
                "market": "crypto",
                "symbol": "BTCUSDT",
                "name": "BTC/USDT",
                "source": "binance",
                "feed": "spot",
                "exchange": "BINANCE",
                "base": "BTC",
                "quote": "USDT",
                "price": 83249.35,
                "change_24h": 1.51,
                "volume_24h": 1258811866.0,
                "spark": [None, [1760313600, 115271.8, 115950.1, 113000.4, 115166.0]],
                "available_from": 1502942400,
            },
            {
                "id": "stock:AAPL",
                "market": "stock",
                "symbol": "AAPL",
                "name": "Apple Inc. Common Stock",
                "source": "alpaca",
                "feed": "sip",
                "exchange": "NASDAQ",
                "base": None,
                "quote": None,
                "price": None,
                "change_24h": None,
                "volume_24h": None,
                "spark": None,
                "available_from": None,
            },
        ],
        "count": 2,
        "total": 2,
        "offset": 0,
        "unavailable": [],
    }
    assert service.calls == [("search", ("btc", None, 20, 0, None, InstrumentSort.VOLUME))]


def test_search_names_a_market_that_cannot_be_searched(
    client: TestClient, service: FakeDataService
) -> None:
    service.unavailable = [Market.STOCK]

    response = client.get("/api/v1/data/instruments?q=btc")

    assert response.status_code == 200
    assert response.json()["unavailable"] == ["stock"]


def test_search_passes_market_and_limit(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments?q=apple&market=stock&limit=1")

    assert response.json()["count"] == 1
    assert service.calls == [("search", ("apple", Market.STOCK, 1, 0, None, InstrumentSort.VOLUME))]


def test_search_without_a_query(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments?market=stock")

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert service.calls == [("search", (None, Market.STOCK, 20, 0, None, InstrumentSort.VOLUME))]


def test_search_pages(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments?market=stock&offset=1&limit=50")

    body = response.json()
    assert [item["id"] for item in body["items"]] == ["stock:AAPL"]
    assert (body["count"], body["total"], body["offset"]) == (1, 2, 1)
    assert service.calls == [("search", (None, Market.STOCK, 50, 1, None, InstrumentSort.VOLUME))]


def test_search_passes_exchange(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments?exchange=NYSE&q=brk")

    assert response.status_code == 200
    assert service.calls == [("search", ("brk", None, 20, 0, Exchange.NYSE, InstrumentSort.VOLUME))]


def test_search_passes_binance(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments?exchange=BINANCE")

    assert response.status_code == 200
    assert service.calls == [
        ("search", (None, None, 20, 0, Exchange.BINANCE, InstrumentSort.VOLUME))
    ]


@pytest.mark.parametrize(
    ("value", "sort"),
    [
        ("volume", InstrumentSort.VOLUME),
        ("change", InstrumentSort.CHANGE),
        ("-change", InstrumentSort.LOSERS),
        ("symbol", InstrumentSort.SYMBOL),
    ],
)
def test_search_passes_the_sort(
    client: TestClient, service: FakeDataService, value: str, sort: InstrumentSort
) -> None:
    response = client.get("/api/v1/data/instruments", params={"sort": value})

    assert response.status_code == 200
    assert service.calls == [("search", (None, None, 20, 0, None, sort))]


def test_search_offset_past_the_end(client: TestClient) -> None:
    response = client.get("/api/v1/data/instruments?q=btc&offset=1000")

    assert response.status_code == 200
    body = response.json()
    assert (body["items"], body["count"], body["total"], body["offset"]) == ([], 0, 2, 1000)


@pytest.mark.parametrize(
    ("query", "detail"),
    [
        ("q=", "query parameter 'q': String should have at least 1 character"),
        (f"q={'a' * 51}", "query parameter 'q': String should have at most 50 characters"),
        ("q=btc&limit=0", "query parameter 'limit': Input should be greater than or equal to 1"),
        ("q=btc&limit=101", "query parameter 'limit': Input should be less than or equal to 100"),
        ("q=btc&offset=-1", "query parameter 'offset': Input should be greater than or equal to 0"),
        ("q=btc&market=forex", "query parameter 'market': Input should be 'crypto' or 'stock'"),
        (
            "sort=gainers",
            "query parameter 'sort': Input should be 'volume', 'change', '-change' or 'symbol'",
        ),
        (
            "exchange=LSE",
            (
                "query parameter 'exchange': Input should be 'AMEX', 'ARCA', 'BATS', 'BINANCE', "
                "'NASDAQ' or 'NYSE'"
            ),
        ),
    ],
)
def test_search_validation(
    client: TestClient, service: FakeDataService, query: str, detail: str
) -> None:
    response = client.get(f"/api/v1/data/instruments?{query}")

    assert problem(response, 422, "validation")["detail"] == detail
    assert service.calls == []


def test_search_without_catalog(client: TestClient, service: FakeDataService) -> None:
    service.error = SourceUnavailable("alpaca", "Alpaca did not answer in time. Try again later.")

    response = client.get("/api/v1/data/instruments?q=apple")

    body = problem(response, 503, "source-unavailable")
    assert body["source"] == "alpaca"
    assert "retry-after" not in response.headers


# --- top instruments ------------------------------------------------------------------------


@pytest.mark.usefixtures("signed_out")
def test_top_instruments(
    client: TestClient, service: FakeDataService, limiter: FakeLimiter
) -> None:
    response = client.get("/api/v1/data/top-instruments")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "id": "crypto:BTCUSDT",
                "market": "crypto",
                "symbol": "BTCUSDT",
                "name": "BTC/USDT",
                "source": "binance",
                "feed": "spot",
                "exchange": "BINANCE",
                "base": "BTC",
                "quote": "USDT",
                "price": 83249.35,
                "change_24h": 1.51,
                "volume_24h": 1258811866.0,
                "spark": [None, [1760313600, 115271.8, 115950.1, 113000.4, 115166.0]],
                "available_from": 1502942400,
            }
        ]
    }
    assert response.headers["cache-control"] == "public, max-age=60"
    assert service.calls == [("top", ())]
    assert limiter.calls == []


def test_top_instruments_without_catalog(client: TestClient, service: FakeDataService) -> None:
    service.error = SourceUnavailable("binance", "Binance did not answer in time. Try again later.")

    response = client.get("/api/v1/data/top-instruments")

    problem(response, 503, "source-unavailable")
    assert "cache-control" not in response.headers


# --- instrument detail ----------------------------------------------------------------------


def test_instrument_detail(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments/stock:aapl")

    assert response.status_code == 200
    assert response.json() == {
        "id": "stock:AAPL",
        "market": "stock",
        "symbol": "AAPL",
        "name": "Apple Inc. Common Stock",
        "source": "alpaca",
        "feed": "sip",
        "exchange": "NASDAQ",
        "base": None,
        "quote": None,
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        "available_from": 1451917800,
        "available_to": 1790372340,
        "max_candles": 50000,
    }
    assert service.calls == [("instrument", (AAPL.id,))]


def test_instrument_not_found(client: TestClient) -> None:
    response = client.get("/api/v1/data/instruments/crypto:NOPEUSDT")

    body = problem(response, 404, "instrument-not-found")
    assert body["title"] == "Instrument not found"
    assert body["instrument"] == "crypto:NOPEUSDT"
    assert body["detail"] == (
        "Instrument crypto:NOPEUSDT does not exist. Search the instruments to find a valid id."
    )


@pytest.mark.parametrize(
    ("instrument_id", "reason"),
    [
        ("BTCUSDT", "must be <market>:<symbol>"),
        ("forex:EURUSD", "Unknown market 'forex'"),
        ("crypto:BTC-USDT", "Symbol 'BTC-USDT' in instrument id 'crypto:BTC-USDT' is not valid"),
    ],
)
def test_instrument_id_validation(
    client: TestClient, service: FakeDataService, instrument_id: str, reason: str
) -> None:
    response = client.get(f"/api/v1/data/instruments/{instrument_id}")

    body = problem(response, 422, "validation")
    assert body["detail"].startswith("path parameter 'instrument_id': ")
    assert reason in body["detail"]
    assert body["errors"][0]["loc"] == ["path", "instrument_id"]
    assert service.calls == []


def test_instrument_ids_in_other_scripts(client: TestClient, service: FakeDataService) -> None:
    client.get("/api/v1/data/instruments/crypto:%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT")
    client.get("/api/v1/data/instruments/stock:brk.b")

    assert [call[1][0] for call in service.calls] == [
        InstrumentId(Market.CRYPTO, "币安人生USDT"),
        InstrumentId(Market.STOCK, "BRK.B"),
    ]


# --- icon -------------------------------------------------------------------------------------


def test_icon_logo(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments/stock:aapl/icon")

    assert response.status_code == 200
    assert response.content == AAPL_LOGO.content
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert service.calls == [("icon", (AAPL.id,))]


def test_icon_fallback(client: TestClient) -> None:
    response = client.get("/api/v1/data/instruments/crypto:BTCUSDT/icon")

    assert response.status_code == 200
    assert response.content == FALLBACK.content
    assert response.headers["content-type"] == "image/svg+xml"
    assert response.headers["cache-control"] == "public, max-age=3600"


def test_icon_of_an_unknown_instrument(client: TestClient) -> None:
    response = client.get("/api/v1/data/instruments/stock:NOPE/icon")

    body = problem(response, 404, "instrument-not-found")
    assert body["instrument"] == "stock:NOPE"


def test_icon_id_validation(client: TestClient, service: FakeDataService) -> None:
    response = client.get("/api/v1/data/instruments/forex:EURUSD/icon")

    problem(response, 422, "validation")
    assert service.calls == []


def test_icon_is_not_rate_limited(client: TestClient, limiter: FakeLimiter) -> None:
    limiter.wait = 30

    assert client.get("/api/v1/data/instruments/stock:AAPL/icon").status_code == 200
    assert limiter.calls == []


# --- candles ----------------------------------------------------------------------------------


def test_candles(client: TestClient, service: FakeDataService) -> None:
    response = client.get(
        "/api/v1/data/instruments/crypto:btcusdt/candles",
        params={
            "timeframe": "1h",
            "start": DAY_START,
            "end": DAY_END,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {
        "meta": {
            "instrument": "crypto:BTCUSDT",
            "timeframe": "1h",
            "start": DAY_START,
            "end": DAY_END,
            "source": "binance",
            "feed": "spot",
            "fingerprint": "sha256:" + "ab" * 32,
            "count": 3,
            "gaps": [[DAY_START + 7200, DAY_START + 10800]],
            "gaps_total": 1,
        },
        "t": [DAY_START, DAY_START + 3600, DAY_START + 10800],
        "o": [67491.0, 67612.5, 67580.1],
        "h": [67700.0, 67650.0, 67640.0],
        "l": [67420.2, 67510.0, 67488.8],
        "c": [67612.4, 67540.3, 67601.9],
        "v": [512.31, 398.07, 0.0],
    }
    # The orjson body is exactly what the documented model describes.
    CandlesOut.model_validate_json(response.content, strict=True)
    assert service.calls == [
        ("candles", (BTCUSDT.id, Timeframe.H1, DAY_START, DAY_END)),
    ]


@pytest.mark.parametrize("instrument", ["stock:BRK.B", "crypto:币安人生USDT"])
def test_candles_instrument_path(
    client: TestClient, service: FakeDataService, instrument: str
) -> None:
    response = client.get(
        f"/api/v1/data/instruments/{instrument}/candles",
        params={
            "instrument": "crypto:BTCUSDT",
            "timeframe": "1h",
            "start": DAY_START,
            "end": DAY_END,
        },
    )

    assert response.status_code == 200
    assert response.json()["meta"]["instrument"] == instrument
    assert service.calls == [
        (
            "candles",
            (InstrumentId.parse(instrument), Timeframe.H1, DAY_START, DAY_END),
        ),
    ]


@pytest.mark.parametrize("path", ["/api/v1/data/candles", "/api/v1/data/instruments//candles"])
def test_candles_requires_instrument_path(
    client: TestClient, service: FakeDataService, path: str
) -> None:
    response = client.get(
        path,
        params={"instrument": "stock:AAPL", "timeframe": "1h", "start": DAY_START},
    )

    problem(response, 404, "not-found")
    assert service.calls == []


def test_no_candles(client: TestClient, service: FakeDataService) -> None:
    found = candle_set(BTCUSDT.id, Timeframe.H1, DAY_START, DAY_END)
    service.result = replace(found, frame=found.frame.clear(), gaps=[(DAY_START, DAY_END)])

    body = client.get(
        f"/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=1h&start={DAY_START}"
        f"&end={DAY_END}"
    ).json()

    assert body["meta"]["count"] == 0
    assert body["meta"]["gaps"] == [[DAY_START, DAY_END]]
    assert body["t"] == body["o"] == body["h"] == body["l"] == body["c"] == body["v"] == []


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("1717372800", DAY_START),
        ("2024-06-03", DAY_START),
        ("2024-06-03T00:00:00Z", DAY_START),
        ("2024-06-03T00:00", DAY_START),
        ("2024-06-03 00:00:00", DAY_START),
        ("2024-06-03T02:00:00+02:00", DAY_START),
        ("2024-06-02T20:00:00-04:00", DAY_START),
        ("2024-06-03T00:00:00.999Z", DAY_START),
        ("2024-06-03T13:30:00Z", DAY_START + 13 * 3600 + 1800),
    ],
)
def test_start_formats(
    client: TestClient, service: FakeDataService, value: str, seconds: int
) -> None:
    response = client.get(
        "/api/v1/data/instruments/stock:AAPL/candles",
        params={"timeframe": "1d", "start": value, "end": DAY_END + 1},
    )

    assert response.status_code == 200
    assert service.calls[0][1][2] == seconds


def test_end_formats_and_default(
    client: TestClient, service: FakeDataService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("candlestack.data.api._time", lambda: DAY_END + 0.7)
    base = "/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&start=2024-06-03"

    client.get(f"{base}&end=2024-06-03T20:00:00Z")
    client.get(base)

    assert [call[1][3] for call in service.calls] == [DAY_START + 20 * 3600, DAY_END]


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        (
            "AAPL/candles?timeframe=1h&start=2024-06-03",
            (
                "path parameter 'instrument_id': Instrument id 'AAPL' must be <market>:<symbol>, "
                "for example crypto:BTCUSDT or stock:AAPL."
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=2h&start=2024-06-03",
            (
                "query parameter 'timeframe': "
                "Input should be '1m', '5m', '15m', '30m', '1h', '4h' or '1d'"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&limit=0",
            "query parameter 'limit': Input should be greater than or equal to 1",
        ),
        (
            "stock:AAPL/candles?timeframe=1h&limit=5001",
            "query parameter 'limit': Input should be less than or equal to 5000",
        ),
        (
            "stock:AAPL/candles?timeframe=1h&limit=many",
            (
                "query parameter 'limit': Input should be a valid integer, unable to parse "
                "string as an integer"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=2024-06-03&limit=10",
            (
                "query parameter 'limit': limit cannot be combined with start: send start and "
                "end, or limit and end"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=yesterday",
            (
                "query parameter 'start': 'yesterday' is neither epoch seconds nor an ISO 8601 "
                "date or date-time"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=-1717372800",
            (
                "query parameter 'start': '-1717372800' is neither epoch seconds nor an ISO 8601 "
                "date or date-time"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=2024-06-03T00:00:00+02:00",
            (
                "query parameter 'start': '2024-06-03T00:00:00 02:00' is neither epoch seconds "
                "nor an ISO 8601 date or date-time (in a URL, write + as %2B)"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=1717372800000",
            (
                "query parameter 'start': 1717372800000 is after the year 9999: send epoch "
                "seconds, not milliseconds"
            ),
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=2024-06-04&end=2024-06-03",
            f"query parameter 'end': end ({DAY_START}) must be after start ({DAY_END})",
        ),
        (
            "stock:AAPL/candles?timeframe=1h&start=2024-06-03&end=2024-06-03",
            f"query parameter 'end': end ({DAY_START}) must be after start ({DAY_START})",
        ),
    ],
)
def test_candles_validation(
    client: TestClient, service: FakeDataService, path: str, detail: str
) -> None:
    response = client.get(f"/api/v1/data/instruments/{path}")

    body = problem(response, 422, "validation")
    assert body["detail"] == detail
    assert service.calls == []


def test_start_with_limit_has_its_own_error_type(client: TestClient) -> None:
    response = client.get(f"{CANDLES}&limit=10")

    body = problem(response, 422, "validation")
    assert [(e["loc"], e["type"]) for e in body["errors"]] == [
        (["query", "limit"], "limit_with_start")
    ]


def test_last_candles_default_limit(
    client: TestClient, service: FakeDataService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("candlestack.data.api._time", lambda: DAY_END + 0.7)

    response = client.get("/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=15m")

    assert response.status_code == 200
    assert response.json()["meta"]["start"] == DAY_END - 500 * 900  # what the service picked
    assert service.calls == [("last_candles", (BTCUSDT.id, Timeframe.M15, DAY_END, 500))]


def test_default_limit_fits_a_low_candles_max(
    app: FastAPI, client: TestClient, service: FakeDataService
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"candles_max": 120})

    client.get("/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=1h")

    assert service.calls[0][1][3] == 120


@pytest.mark.parametrize("limit", [1, 5000])
def test_last_candles_before_end(client: TestClient, service: FakeDataService, limit: int) -> None:
    response = client.get(
        "/api/v1/data/instruments/stock:AAPL/candles",
        params={"timeframe": "1h", "end": "2024-06-03T13:30Z", "limit": limit},
    )

    assert response.status_code == 200
    assert response.json()["meta"]["start"] == 1717421400 - limit * 3600
    assert service.calls == [("last_candles", (AAPL.id, Timeframe.H1, 1717421400, limit))]


def test_start_in_the_future(
    client: TestClient, service: FakeDataService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("candlestack.data.api._time", lambda: DAY_START)

    response = client.get(
        f"/api/v1/data/instruments/stock:AAPL/candles?timeframe=1h&start={DAY_START}"
    )

    assert problem(response, 422, "validation")["detail"] == (
        f"query parameter 'end': end ({DAY_START}, now) must be after start ({DAY_START})"
    )


def test_all_parameter_errors_at_once(client: TestClient) -> None:
    response = client.get("/api/v1/data/instruments/AAPL/candles?timeframe=2h&start=soon")

    locs = [error["loc"] for error in problem(response, 422, "validation")["errors"]]
    assert locs == [["path", "instrument_id"], ["query", "timeframe"], ["query", "start"]]


# --- error mapping ----------------------------------------------------------------------------

CANDLES = f"/api/v1/data/instruments/crypto:BTCUSDT/candles?timeframe=1m&start={DAY_START}"


def test_period_out_of_range(client: TestClient, service: FakeDataService) -> None:
    service.error = PeriodOutOfRange(
        "crypto:BTCUSDT", "1m", 1502942400, 1790424000, start=1400000000
    )

    body = problem(client.get(CANDLES), 422, "period-out-of-range")

    assert body == {
        "type": PROBLEMS + "period-out-of-range",
        "title": "Period out of range",
        "status": 422,
        "detail": "crypto:BTCUSDT has 1m candles from 2017-08-17T04:00:00Z (1502942400). "
        "Set start to 1502942400 or later.",
        "instrument": "crypto:BTCUSDT",
        "timeframe": "1m",
        "available_from": 1502942400,
        "available_to": 1790424000,
    }


def test_too_many_candles(client: TestClient, service: FakeDataService) -> None:
    suggestion = (
        "Use 5m or a larger timeframe, or end the period at 2024-02-04T17:20:00Z (1707067200) "
        "or earlier."
    )
    service.error = TooManyCandles(131040, 50000, suggestion)

    body = problem(client.get(CANDLES), 422, "too-many-candles")

    assert body == {
        "type": PROBLEMS + "too-many-candles",
        "title": "Too many candles",
        "status": 422,
        "detail": f"Requested 131040 candles, the maximum is 50000. {suggestion}",
        "requested": 131040,
        "maximum": 50000,
        "suggestion": suggestion,
    }


def test_invalid_request_from_the_service(client: TestClient, service: FakeDataService) -> None:
    service.error = InvalidRequest("stock:AAPL has no 1m candles before 2016-01-04.")

    body = problem(client.get(CANDLES), 422, "validation")

    assert body["title"] == "Invalid request"
    assert body["detail"] == "stock:AAPL has no 1m candles before 2016-01-04."


def test_unknown_instrument_in_candles(client: TestClient, service: FakeDataService) -> None:
    service.error = InstrumentNotFound("crypto:BTCUSDT")

    assert problem(client.get(CANDLES), 404, "instrument-not-found")["instrument"] == (
        "crypto:BTCUSDT"
    )


def test_source_budget_spent(client: TestClient, service: FakeDataService) -> None:
    service.error = SourceUnavailable(
        "alpaca", "Our Alpaca request budget is spent. Try again in 20 s.", retry_after=20
    )

    response = client.get(CANDLES)

    body = problem(response, 503, "source-unavailable")
    assert body["title"] == "Data source unavailable"
    assert body["detail"] == "Our Alpaca request budget is spent. Try again in 20 s."
    assert body["source"] == "alpaca"
    assert response.headers["retry-after"] == "20"


def test_invalid_source_data(
    client: TestClient, service: FakeDataService, caplog: pytest.LogCaptureFixture
) -> None:
    # What failed (here a library message) goes to the log, not to the client.
    service.error = DataIntegrityError(
        'Alpaca bars cannot be read: could not append value: "n/a" of type: str to the '
        "builder; consider increasing `infer_schema_length`",
        source="alpaca",
    )

    body = problem(client.get(CANDLES), 502, "source-data-invalid")

    assert body["source"] == "alpaca"
    assert body["detail"] == (
        "Alpaca sent data for this request that failed validation, so it was not used. "
        "Try again later, or ask for another period."
    )
    assert "infer_schema_length" in caplog.text


def test_invalid_data_of_no_known_source(client: TestClient, service: FakeDataService) -> None:
    service.error = DataIntegrityError("Malformed trading calendar day {'date': 'x'}")

    body = problem(client.get(CANDLES), 502, "source-data-invalid")

    assert body["source"] is None
    assert body["detail"].startswith("The data source sent data for this request that failed")


def test_unknown_data_error_is_a_500(client: TestClient, service: FakeDataService) -> None:
    service.error = DataError("Something new went wrong.")

    problem(client.get(CANDLES), 500, "internal-error")


# --- client rate limit ------------------------------------------------------------------------


def test_rate_limit_key_is_the_cloudflare_client_ip(
    client: TestClient, limiter: FakeLimiter
) -> None:
    client.get(CANDLES, headers={"CF-Connecting-IP": "203.0.113.7"})
    client.get(CANDLES)

    assert limiter.calls == [
        ("data:rl:client:203.0.113.7", 60),
        ("data:rl:client:testclient", 60),
    ]


def test_rate_limited(client: TestClient, service: FakeDataService, limiter: FakeLimiter) -> None:
    limiter.wait = 12.2

    response = client.get(CANDLES)

    body = problem(response, 429, "rate-limited")
    assert response.headers["retry-after"] == "13"
    assert body == {
        "type": PROBLEMS + "rate-limited",
        "title": "Too many requests",
        "status": 429,
        "detail": "More than 60 candle and instrument requests in one minute from this "
        "address. Retry in 13 seconds.",
        "limit": 60,
    }
    assert service.calls == []


def test_rate_limit_comes_before_validation(client: TestClient, limiter: FakeLimiter) -> None:
    limiter.wait = 0.2

    response = client.get("/api/v1/data/instruments/nonsense/candles")

    problem(response, 429, "rate-limited")
    assert response.headers["retry-after"] == "1"


@pytest.mark.parametrize(
    ("address", "key"),
    [
        ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),
        ("2001:DB8:1:2::ABCD", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
    ],
)
def test_rate_limit_key_of_an_ipv6_client_is_its_64_network(
    client: TestClient, limiter: FakeLimiter, address: str, key: str
) -> None:
    client.get(CANDLES, headers={"CF-Connecting-IP": address})

    assert limiter.calls == [(f"data:rl:client:{key}", 60)]


def test_instrument_detail_is_rate_limited(
    client: TestClient, service: FakeDataService, limiter: FakeLimiter
) -> None:
    # An uncached detail asks the source for the first candle, from the environment's budget.
    client.get("/api/v1/data/instruments/stock:AAPL", headers={"CF-Connecting-IP": "203.0.113.7"})
    limiter.wait = 30

    response = client.get("/api/v1/data/instruments/stock:AAPL")

    problem(response, 429, "rate-limited")
    assert response.headers["retry-after"] == "30"
    assert limiter.calls == [("data:rl:client:203.0.113.7", 60), ("data:rl:client:testclient", 60)]
    assert service.calls == [("instrument", (AAPL.id,))]


def test_search_is_not_rate_limited(client: TestClient, limiter: FakeLimiter) -> None:
    limiter.wait = 30

    assert client.get("/api/v1/data/instruments?q=btc").status_code == 200
    assert limiter.calls == []


class TestRateLimitDisabled:
    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(app_env="test", client_rate_limit=0)

    def test_no_limit(self, client: TestClient, limiter: FakeLimiter) -> None:
        limiter.wait = 30

        assert client.get(CANDLES).status_code == 200
        assert limiter.calls == [("data:rl:client:testclient", 0)]


# --- sources health ---------------------------------------------------------------------------


def test_sources_health(client: TestClient) -> None:
    response = client.get("/api/health/sources")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "sources": {
            "binance": {"status": "ok", "latency_ms": 84, "detail": None},
            "alpaca": {"status": "ok", "latency_ms": 131, "detail": None},
        },
    }


def test_sources_health_with_a_source_down(client: TestClient, service: FakeDataService) -> None:
    service.health["alpaca"] = SourceHealth(ok=False, latency_ms=None, detail="HTTP 401")

    response = client.get("/api/health/sources")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/json"
    body = response.json()
    assert body["status"] == "error"
    assert body["sources"]["binance"]["status"] == "ok"
    assert body["sources"]["alpaca"] == {
        "status": "error",
        "latency_ms": None,
        "detail": "HTTP 401",
    }


# --- OpenAPI ----------------------------------------------------------------------------------


def test_openapi_documents_the_data_api(client: TestClient) -> None:
    schema = client.get("/api/v1/openapi.json").json()
    paths = schema["paths"]

    assert "/api/v1/data/candles" not in paths
    candles = paths["/api/v1/data/instruments/{instrument_id}/candles"]["get"]
    assert [
        (parameter["name"], parameter["in"], parameter["required"])
        for parameter in candles["parameters"]
    ] == [
        ("instrument_id", "path", True),
        ("timeframe", "query", True),
        ("start", "query", False),
        ("end", "query", False),
        ("limit", "query", False),
    ]
    assert candles["tags"] == ["data"]
    assert candles["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CandlesOut"
    }
    assert set(candles["responses"]) == {"200", "401", "404", "422", "429", "502", "503"}
    for status in ("401", "404", "422", "429", "502", "503"):
        assert list(candles["responses"][status]["content"]) == [PROBLEM]
    assert "too-many-candles" in candles["responses"]["422"]["description"]
    detail = paths["/api/v1/data/instruments/{instrument_id}"]["get"]
    assert set(detail["responses"]) == {"200", "401", "404", "422", "429", "502", "503"}
    search = paths["/api/v1/data/instruments"]["get"]
    for operation in (search, detail, candles):
        assert "not-signed-in" in operation["responses"]["401"]["description"]
        assert "Needs a signed-in account." in operation["description"]
    assert set(paths) >= {
        "/api/v1/data/instruments",
        "/api/v1/data/instruments/{instrument_id}",
        "/api/health/sources",
    }
    assert paths["/api/health/sources"]["get"]["tags"] == ["health"]


def test_openapi_documents_the_public_data_routes(client: TestClient) -> None:
    paths = client.get("/api/v1/openapi.json").json()["paths"]

    icon = paths["/api/v1/data/instruments/{instrument_id}/icon"]["get"]
    top = paths["/api/v1/data/top-instruments"]["get"]
    assert set(top["responses"]) == {"200", "502", "503"}
    assert top["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TopInstrumentsOut"
    }
    for operation in (icon, top):
        assert "401" not in operation["responses"]
        assert "Public" in operation["description"]


def test_candles_json_matches_the_documented_model() -> None:
    body = candle_set(BTCUSDT.id, Timeframe.H1, DAY_START, DAY_END)

    parsed = CandlesOut.model_validate(orjson.loads(candles_json(body)))

    assert parsed.meta.count == 3
    assert parsed.l == [67420.2, 67510.0, 67488.8]
