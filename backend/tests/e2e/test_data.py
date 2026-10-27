"""The data API of the built image against the fake sources (mock_sources.py, tests/fixtures),
signed in with the dev sign-in: the market data is for signed-in accounts only.

Expected values come from the fixtures: BTCUSDT archives of 2024-01 (1h, 1d) and 2025-01 (1h,
microsecond timestamps), AAPL IEX bars of 2024-06-03 and 2024-06-04 (1m) and June 2024 (1d).
"""

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.e2e

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}
JAN_2024, FEB_2024 = 1704067200, 1706745600
# 2024-06-03 and 2024-06-04 00:00 UTC; the session opens 09:30 New York = 13:30 UTC (EDT).
JUNE_3, JUNE_4 = 1717372800, 1717459200
JUNE_3_OPEN = JUNE_3 + 13 * 3600 + 1800


@pytest.fixture(scope="module")
def http(base_url: str) -> Iterator[httpx.Client]:
    """One session for the module: the dev sign-in takes 20 sign-ins a minute per address."""
    with httpx.Client(base_url=base_url, timeout=10) as client:
        assert client.post("/api/v1/auth/dev", headers=ORIGIN).status_code == 204
        yield client


def candles(http: httpx.Client, *, instrument: str, **params: Any) -> httpx.Response:
    return http.get(f"/api/v1/data/instruments/{instrument}/candles", params=params)


def app_ms(response: httpx.Response) -> float:
    """The app's own time from ``Server-Timing: app;dur=<ms>``."""
    return float(response.headers["server-timing"].removeprefix("app;dur="))


def test_sources_health(http: httpx.Client) -> None:
    response = http.get("/api/health/sources")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert {name: check["status"] for name, check in body["sources"].items()} == {
        "binance": "ok",
        "alpaca": "ok",
    }


@pytest.mark.parametrize(
    ("q", "found"),
    [("btc", "crypto:BTCUSDT"), ("apple", "stock:AAPL"), ("brkb", "stock:BRK.B")],
)
def test_search(http: httpx.Client, q: str, found: str) -> None:
    response = http.get("/api/v1/data/instruments", params={"q": q})

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == found


def test_search_leaves_out_what_is_not_tradable(http: httpx.Client) -> None:
    # HEINY is OTC, MLGO not tradable (fixture assets.json).
    for q in ("heiny", "mlgo"):
        assert http.get("/api/v1/data/instruments", params={"q": q}).json()["count"] == 0


def test_crypto_instrument(http: httpx.Client) -> None:
    response = http.get("/api/v1/data/instruments/crypto:btcusdt")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "crypto:BTCUSDT"
    assert (body["source"], body["feed"], body["base"], body["quote"]) == (
        "binance",
        "spot",
        "BTC",
        "USDT",
    )
    assert body["timeframes"] == ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
    assert body["available_from"] == 1502942400  # first BTCUSDT 1m candle, 2017-08-17T04:00Z
    assert body["max_candles"] == 50000


def test_stock_instrument(http: httpx.Client) -> None:
    body = http.get("/api/v1/data/instruments/stock:AAPL").json()

    assert (body["source"], body["feed"], body["exchange"]) == ("alpaca", "sip", "NASDAQ")
    # SIP history starts on 2016-01-04: its session opens at 14:30 UTC.
    assert body["available_from"] == 1451917800


def test_unknown_instrument(http: httpx.Client) -> None:
    response = http.get("/api/v1/data/instruments/crypto:NOPEUSDT")

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "instrument-not-found"


@pytest.mark.parametrize(
    ("instrument", "media_type"),
    [
        ("stock:AAPL", "image/png"),
        ("crypto:BTCUSDT", "image/png"),
        ("crypto:ETHUSDT", "image/svg+xml"),
    ],
)
def test_icon(http: httpx.Client, instrument: str, media_type: str) -> None:
    response = http.get(f"/api/v1/data/instruments/{instrument}/icon")

    assert response.status_code == 200
    assert response.headers["content-type"] == media_type
    assert response.headers["cache-control"].startswith("public, max-age=")
    assert response.content


