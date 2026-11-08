"""The MCP tools of sweeps end to end: the SDK's client over HTTP, signed in with a real API key,
on a real Postgres and job queue with the fake market and worker of conftest.py (which the MCP
tests' own fixtures do not have)."""

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from candlestack.core import Settings

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
RULES = [{"name": "threshold", "limit": 0.05}, {"name": "threshold", "limit": 0.2}]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Ada is on the beta list, so her keys sign in."""
    return settings.model_copy(update={"beta_allowlist": frozenset({"usr_adaaaaaaaa@example.com"})})


@pytest.fixture
def key(ada) -> Callable[..., str]:
    """``key("read", "backtests")``: the secret of a new key of Ada's with those scopes."""

    def make(*scopes: str) -> str:
        created = ada.request(
            "POST", "/api/v1/me/api-keys", json={"name": "agent", "scopes": list(scopes)}
        )
        assert created.status_code == 201, created.text
        return created.json()["secret"]

    return make


@pytest.fixture
def mcp(app: FastAPI, client: TestClient) -> Callable[..., Any]:
    """``mcp(fn, key)``: the result of ``await fn(session)`` for a session of the SDK's client
    signed in with `key`, in the TestClient's event loop (the app's lifespan runs there)."""

    async def run(fn: Callable[[Client], Awaitable[Any]], secret: str) -> Any:
        transport = httpx2.ASGITransport(app=app)
        headers = {"Authorization": f"Bearer {secret}"}
        async with (
            httpx2.AsyncClient(transport=transport, headers=headers) as http_client,
            Client(streamable_http_client("http://testserver/mcp", http_client=http_client)) as s,
        ):
            return await fn(s)

    def call(fn: Callable[[Client], Awaitable[Any]], secret: str) -> Any:
        assert client.portal is not None
        return client.portal.call(run, fn, secret)

    return call


def tool(name: str, **arguments: Any) -> Callable[[Client], Awaitable[Any]]:
    async def call(session: Client) -> Any:
        return await session.call_tool(name, arguments)

    return call


def base(model_id: str) -> dict[str, Any]:
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
    }


def problem(result: Any) -> dict[str, Any]:
    assert result.is_error
    return json.loads(result.content[0].text)


def test_an_agent_checks_runs_and_ranks_a_sweep(ada, key, mcp, worker) -> None:
    model_id = ada.runnable_model()
    secret = key("read", "backtests")
    vary = {"rules": RULES}

    planned = mcp(tool("validate_sweep", base=base(model_id), vary=vary), secret)
    started = mcp(tool("run_sweep", base=base(model_id), vary=vary, metric="win_rate"), secret)

    assert not planned.is_error, planned.content
    assert planned.structured_content["count"] == 2
    assert [setup["rule"] for setup in planned.structured_content["setups"]] == RULES
    assert not started.is_error, started.content
    sweep = started.structured_content
    assert (sweep["count"], sweep["metric"]) == (2, "win_rate")
    for each in sweep["backtests"]:
        ada.ended(each["id"])
    done = mcp(tool("get_sweep", sweep_id=sweep["id"]), secret)
    assert not done.is_error, done.content
    assert done.structured_content["done"] is True
    ranking = done.structured_content["ranking"]
    assert ranking["metric"] == "win_rate"
    assert {each["id"] for each in ranking["backtests"]} == {b["id"] for b in sweep["backtests"]}
    # What the API answers, to the letter.
    assert ada.request("GET", f"/api/v1/sweeps/{sweep['id']}").json() == done.structured_content
    assert worker.jobs


@pytest.mark.usefixtures("market")
def test_the_tools_need_their_scopes_and_answer_problems(ada, key, mcp) -> None:
    model_id = ada.runnable_model()
    read = key("read")
    vary = {"rules": RULES}

    refused = problem(mcp(tool("run_sweep", base=base(model_id), vary=vary), read))
    too_few = problem(mcp(tool("validate_sweep", base=base(model_id)), read))
    missing = problem(mcp(tool("get_sweep", sweep_id="sw_nothere000"), read))
    elsewhere = problem(
        mcp(
            tool(
                "validate_sweep",
                base=base(model_id),
                vary={"instruments": ["stock:AAPL", "stock:NOPE"]},
            ),
            read,
        )
    )

    assert refused["type"] == PROBLEMS + "missing-scope"
    assert refused["scope"] == "backtests"
    assert too_few["type"] == PROBLEMS + "validation"
    assert missing["type"] == PROBLEMS + "not-found"
    assert (elsewhere["type"], elsewhere["setup"]) == (PROBLEMS + "instrument-not-found", 2)
    assert ada.sql("SELECT count(*) FROM backtests") == [(0,)]
