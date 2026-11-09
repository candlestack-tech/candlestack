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
    # The app's Compare page of both, in the grid's order, for the agent to give the user.
    grid = ",".join(each["id"] for each in sweep["backtests"])
    assert sweep["url"] == f"http://testserver/backtests/compare?ids={grid}"
    for each in sweep["backtests"]:
        ada.ended(each["id"])
    done = mcp(tool("get_sweep", sweep_id=sweep["id"]), secret)
    assert not done.is_error, done.content
    assert done.structured_content["done"] is True
    ranking = done.structured_content["ranking"]
    assert ranking["metric"] == "win_rate"
    assert {each["id"] for each in ranking["backtests"]} == {b["id"] for b in sweep["backtests"]}
    # What the API answers, to the letter, and the Compare page of the ranked ones, best first.
    answered = {**done.structured_content}
    url = answered.pop("url")
    assert ada.request("GET", f"/api/v1/sweeps/{sweep['id']}").json() == answered
    ranked = [each["id"] for each in ranking["backtests"]]
    assert url == f"http://testserver/backtests/compare?ids={','.join(ranked)}"
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


def test_an_agent_waits_for_a_sweep_until_it_is_ranked(ada, key, mcp, worker) -> None:
    worker.gate.clear()  # nothing ends until the first wait has answered
    model_id = ada.runnable_model()
    secret = key("read", "backtests")
    started = mcp(tool("run_sweep", base=base(model_id), vary={"rules": RULES}), secret)
    sweep = started.structured_content

    waiting = mcp(tool("wait_for_sweep", sweep_id=sweep["id"], timeout_s=1), secret)
    worker.gate.set()
    done = None
    for _ in range(5):
        done = mcp(tool("wait_for_sweep", sweep_id=sweep["id"]), secret).structured_content
        if done["done"]:
            break

    assert not started.is_error, started.content
    assert "eta_s" in sweep
    assert not waiting.is_error, waiting.content
    assert waiting.structured_content["done"] is False
    assert waiting.structured_content["ranking"] is None
    assert waiting.structured_content["url"] is None
    assert waiting.structured_content["progress"]["finished"] == 0
    assert done is not None
    assert done["done"] is True
    assert (done["count"], done["eta_s"]) == (2, None)
    assert done["progress"] == {"queued": 0, "running": 0, "finished": 2, "failed": 0}
    gotten = ada.request("GET", f"/api/v1/sweeps/{sweep['id']}").json()
    assert done["ranking"] == gotten["ranking"]
    best = ",".join(each["id"] for each in gotten["ranking"]["backtests"])
    assert done["url"] == f"http://testserver/backtests/compare?ids={best}"


def test_a_wait_for_a_sweep_reports_how_many_ended(ada, key, mcp, worker) -> None:
    model_id = ada.runnable_model()
    secret = key("read", "backtests")
    sweep = mcp(tool("run_sweep", base=base(model_id), vary={"rules": RULES}), secret)
    reported: list[tuple[float, float | None, str | None]] = []

    async def wait(session: Client) -> Any:
        async def report(progress: float, total: float | None, message: str | None) -> None:
            reported.append((progress, total, message))

        arguments = {"sweep_id": sweep.structured_content["id"]}
        return await session.call_tool("wait_for_sweep", arguments, progress_callback=report)

    done = mcp(wait, secret)

    assert done.structured_content["done"] is True
    # Each notification is higher than the last; the last says every backtest ended.
    values = [value for value, _, _ in reported]
    assert values == sorted(set(values))
    assert reported[-1] == (2, 2, "2 of 2 ended: 2 finished, 0 failed, 0 running, 0 queued")


@pytest.mark.usefixtures("market")
def test_a_sweep_started_again_with_its_key_is_the_first_one(ada, key, mcp, sql) -> None:
    model_id = ada.runnable_model()
    secret = key("read", "backtests")
    arguments = {"base": base(model_id), "vary": {"rules": RULES}, "idempotency_key": "sweep-1"}

    first = mcp(tool("run_sweep", **arguments), secret)
    again = mcp(tool("run_sweep", **arguments), secret)
    other = mcp(tool("run_sweep", **{**arguments, "metric": "win_rate"}), secret)

    assert not first.is_error, first.content
    assert again.structured_content == first.structured_content
    assert sql("SELECT count(*) FROM sweeps") == [(1,)]
    assert problem(other)["type"] == PROBLEMS + "idempotency-key-reused"


@pytest.mark.usefixtures("market")
def test_a_wait_for_another_users_sweep_is_not_found(ada, key, mcp) -> None:
    result = mcp(tool("wait_for_sweep", sweep_id="sw_7f3k2m9qx4"), key("read"))

    assert problem(result)["type"] == PROBLEMS + "not-found"