def test_icon_of_an_unknown_instrument(http: httpx.Client) -> None:
    response = http.get("/api/v1/data/instruments/crypto:NOPEUSDT/icon")

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "instrument-not-found"


def test_crypto_1h_of_a_month(http: httpx.Client) -> None:
    response = candles(
        http, instrument="crypto:BTCUSDT", timeframe="1h", start="2024-01-01", end="2024-02-01"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["meta"] | {"fingerprint": None} == {
        "instrument": "crypto:BTCUSDT",
        "timeframe": "1h",
        "start": JAN_2024,
        "end": FEB_2024,
        "source": "binance",
        "feed": "spot",
        "fingerprint": None,
        "count": 744,
        "gaps": [],
        "gaps_total": 0,
    }
    assert body["meta"]["fingerprint"].startswith("sha256:")
    assert body["t"] == list(range(JAN_2024, FEB_2024, 3600))
    # First row of BTCUSDT-1h-2024-01.zip.
    assert (body["o"][0], body["h"][0], body["l"][0], body["c"][0], body["v"][0]) == (
        42283.58,
        42554.57,
        42261.02,
        42475.23,
        1271.68108,
    )


def test_crypto_1d(http: httpx.Client) -> None:
    body = candles(
        http, instrument="crypto:BTCUSDT", timeframe="1d", start=JAN_2024, end=FEB_2024
    ).json()

    assert body["meta"]["count"] == 31
    assert body["t"] == list(range(JAN_2024, FEB_2024, 86400))


def test_stock_1h_of_a_session(http: httpx.Client) -> None:
    response = candles(
        http, instrument="stock:AAPL", timeframe="1h", start="2024-06-03", end="2024-06-04"
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["meta"]["source"], body["meta"]["feed"]) == ("alpaca", "sip")
    # 09:30, 10:30, ..., 15:30 New York: the last candle is the 30 minutes up to the close.
    assert body["t"] == [JUNE_3_OPEN + hour * 3600 for hour in range(7)]
    assert body["meta"]["gaps"] == []
    assert body["o"][0] == 191.16  # the first regular 1m bar of the day


def test_stock_1d(http: httpx.Client) -> None:
    body = candles(
        http, instrument="stock:AAPL", timeframe="1d", start="2024-06-03", end="2024-06-05"
    ).json()

    # Alpaca's 1Day bars, labelled with the session open.
    assert body["t"] == [JUNE_3_OPEN, JUNE_3_OPEN + 86400]
    assert (body["o"][0], body["h"][0], body["l"][0], body["c"][0]) == (
        191.16,
        193.15,
        190.78,
        192.16,
    )


def test_too_many_candles(http: httpx.Client) -> None:
    response = candles(
        http, instrument="crypto:BTCUSDT", timeframe="1m", start="2024-01-01", end="2024-03-01"
    )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == PROBLEMS + "too-many-candles"
    assert (body["requested"], body["maximum"]) == (86400, 50000)
    assert body["suggestion"].startswith("Use 5m or a larger timeframe")


def test_period_out_of_range(http: httpx.Client) -> None:
    response = candles(
        http, instrument="crypto:BTCUSDT", timeframe="1h", start="2017-01-01", end="2017-02-01"
    )

    assert response.status_code == 422
    body = response.json()
    assert body["type"] == PROBLEMS + "period-out-of-range"
    assert body["available_from"] == 1502942400
    assert "Set start to 1502942400 or later." in body["detail"]


def test_second_call_comes_from_the_cache(http: httpx.Client) -> None:
    params = {"instrument": "crypto:BTCUSDT", "timeframe": "1h", "start": "2025-01-01"}
    params["end"] = "2025-02-01"

    cold = candles(http, **params)
    warm = candles(http, **params)

    assert cold.status_code == warm.status_code == 200
    assert cold.json()["meta"]["count"] == 744  # microsecond timestamps in 2025 archives
    assert warm.content == cold.content
    assert app_ms(warm) < app_ms(cold)
