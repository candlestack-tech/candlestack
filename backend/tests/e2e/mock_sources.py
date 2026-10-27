"""Fake Binance, Alpaca and logo.dev for the e2e tests, built from the committed fixtures
(stdlib only).

One server answers the paths of all five upstreams, which do not overlap, so the backend's
``BINANCE_API_URL``, ``BINANCE_DATA_URL``, ``ALPACA_API_URL``, ``ALPACA_DATA_URL`` and
``LOGO_DEV_URL`` all point at it:

- ``api.binance.com``: ``/api/v3/ping``, ``/api/v3/exchangeInfo``, ``/api/v3/klines`` (rows of
  the fixture archives; ``startTime=0`` is the first-candle lookup and gets the recorded
  ``klines-<SYMBOL>-<interval>-first.json`` of the requested interval)
- ``data.binance.vision``: ``/data/spot/{monthly,daily}/klines/...`` (the fixture archives and
  their ``.CHECKSUM`` files, 404 for any other)
- ``paper-api.alpaca.markets``: ``/v2/assets``, ``/v2/calendar``, ``/v2/clock``
- ``data.alpaca.markets``: ``/v2/stocks/bars`` and ``/v2/stocks/{symbol}/bars`` (the bars of
  every ``bars-<SYMBOL>-<timeframe>-*.json`` fixture, filtered by ``start``/``end``, both
  inclusive like Alpaca, and paginated by ``limit``; ``5Min``, ``15Min`` and ``30Min`` bars are
  built from the ``1Min`` ones)
- ``img.logo.dev``: ``/ticker/{symbol}`` and ``/crypto/{base asset}``, a 1x1 PNG for the symbols
  in ``LOGOS``, 404 for any other, 401 without the key ``e2e-logo-key``

Fixture files are found by name anywhere under ``FIXTURES``. Every answer is delayed by
``LATENCY_MS`` to make a source call cost what it costs over the internet.

    FIXTURES=backend/tests/fixtures PORT=18103 python backend/tests/e2e/mock_sources.py
"""

import base64
import csv
import io
import json
import os
import re
import time
import zipfile
from collections.abc import Iterable
from datetime import UTC, date, datetime
from email.message import Message
from functools import cache
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

FIXTURES = Path(os.environ.get("FIXTURES", "/fixtures"))
PORT = int(os.environ.get("PORT", "8080"))
LATENCY = float(os.environ.get("LATENCY_MS", "0")) / 1000

FILES = {path.name: path for path in FIXTURES.rglob("*") if path.is_file()}
ARCHIVE = re.compile(
    r"/data/spot/(?P<kind>monthly|daily)/klines/(?P<symbol>[^/]+)/(?P<tf>[^/]+)/(?P<name>[^/]+)"
)
ARCHIVE_NAME = re.compile(r"(?P<symbol>.+)-(?P<tf>\d+[mhd])-(?P<label>\d{4}-\d{2}(-\d{2})?)\.zip")
BARS_NAME = re.compile(r"bars-(?P<symbol>[A-Z.]+)-(?P<tf>\d+(Min|Hour|Day))-(?P<rest>.+)\.json")
KLINES_MAX = 1000
LOGO_KEY = "e2e-logo-key"
LOGOS = {"/ticker/AAPL", "/crypto/BTC"}
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
BARS_DEFAULT, BARS_MAX = 1000, 10000


class Answer(Exception):
    """Ends a request with this status, body and content type."""

    def __init__(self, status: int, body: bytes | str | Any, content_type: str = "") -> None:
        if isinstance(body, str):
            body = body.encode()
        elif not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
            content_type = content_type or "application/json"
        self.status = status
        self.body = body
        self.content_type = content_type or "text/plain"


def fixture(name: str) -> bytes:
    if name not in FILES:
        raise Answer(500, f"fixture {name} is missing")
    return FILES[name].read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture(name))


# --- Binance --------------------------------------------------------------------------------


def archive(path: str) -> None:
    """A fixture archive or its ``.CHECKSUM`` at its data.binance.vision path, else 404."""
    if match := ARCHIVE.fullmatch(path):
        name = unquote(match["name"])
        parts = ARCHIVE_NAME.fullmatch(name.removesuffix(".CHECKSUM"))
        if (
            parts
            and name in FILES
            and parts["symbol"] == unquote(match["symbol"])
            and parts["tf"] == match["tf"]
            and (len(parts["label"]) == len("2024-01-01")) == (match["kind"] == "daily")
        ):
            raise Answer(200, fixture(name), "application/zip")
    raise Answer(404, "<Error><Code>NoSuchKey</Code></Error>", "application/xml")


