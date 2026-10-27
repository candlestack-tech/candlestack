"""Fixtures of the data integration tests: a real Redis at REDIS_URL, flushed before each test
(it is a cache, nothing in it matters), and the sources mocked with respx at fake hosts.

Test modules cannot import this file, so the helpers they need hang on the ``upstream``
fixture (``Upstream``).
"""

import hashlib
import io
import json
import zipfile
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from redis.asyncio import Redis

from candlestack.core import Settings, create_redis
from candlestack.core import http as http_module
from candlestack.data import DataService, build_data_service, firsts
from candlestack.data.sources import base

FIXTURES = Path(__file__).parents[2] / "fixtures"
BINANCE_API = "https://api.binance.test"
BINANCE_DATA = "https://data.binance.test"
ALPACA_API = "https://api.alpaca.test"
ALPACA_DATA = "https://data.alpaca.test"
LOGO_DEV = "https://img.logo.test"
HOUR_MS = 3_600_000


def fixture_bytes(path: str) -> bytes:
    return (FIXTURES / path).read_bytes()


def kline(open_ms: int, interval_ms: int = HOUR_MS, price: float = 100.0) -> list[Any]:
    """A REST kline row; times in milliseconds."""
    return [
        open_ms,
        f"{price:.8f}",
        f"{price + 1:.8f}",
        f"{price - 1:.8f}",
        f"{price + 0.5:.8f}",
        "1.50000000",
        open_ms + interval_ms - 1,
        "150.0",
        10,
        "0.7",
        "70.0",
        "0",
    ]


def make_archive(name: str, opens: list[int], interval: int, price: float = 100.0) -> bytes:
    """A kline archive like data.binance.vision's: a zip with one CSV without a header;
    ``opens`` and ``interval`` in the file's unit (ms before 2025, us from 2025)."""
    lines = []
    for opens_at in opens:
        row = kline(opens_at, interval, price)
        lines.append(",".join(str(value) for value in row))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name.removesuffix(".zip") + ".csv", "\n".join(lines) + "\n")
    return buffer.getvalue()


def checksum(content: bytes, name: str) -> str:
    return f"{hashlib.sha256(content).hexdigest()}  {name}"


