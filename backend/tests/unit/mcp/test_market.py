"""The market tools through the SDK's client over HTTP, with the API keys, the DataService, the
client rate limit and the stored preferences faked: what each tool answers, how get_candles
pages and the problems they share with the data routes. The preferences on Postgres are
integration tests (tests/integration/mcp/test_market.py)."""

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.types import CallToolResult

from candlestack.accounts import AccountsService, User
from candlestack.accounts.schemas import (
    Appearance,
    BacktestDefaults,
    BacktestDefaultsIn,
    PreferencesOut,
    TimeZone,
)
from candlestack.data import (
    CANDLE_SCHEMA,
    CandleSet,
    DataError,
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
    Timeframe,
)
from candlestack.mcp import market

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
BTCUSDT_FIRST = 1502942400  # 2017-08-17T04:00:00Z
DAY = 1717372800  # 2024-06-03T00:00:00Z, a Monday
NOW = DAY + 30 * 86400
HOUR = 3600


def problem(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error
    [content] = result.content
    assert content.type == "text"
    return json.loads(content.text)


def hourly(instrument: InstrumentId, start: int, end: int, missing: set[int]) -> CandleSet:
    """Hourly candles of ``[start, end)`` but those in `missing`, which are its gaps."""
    times = [t for t in range(start, end, HOUR) if t not in missing]
    frame = pl.DataFrame(
        {
            "ts": times,
            "open": [float(i) for i in range(len(times))],
            "high": [i + 2.0 for i in range(len(times))],
            "low": [i - 1.0 for i in range(len(times))],
            "close": [i + 1.0 for i in range(len(times))],
            "volume": [10.0] * len(times),
        },
        schema=CANDLE_SCHEMA,
    )
    gaps = [(t, t + HOUR) for t in sorted(missing) if start <= t < end]
    source, feed = ("binance", "spot") if instrument.market is Market.CRYPTO else ("alpaca", "sip")
    return CandleSet(
        instrument=instrument,
        timeframe=Timeframe.H1,
        start=start,
        end=end,
        source=source,
        feed=feed,
        frame=frame,
        gaps=gaps,
        gaps_total=len(gaps),
        fingerprint="sha256:" + "ab" * 32,
    )


@dataclass
class FakeDataService:
    """Answers like the DataService, from BTCUSDT and AAPL; ``error`` is raised by every call
    when set, and ``missing`` are the open times without a candle."""

    error: DataError | None = None
    unavailable: list[Market] = field(default_factory=list)
    missing: set[int] = field(default_factory=set)
    calls: list[tuple[Any, ...]] = field(default_factory=list)

    def _called(self, *call: Any) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

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
        found, stats, firsts = [BTCUSDT, AAPL], [BTCUSDT_STATS, None], [BTCUSDT_FIRST, None]
        page = slice(offset, offset + limit)
        return SearchResult(
            found[page], self.unavailable, len(found), stats[page], [None] * 2, firsts[page]
        )

    async def instrument(self, instrument_id: InstrumentId) -> InstrumentInfo:
        self._called("instrument", instrument_id)
        if instrument_id != AAPL.id:
            raise InstrumentNotFound(instrument_id)
        return InstrumentInfo(AAPL, tuple(Timeframe), 1451917800, 1790372340, 50000)

    async def first_candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, start: int, end: int, limit: int
    ) -> CandleSet:
        """The first `limit` hours from `start` (the data begins at ``DAY``), missing ones
        counted."""
        self._called("first_candles", instrument_id, timeframe, start, end, limit)
        if start >= end:
            raise InvalidRequest(f"start ({start}) must be before end ({end}).")
        return hourly(instrument_id, start, min(end, start + limit * HOUR), self.missing)

    async def last_candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, end: int, limit: int
    ) -> CandleSet:
        """The last `limit` hours before `end`, missing ones counted, from ``DAY`` on."""
        self._called("last_candles", instrument_id, timeframe, end, limit)
        return hourly(instrument_id, max(end - limit * HOUR, DAY), end, self.missing)


@dataclass
class FakeLimiter:
    """``RateLimiter.hit`` that answers ``wait``, recording its keys."""

    wait: float = 0.0
    keys: list[str] = field(default_factory=list)

    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        self.keys.append(key)
        return self.wait if limit > 0 and cost > 0 and window > 0 else 0.0


@pytest.fixture
def service() -> FakeDataService:
    return FakeDataService()