def _milliseconds(value: str) -> int:
    number = int(value)
    return number // 1000 if number >= 10**15 else number


@cache
def kline_rows(symbol: str, interval: str) -> list[list[Any]]:
    """REST rows (times in ms) of every fixture archive of the symbol and interval."""
    rows = {}
    for name, path in FILES.items():
        parts = ARCHIVE_NAME.fullmatch(name)
        if not parts or parts["symbol"] != symbol or parts["tf"] != interval:
            continue
        with zipfile.ZipFile(path) as zipped:
            text = zipped.read(zipped.namelist()[0]).decode()
        for row in csv.reader(io.StringIO(text)):
            opens = _milliseconds(row[0])
            rows[opens] = [opens, *row[1:6], _milliseconds(row[6]), row[7], int(row[8]), *row[9:]]
    return [rows[opens] for opens in sorted(rows)]


def trading_symbols() -> set[str]:
    return {item["symbol"] for item in fixture_json("exchangeInfo-trading.json")["symbols"]}


def klines(query: dict[str, str]) -> None:
    symbol, interval = query.get("symbol", ""), query.get("interval", "")
    if symbol not in trading_symbols():
        raise Answer(400, {"code": -1121, "msg": "Invalid symbol."})
    start = int(query.get("startTime", 0))
    end = int(query.get("endTime", 2**62))
    limit = min(int(query.get("limit", 500)), KLINES_MAX)
    first = f"klines-{symbol}-{interval}-first.json"
    if start == 0 and first in FILES:
        raise Answer(200, fixture_json(first)[:limit])
    rows = [row for row in kline_rows(symbol, interval) if start <= row[0] <= end]
    raise Answer(200, rows[:limit])


def binance(path: str, query: dict[str, str]) -> None:
    if path == "/api/v3/ping":
        raise Answer(200, {})
    if path == "/api/v3/exchangeInfo":
        raise Answer(200, fixture("exchangeInfo-trading.json"), "application/json;charset=UTF-8")
    if path == "/api/v3/klines":
        klines(query)
    raise Answer(404, {"code": -1, "msg": f"no fake for {path}"})


# --- Alpaca ---------------------------------------------------------------------------------


def _time(value: str) -> datetime:
    """An Alpaca time parameter: RFC 3339 or a date."""
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), datetime.min.time(), UTC)
    return datetime.fromisoformat(value)


@cache
def bars_of(symbol: str, timeframe: str) -> list[dict[str, Any]]:
    """Every fixture bar of the symbol and timeframe (adjusted ones only), oldest first.
    ``5Min``, ``15Min`` and ``30Min`` bars are built from the ``1Min`` ones as Alpaca builds
    them: in bins on the clock's multiples of the timeframe, extended hours included."""
    if minutes := re.fullmatch(r"(5|15|30)Min", timeframe):
        return _aggregated(bars_of(symbol, "1Min"), int(minutes[1]) * 60)
    bars = {}
    for name in FILES:
        parts = BARS_NAME.fullmatch(name)
        if not parts or parts["tf"] != timeframe or "raw" in parts["rest"]:
            continue
        for bar in fixture_json(name)["bars"].get(symbol) or []:
            bars[bar["t"]] = bar
    return [bars[t] for t in sorted(bars)]


def _aggregated(bars: list[dict[str, Any]], step: int) -> list[dict[str, Any]]:
    built: dict[int, dict[str, Any]] = {}
    for bar in bars:
        opens = int(_time(bar["t"]).timestamp()) // step * step
        if opens not in built:
            moment = datetime.fromtimestamp(opens, UTC)
            built[opens] = {"t": f"{moment:%Y-%m-%dT%H:%M:%SZ}", "o": bar["o"], "v": 0}
            built[opens].update(h=bar["h"], l=bar["l"])
        native = built[opens]
        native.update(h=max(native["h"], bar["h"]), l=min(native["l"], bar["l"]), c=bar["c"])
        native["v"] += bar["v"]
    return list(built.values())