class Upstream:
    """Routes of the mocked sources, and helpers to build their responses."""

    BINANCE_API = BINANCE_API
    BINANCE_DATA = BINANCE_DATA
    ALPACA_API = ALPACA_API
    ALPACA_DATA = ALPACA_DATA
    LOGO_DEV = LOGO_DEV
    fixture_bytes = staticmethod(fixture_bytes)
    kline = staticmethod(kline)
    make_archive = staticmethod(make_archive)
    checksum = staticmethod(checksum)

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router

    # Binance

    def exchange_info(self, body: bytes | None = None) -> respx.Route:
        body = body or fixture_bytes("binance/rest/exchangeInfo-trading.json")
        return self.router.get(f"{BINANCE_API}/api/v3/exchangeInfo").respond(200, content=body)

    def ticker(self, body: bytes | None = None) -> respx.Route:
        """The 24 h ticker of every trading pair."""
        body = body or fixture_bytes("binance/rest/ticker-24hr-trading.json")
        return self.router.get(
            f"{BINANCE_API}/api/v3/ticker/24hr", params={"symbolStatus": "TRADING"}
        ).respond(200, content=body)

    def first_kline(self, open_ms: int) -> respx.Route:
        """The first-candle lookup, which asks for the first 1m kline."""
        return self.router.get(
            f"{BINANCE_API}/api/v3/klines",
            params={"interval": "1m", "startTime": "0", "limit": "1"},
        ).respond(200, json=[kline(open_ms, 60_000)])

    def klines(self, start_ms: int, rows: list[list[Any]]) -> respx.Route:
        return self.router.get(
            f"{BINANCE_API}/api/v3/klines", params={"startTime": str(start_ms)}
        ).respond(200, json=rows)

    def archive(
        self, kind: str, name: str, content: bytes | None, sha256: str | None = None
    ) -> tuple[respx.Route, respx.Route]:
        """An archive (``kind`` monthly or daily) and its checksum; ``None`` content is 404."""
        symbol, timeframe = name.split("-")[:2]
        url = f"{BINANCE_DATA}/data/spot/{kind}/klines/{symbol}/{timeframe}/{name}"
        if content is None:
            return (
                self.router.get(url).respond(404),
                self.router.get(url + ".CHECKSUM").respond(404),
            )
        return (
            self.router.get(url).respond(200, content=content),
            self.router.get(url + ".CHECKSUM").respond(200, text=sha256 or checksum(content, name)),
        )

    def fixture_archive(self, kind: str, name: str) -> tuple[respx.Route, respx.Route]:
        return self.archive(
            kind,
            name,
            fixture_bytes(f"binance/archive/{kind}/{name}"),
            fixture_bytes(f"binance/archive/{kind}/{name}.CHECKSUM").decode(),
        )

    # Alpaca

    def assets(self) -> respx.Route:
        return self.router.get(f"{ALPACA_API}/v2/assets").respond(
            200, content=fixture_bytes("alpaca/assets.json")
        )

    def calendar(self) -> respx.Route:
        return self.router.get(f"{ALPACA_API}/v2/calendar").respond(
            200, content=fixture_bytes("alpaca/calendar-2024.json")
        )

    def snapshots(self) -> respx.Route:
        """Snapshots of the requested symbols (``symbols``) out of the sample file, with
        Alpaca's rate-limit headers."""
        sample = json.loads(fixture_bytes("alpaca/snapshots-sample.json"))

        def answer(request: httpx.Request) -> httpx.Response:
            symbols = request.url.params["symbols"].split(",")
            return httpx.Response(
                200,
                json={symbol: sample[symbol] for symbol in symbols if symbol in sample},
                headers={"X-RateLimit-Limit": "200", "X-RateLimit-Remaining": "187"},
            )

        return self.router.get(f"{ALPACA_DATA}/v2/stocks/snapshots").mock(side_effect=answer)

    def first_bar(self) -> respx.Route:
        return self.router.get(
            f"{ALPACA_DATA}/v2/stocks/bars",
            params={"symbols": "AAPL", "timeframe": "1Day", "limit": "1"},
        ).respond(200, content=fixture_bytes("alpaca/bars-AAPL-1Day-earliest.json"))

    def bars(self, timeframe: str, start: str, *pages: str) -> respx.Route:
        """Bars from ``start`` (RFC 3339), one fixture file per page."""
        return self.router.get(
            f"{ALPACA_DATA}/v2/stocks/bars", params={"timeframe": timeframe, "start": start}
        ).mock(
            side_effect=[
                httpx.Response(200, content=fixture_bytes(f"alpaca/{page}")) for page in pages
            ]
        )

    def native_bars(self, timeframe: str, start: str, *pages: str) -> respx.Route:
        """Bars of ``timeframe`` (``5Min``, ``15Min`` or ``30Min``) from ``start`` (RFC 3339),
        built from the 1Min fixture pages as Alpaca builds them: bins on the clock's multiples of
        the timeframe, extended hours included, in one page."""
        step = {"5Min": 300, "15Min": 900, "30Min": 1800}[timeframe]
        bins: dict[int, dict[str, Any]] = {}
        for bar in self.aapl_bars(*pages):
            ts = int(datetime.fromisoformat(bar["t"]).timestamp())
            opens = ts // step * step
            if opens not in bins:
                bins[opens] = {"t": f"{datetime.fromtimestamp(opens, UTC):%Y-%m-%dT%H:%M:%SZ}"}
                bins[opens].update(o=bar["o"], h=bar["h"], l=bar["l"], c=bar["c"], v=0)
            native = bins[opens]
            native.update(h=max(native["h"], bar["h"]), l=min(native["l"], bar["l"]), c=bar["c"])
            native["v"] += bar["v"]
        return self.router.get(
            f"{ALPACA_DATA}/v2/stocks/bars", params={"timeframe": timeframe, "start": start}
        ).respond(200, json={"bars": {"AAPL": list(bins.values())}, "next_page_token": None})

    def bars_json(self, timeframe: str, start: str, bars: list[dict[str, Any]]) -> respx.Route:
        return self.router.get(
            f"{ALPACA_DATA}/v2/stocks/bars", params={"timeframe": timeframe, "start": start}
        ).respond(200, json={"bars": {"AAPL": bars}, "next_page_token": None})

    # logo.dev

    def logo(self, path: str) -> respx.Route:
        """The logo at ``path`` (``ticker/AAPL``, ``crypto/BTC``); the caller sets the answer."""
        return self.router.get(f"{LOGO_DEV}/{path}")

    @staticmethod
    def aapl_bars(*paths: str) -> list[dict[str, Any]]:
        return [
            bar
            for path in paths
            for bar in json.loads(fixture_bytes(f"alpaca/{path}"))["bars"]["AAPL"]
        ]


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = create_redis(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        app_env="test",
        binance_api_url=BINANCE_API,
        binance_data_url=BINANCE_DATA,
        alpaca_api_url=ALPACA_API,
        alpaca_data_url=ALPACA_DATA,
        alpaca_key_id="test-key-id",
        alpaca_secret_key="test-secret",
        logo_dev_url=LOGO_DEV,
        logo_dev_key="test-logo-key",
    )


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def service(settings: Settings, redis: Redis, http: httpx.AsyncClient) -> DataService:
    return build_data_service(settings, redis, http)


@pytest.fixture
def upstream() -> Iterator[Upstream]:
    with respx.mock(assert_all_called=False) as router:
        yield Upstream(router)


@pytest.fixture(autouse=True)
def no_first_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    """A search looks up no first candles in the background, so that the mocked sources see
    only the requests a test makes; test_firsts.py turns the lookups on."""
    monkeypatch.setattr(firsts, "WARM_PER_PAGE", 0)


@pytest.fixture(autouse=True)
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Records the retry and budget waits instead of sleeping."""
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(http_module, "_sleep", sleep)
    monkeypatch.setattr(base, "_sleep", sleep)
    return delays
