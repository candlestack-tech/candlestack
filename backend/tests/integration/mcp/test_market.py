"""The market tools end to end: the SDK's client over HTTP, signed in with a real API key, on a
real Postgres and Redis (conftest.py): the preferences the user saved in Settings, and the
client rate limit of the data routes counted in Redis. The market data itself is the data
module's integration tests; here the DataService is faked."""

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from mcp import Client

from candlestack.core import Settings, ratelimit
from candlestack.data import Instrument, InstrumentId, InstrumentInfo, Market, Timeframe

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
ADA = "usr_adaaaaaaaa"
DEFAULTS = {
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
AAPL = Instrument(
    InstrumentId(Market.STOCK, "AAPL"), "Apple Inc. Common Stock", "alpaca", "sip", "NASDAQ"
)


@pytest.fixture
def settings(database_url: str, redis_url: str, tmp_path: Path) -> Settings:
    """Ada on the beta list, and one instrument or candle request a minute per address."""
    return Settings(
        app_env="test",
        database_url=database_url,
        redis_url=redis_url,
        model_storage_dir=tmp_path / "models",
        beta_allowlist=f"{ADA}@example.com",
        client_rate_limit=1,
    )


def tool(name: str):
    async def run(session: Client):
        return await session.call_tool(name, {})

    return run


def test_the_backtest_defaults_and_preferences_the_user_saved(mcp, keys, ada) -> None:
    saved = ada.request("PUT", "/api/v1/me/preferences/backtest-defaults", json=DEFAULTS)
    assert saved.status_code == 200, saved.text
    changed = ada.request("PATCH", "/api/v1/me/preferences/appearance", json={"time_zone": "local"})
    assert changed.status_code == 200, changed.text
    key = keys("read")

    defaults = mcp(tool("get_backtest_defaults"), key)
    preferences = mcp(tool("get_preferences"), key)

    assert not defaults.is_error, defaults.content
    assert defaults.structured_content == DEFAULTS
    assert preferences.structured_content == {
        "time_zone": "local",
        "date_format": "dmy",
        "time_format": "24",
    }


def test_a_user_who_saved_nothing_gets_the_app_s_defaults(mcp, keys) -> None:
    defaults = mcp(tool("get_backtest_defaults"), keys("read"))

    assert defaults.structured_content["timeframe"] == "30m"
    assert defaults.structured_content["benchmark"] == "bh"


class OneInstrument:
    """The DataService of a catalog with AAPL alone."""

    async def instrument(self, instrument_id: InstrumentId) -> InstrumentInfo:
        assert instrument_id == AAPL.id
        return InstrumentInfo(AAPL, tuple(Timeframe), 1451917800, 1790372340, 50000)


def test_the_client_rate_limit_is_counted_in_redis(
    mcp, keys, app: FastAPI, client, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.portal is not None
    # Both calls in one window of the limit.
    monkeypatch.setattr(ratelimit, "_time", lambda: 1790372345.0)
    app.state.data_service = OneInstrument()
    key = keys("read")

    async def detail(session: Client):
        first = await session.call_tool("get_instrument", {"instrument_id": "stock:AAPL"})
        second = await session.call_tool("get_instrument", {"instrument_id": "stock:AAPL"})
        return first, second

    first, second = mcp(detail, key)

    assert not first.is_error, first.content
    assert first.structured_content["id"] == "stock:AAPL"
    assert second.is_error
    problem = json.loads(second.content[0].text)
    assert problem["type"] == PROBLEMS + "rate-limited"
    assert problem["limit"] == 1