def _page(items: list[Any], query: dict[str, str]) -> tuple[list[Any], str | None]:
    limit = int(query.get("limit", BARS_DEFAULT))
    if not 1 <= limit <= BARS_MAX:
        raise Answer(
            400, {"message": f"invalid limit: larger than the allowed maximum of {BARS_MAX}"}
        )
    token = query.get("page_token")
    offset = int(base64.b64decode(token)) if token else 0
    rest = items[offset + limit :]
    following = base64.b64encode(str(offset + limit).encode()).decode() if rest else None
    return items[offset : offset + limit], following


def bars(symbols: Iterable[str], query: dict[str, str]) -> list[tuple[str, dict[str, Any]]]:
    """(symbol, bar) pairs of the request, symbol-major like Alpaca."""
    timeframe = query.get("timeframe", "")
    if not re.fullmatch(r"\d+(Min|Hour|Day|Week|Month)", timeframe):
        raise Answer(400, {"message": f"invalid timeframe: {timeframe}"})
    start = _time(query["start"]) if "start" in query else datetime.min.replace(tzinfo=UTC)
    end = _time(query["end"]) if "end" in query else datetime.now(UTC)
    if end < start:
        raise Answer(400, {"message": "end should not be before start"})
    found = []
    for symbol in symbols:
        selected = [bar for bar in bars_of(symbol, timeframe) if start <= _time(bar["t"]) <= end]
        if query.get("sort") == "desc":
            selected.reverse()
        found.extend((symbol, bar) for bar in selected)
    return found


def alpaca(path: str, query: dict[str, str], headers: Message) -> None:
    if not (headers.get("APCA-API-KEY-ID") and headers.get("APCA-API-SECRET-KEY")):
        raise Answer(401, fixture("error-no-auth.html"), "text/html")
    if path == "/v2/assets":
        raise Answer(200, fixture("assets.json"), "application/json; charset=UTF-8")
    if path == "/v2/clock":
        raise Answer(200, fixture("clock.json"), "application/json")
    if path == "/v2/calendar":
        days = {
            item["date"]: item
            for name in FILES
            if name.startswith("calendar-")
            for item in fixture_json(name)
        }
        first = query.get("start", "0000")
        last = query.get("end", "9999")
        raise Answer(200, [days[day] for day in sorted(days) if first <= day <= last])
    if path == "/v2/stocks/bars":
        page, following = _page(bars(query.get("symbols", "").split(","), query), query)
        grouped: dict[str, list[Any]] = {}
        for symbol, bar in page:
            grouped.setdefault(symbol, []).append(bar)
        raise Answer(200, {"bars": grouped, "next_page_token": following})
    if match := re.fullmatch(r"/v2/stocks/([^/]+)/bars", path):
        symbol = unquote(match[1])
        page, following = _page(bars([symbol], query), query)
        items = [bar for _, bar in page] or None
        raise Answer(200, {"bars": items, "next_page_token": following, "symbol": symbol})
    raise Answer(404, {"message": f"no fake for {path}"})


# --- logo.dev -------------------------------------------------------------------------------


def logo(path: str, query: dict[str, str]) -> None:
    if query.get("token") != LOGO_KEY:
        raise Answer(401, {"error": "invalid token"})
    if query.get("fallback") == "404" and path not in LOGOS:
        raise Answer(404, {"error": "not found"})
    raise Answer(200, PNG, "image/png")


# --- server ---------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        query = {key: values[-1] for key, values in parse_qs(url.query).items()}
        time.sleep(LATENCY)
        try:
            if url.path.startswith("/api/v3/"):
                binance(url.path, query)
            elif url.path.startswith("/data/spot/"):
                archive(url.path)
            elif url.path.startswith("/v2/"):
                alpaca(url.path, query, self.headers)
            elif url.path.startswith(("/ticker/", "/crypto/")):
                logo(url.path, query)
            raise Answer(404, f"no fake for {url.path}")
        except Answer as answer:
            self._send(answer)
        except Exception as error:  # a bug in the fake: tell the backend and the log
            self._send(Answer(500, f"{type(error).__name__}: {error}"))
            raise

    def do_HEAD(self) -> None:
        self.do_GET()

    def _send(self, answer: Answer) -> None:
        self.send_response(answer.status)
        self.send_header("Content-Type", answer.content_type)
        self.send_header("Content-Length", str(len(answer.body)))
        if self.path.startswith("/api/v3/"):
            self.send_header("X-MBX-USED-WEIGHT-1M", "1")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(answer.body)


if __name__ == "__main__":
    print(f"Fake sources on :{PORT} with {len(FILES)} fixture files from {FIXTURES}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