@pytest.fixture
def limiter() -> FakeLimiter:
    return FakeLimiter()


@pytest.fixture(autouse=True)
def _fakes(
    app: FastAPI,
    mcp_client: TestClient,
    service: FakeDataService,
    limiter: FakeLimiter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fakes in place of what the app's lifespan built, and the clock at ``NOW``."""
    assert mcp_client.portal is not None
    app.state.data_service = service
    app.state.rate_limiter = limiter
    monkeypatch.setattr(market, "_time", lambda: NOW + 0.5)


def call(mcp, tool: str, arguments: dict[str, Any] | None = None, **options: Any) -> Any:
    async def run(session: Client) -> CallToolResult:
        return await session.call_tool(tool, arguments or {})

    return mcp(run, **options)


def answer(mcp, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    result = call(mcp, tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


# --- search_instruments and top_instruments ---------------------------------------------------

BTC_ITEM = {
    "id": "crypto:BTCUSDT",
    "name": "BTC/USDT",
    "exchange": "BINANCE",
    "price": 83249.35,
    "change_24h": 1.51,
    "volume_24h": 1258811866.0,
    "available_from": "2017-08-17T04:00:00Z",
}


def test_search_answers_compact_items_and_the_next_page(mcp, service: FakeDataService) -> None:
    found = answer(
        mcp, "search_instruments", {"query": "btc", "market": "crypto", "exchange": "BINANCE"}
    )

    assert found == {
        "items": [
            BTC_ITEM,
            {
                "id": "stock:AAPL",
                "name": "Apple Inc. Common Stock",
                "exchange": "NASDAQ",
                "price": None,
                "change_24h": None,
                "volume_24h": None,
                "available_from": None,
            },
        ],
        "total": 2,
        "next_offset": None,
        "unavailable": [],
    }
    assert service.calls == [
        ("search", "btc", Market.CRYPTO, 10, 0, Exchange.BINANCE, InstrumentSort.VOLUME)
    ]


def test_search_pages_with_offset(mcp, service: FakeDataService) -> None:
    service.unavailable = [Market.STOCK]

    first = answer(mcp, "search_instruments", {"query": "a", "limit": 1})
    second = answer(mcp, "search_instruments", {"query": "a", "limit": 1, "offset": 1})

    assert [item["id"] for item in first["items"]] == ["crypto:BTCUSDT"]
    assert first["next_offset"] == 1
    assert first["unavailable"] == ["stock"]
    assert [item["id"] for item in second["items"]] == ["stock:AAPL"]
    assert second["next_offset"] is None


def test_search_needs_a_query(mcp, service: FakeDataService) -> None:
    body = problem(call(mcp, "search_instruments", {"query": ""}))

    assert body["type"] == PROBLEMS + "validation"
    assert body["errors"][0]["loc"] == ["query"]
    assert service.calls == []


@pytest.mark.parametrize(
    ("sort", "order"),
    [
        ("volume", InstrumentSort.VOLUME),
        ("change", InstrumentSort.CHANGE),
        ("-change", InstrumentSort.LOSERS),
    ],
)
def test_top_instruments_lists_without_a_query_in_the_order_asked(
    mcp, service: FakeDataService, sort: str, order: InstrumentSort
) -> None:
    top = answer(mcp, "top_instruments", {"market": "crypto", "sort": sort, "limit": 1})

    assert top == {"items": [BTC_ITEM], "unavailable": []}
    assert service.calls == [("search", None, Market.CRYPTO, 1, 0, None, order)]


def test_top_instruments_defaults_to_the_ten_most_traded(mcp, service: FakeDataService) -> None:
    answer(mcp, "top_instruments")

    assert service.calls == [("search", None, None, 10, 0, None, InstrumentSort.VOLUME)]


def test_top_instruments_does_not_sort_by_symbol(mcp) -> None:
    body = problem(call(mcp, "top_instruments", {"sort": "symbol"}))

    assert body["type"] == PROBLEMS + "validation"


# --- get_instrument -----------------------------------------------------------------------------


def test_get_instrument_answers_its_period(mcp, limiter: FakeLimiter) -> None:
    detail = answer(mcp, "get_instrument", {"instrument_id": " Stock:aapl "})

    assert detail == {
        "id": "stock:AAPL",
        "name": "Apple Inc. Common Stock",
        "exchange": "NASDAQ",
        "timeframes": ["1m", "5m", "15m", "30m", "1h", "4h", "1d"],
        "available_from": "2016-01-04T14:30:00Z",
        "available_to": "2026-09-25T21:39:00Z",
    }
    assert limiter.keys == ["data:rl:client:127.0.0.1"]


def test_an_unknown_instrument_is_the_data_problem(mcp) -> None:
    body = problem(call(mcp, "get_instrument", {"instrument_id": "stock:NOPE"}))

    assert body["type"] == PROBLEMS + "instrument-not-found"
    assert body["status"] == 404
    assert body["instrument"] == "stock:NOPE"


def test_a_malformed_instrument_id_is_a_validation_problem(mcp, service: FakeDataService) -> None:
    body = problem(call(mcp, "get_instrument", {"instrument_id": "forex:EURUSD"}))

    assert body["type"] == PROBLEMS + "validation"
    assert "crypto and stock" in body["detail"]
    assert service.calls == []


def test_the_client_rate_limit_applies(mcp, service: FakeDataService, limiter: FakeLimiter) -> None:
    limiter.wait = 12.2

    body = problem(call(mcp, "get_instrument", {"instrument_id": "stock:AAPL"}))

    assert body["type"] == PROBLEMS + "rate-limited"
    assert "Retry in 13 seconds" in body["detail"]
    assert service.calls == []


# --- get_candles --------------------------------------------------------------------------------


def candles_of(page: dict[str, Any]) -> list[int]:
    return [candle[0] for candle in page["candles"]]


def test_without_start_the_newest_candles_and_where_the_earlier_end(
    mcp, service: FakeDataService, limiter: FakeLimiter
) -> None:
    page = answer(
        mcp, "get_candles", {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "limit": 3}
    )

    assert service.calls == [("last_candles", BTCUSDT.id, Timeframe.H1, NOW, 3)]
    assert candles_of(page) == [NOW - 3 * HOUR, NOW - 2 * HOUR, NOW - HOUR]
    assert page["candles"][0] == [NOW - 3 * HOUR, 0.0, 2.0, -1.0, 1.0, 10.0]
    assert page["start"] == "2024-07-02T21:00:00Z"
    assert page["end"] == "2024-07-03T00:00:00Z"
    assert page["next_end"] == "2024-07-02T21:00:00Z"
    assert page["next_start"] is None
    assert page["instrument"] == "crypto:BTCUSDT"
    assert page["timeframe"] == "1h"
    assert limiter.keys == ["data:rl:client:127.0.0.1"]


def test_without_start_missing_candles_do_not_end_the_paging(mcp, service: FakeDataService) -> None:
    """Hours without a trade make a page shorter than `limit`; the earlier data is still
    there, and the next page ends where this one starts, not at its first candle."""
    service.missing = {NOW - 4 * HOUR, NOW - 2 * HOUR}

    page = answer(
        mcp, "get_candles", {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "limit": 4}
    )

    assert candles_of(page) == [NOW - 3 * HOUR, NOW - HOUR]
    assert page["gaps"] == [[NOW - 4 * HOUR, NOW - 3 * HOUR], [NOW - 2 * HOUR, NOW - HOUR]]
    assert page["start"] == "2024-07-02T20:00:00Z"
    assert page["next_end"] == "2024-07-02T20:00:00Z"


def test_without_start_the_data_s_beginning_ends_the_paging(mcp) -> None:
    page = answer(
        mcp,
        "get_candles",
        {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "end": "2024-06-03T02:00:00Z"},
    )

    assert candles_of(page) == [DAY, DAY + HOUR]
    assert page["next_end"] is None


def test_with_start_a_crypto_page_spans_limit_candles(mcp, service: FakeDataService) -> None:
    service.missing = {DAY + HOUR}

    page = answer(
        mcp,
        "get_candles",
        {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "start": "2024-06-03", "limit": 3},
    )

    assert service.calls == [("first_candles", BTCUSDT.id, Timeframe.H1, DAY, NOW, 3)]
    assert candles_of(page) == [DAY, DAY + 2 * HOUR]
    assert page["gaps"] == [[DAY + HOUR, DAY + 2 * HOUR]]
    assert page["start"] == "2024-06-03T00:00:00Z"
    assert page["end"] == "2024-06-03T03:00:00Z"
    assert page["next_start"] == "2024-06-03T03:00:00Z"
    assert page["next_end"] is None


def test_with_start_the_last_page_ends_at_end(mcp, service: FakeDataService) -> None:
    page = answer(
        mcp,
        "get_candles",
        {
            "instrument_id": "crypto:BTCUSDT",
            "timeframe": "1h",
            "start": DAY,
            "end": "2024-06-03T02:00:00+00:00",
        },
    )

    assert service.calls == [("first_candles", BTCUSDT.id, Timeframe.H1, DAY, DAY + 2 * HOUR, 200)]
    assert candles_of(page) == [DAY, DAY + HOUR]
    assert page["next_start"] is None


def test_a_stock_page_ends_where_its_first_limit_candles_do(mcp, service: FakeDataService) -> None:
    """The service counts the session candles of a stock; the page ends where it says."""
    service.missing = {DAY + 2 * HOUR}

    page = answer(
        mcp,
        "get_candles",
        {
            "instrument_id": "stock:AAPL",
            "timeframe": "1h",
            "start": "2024-06-03T00:00:00-04:00",
            "end": str(NOW),
            "limit": 3,
        },
    )

    assert service.calls == [("first_candles", AAPL.id, Timeframe.H1, DAY + 4 * HOUR, NOW, 3)]
    assert candles_of(page) == [DAY + 4 * HOUR, DAY + 5 * HOUR, DAY + 6 * HOUR]
    assert page["end"] == "2024-06-03T07:00:00Z"
    assert page["next_start"] == "2024-06-03T07:00:00Z"


def test_a_time_that_is_neither_a_date_nor_epoch_seconds(mcp, service: FakeDataService) -> None:
    body = problem(
        call(
            mcp,
            "get_candles",
            {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "start": "yesterday"},
        )
    )

    assert body["type"] == PROBLEMS + "validation"
    assert body["errors"][0]["loc"] == ["start"]
    assert service.calls == []


def test_a_start_after_the_end_is_a_validation_problem(mcp) -> None:
    body = problem(
        call(
            mcp,
            "get_candles",
            {
                "instrument_id": "crypto:BTCUSDT",
                "timeframe": "1h",
                "start": "2024-06-04",
                "end": "2024-06-03",
            },
        )
    )

    assert body["type"] == PROBLEMS + "validation"
    assert "must be before" in body["detail"]


def test_a_period_without_data_is_the_data_problem(mcp, service: FakeDataService) -> None:
    service.error = PeriodOutOfRange(
        BTCUSDT.id, Timeframe.H1, BTCUSDT_FIRST, NOW, start=DAY - 10 * 365 * 86400, end=DAY
    )

    body = problem(
        call(
            mcp,
            "get_candles",
            {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "start": "2014-06-03"},
        )
    )

    assert body["type"] == PROBLEMS + "period-out-of-range"
    assert body["available_from"] == BTCUSDT_FIRST


@pytest.mark.parametrize("limit", [0, 501])
def test_a_page_holds_500_candles_at_most(mcp, service: FakeDataService, limit: int) -> None:
    body = problem(
        call(
            mcp,
            "get_candles",
            {"instrument_id": "crypto:BTCUSDT", "timeframe": "1h", "limit": limit},
        )
    )

    assert body["type"] == PROBLEMS + "validation"
    assert body["errors"][0]["loc"] == ["limit"]
    assert service.calls == []


def test_the_description_says_the_data_terms(mcp) -> None:
    async def tools(session: Client) -> Any:
        return await session.list_tools()

    [tool] = [tool for tool in mcp(tools).tools if tool.name == "get_candles"]

    assert "Alpaca" in tool.description
    assert "personal, noncommercial use" in tool.description
    assert "15 minutes delayed" in tool.description


# --- preprocessing_catalog ----------------------------------------------------------------------


def test_the_catalog_lists_every_block_without_the_app_s_fields(mcp) -> None:
    catalog = answer(mcp, "preprocessing_catalog")

    ids = [block["id"] for block in catalog["blocks"]]
    assert ids[0] == "candles@1"
    assert ids[-1] == "window@1"
    assert "rsi@2" in ids
    rsi = next(block for block in catalog["blocks"] if block["id"] == "rsi@2")
    assert set(rsi) == {"id", "step", "description", "warm_up", "params"}
    assert "tunable" not in rsi["params"][0]


def test_the_catalog_of_one_step(mcp) -> None:
    catalog = answer(mcp, "preprocessing_catalog", {"step": "window"})

    [window] = catalog["blocks"]
    assert window["id"] == "window@1"
    assert window["warm_up"] == "length - 1"
    length = window["params"][0]
    assert length["name"] == "length"
    assert length["required"] is True
    assert (length["minimum"], length["maximum"]) == (1, 1000)
    # What a parameter does not have is left out rather than null.
    assert "default" not in length
    assert "options" not in length
    assert "exclusive_minimum" not in length


# --- get_backtest_defaults and get_preferences ---------------------------------------------------


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The users whose preferences were read; Ada saved a timeframe, a period and New York
    time."""
    users: list[str] = []

    async def preferences(_service: AccountsService, user: User) -> PreferencesOut:
        users.append(user.id)
        return PreferencesOut(
            backtest_defaults=BacktestDefaults(
                timeframe="1h", period_start=date(2022, 1, 3), period_end=date(2023, 12, 29)
            ),
            appearance=Appearance(theme="light", time_zone=TimeZone.local, date_format="iso"),
        )

    monkeypatch.setattr(AccountsService, "preferences", preferences)
    return users


def test_get_backtest_defaults_answers_the_saved_defaults(mcp, saved: list[str]) -> None:
    defaults = answer(mcp, "get_backtest_defaults")

    assert defaults == {
        "timeframe": "1h",
        "period_start": "2022-01-03",
        "period_end": "2023-12-29",
        "direction": "long",
        "threshold": 0.05,
        "stop_loss_pct": 7,
        "fees_pct": 0.1,
        "slippage_pct": 0.02,
        "benchmark": "bh",
    }
    assert saved == ["usr_adaaaaaaaa"]


def test_get_preferences_answers_how_the_user_reads_dates(mcp, saved: list[str]) -> None:
    preferences = answer(mcp, "get_preferences")

    assert preferences == {"time_zone": "local", "date_format": "iso", "time_format": "24"}
    assert saved == ["usr_adaaaaaaaa"]


SAVED = {
    "timeframe": "4h",
    "period_start": "2022-01-03",
    "period_end": "2023-12-29",
    "direction": "both",
    "threshold": 0.1,
    "stop_loss_pct": 5,
    "fees_pct": 0.05,
    "slippage_pct": 0.01,
    "benchmark": "SPY",
}


@pytest.fixture
def saves(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """The users and defaults saved."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def save(
        _service: AccountsService, user: User, defaults: BacktestDefaultsIn
    ) -> BacktestDefaults:
        calls.append((user.id, defaults.model_dump(mode="json")))
        return defaults

    monkeypatch.setattr(AccountsService, "save_backtest_defaults", save)
    return calls


def test_update_backtest_defaults_saves_every_setting(
    mcp, keys, saves: list[tuple[str, dict[str, Any]]]
) -> None:
    result = call(mcp, "update_backtest_defaults", {"defaults": SAVED}, key=keys.backtests_key)

    assert not result.is_error, result.content
    assert result.structured_content == SAVED
    assert saves == [("usr_adaaaaaaaa", SAVED)]


@pytest.mark.parametrize(
    "defaults",
    [
        {k: v for k, v in SAVED.items() if k != "benchmark"},
        {**SAVED, "period_start": "2024-01-02"},
        {**SAVED, "stop": 5},
    ],
    ids=["a setting left out", "a period that runs back", "an unknown setting"],
)
def test_update_backtest_defaults_refuses_what_the_route_refuses(
    mcp, keys, saves: list[tuple[str, dict[str, Any]]], defaults: dict[str, Any]
) -> None:
    body = problem(
        call(mcp, "update_backtest_defaults", {"defaults": defaults}, key=keys.backtests_key)
    )

    assert body["type"] == PROBLEMS + "validation"
    assert body["errors"][0]["loc"][0] == "defaults"
    assert saves == []


def test_update_backtest_defaults_needs_backtests(
    mcp, saves: list[tuple[str, dict[str, Any]]]
) -> None:
    body = problem(call(mcp, "update_backtest_defaults", {"defaults": SAVED}))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["scope"] == "backtests"
    assert saves == []


# --- scopes -------------------------------------------------------------------------------------

READ_TOOLS = [spec.name for spec in market.TOOLS if spec.annotations.read_only_hint]


def test_only_saving_the_defaults_changes_anything() -> None:
    assert {spec.name for spec in market.TOOLS} - set(READ_TOOLS) == {"update_backtest_defaults"}


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_every_market_tool_that_reads_needs_read(
    mcp, keys, service: FakeDataService, tool: str
) -> None:
    body = problem(call(mcp, tool, key=keys.backtests_key))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["scope"] == "read"
    assert service.calls == []
