"""The MCP server through the SDK's client over HTTP, with the API keys faked (conftest.py):
what a client sees when it connects, the docs resource, and every failure of a tool as a
problem. The tools on Postgres are integration tests (tests/integration/mcp/)."""

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextResourceContents
from pydantic import BaseModel

from candlestack.accounts import HEAVY, Caller, Scope, User
from candlestack.accounts.limits import USER_RATE_LIMIT, Budget
from candlestack.core import ProblemError
from candlestack.mcp import http
from candlestack.mcp.convention import Example, ToolSpec, changes, reads, tool_call
from candlestack.mcp.docs import AGENTS_DOCS, DOCS_URI, MANIFEST_DOCS, MCP_DOCS, RESULT_DOCS
from candlestack.mcp.server import TOOLS, CandleStackMCP

PROBLEMS = "https://candlestack.tech/problems/"
ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")


def problem(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error
    [content] = result.content
    assert content.type == "text"
    return json.loads(content.text)


class Count(BaseModel):
    count: int


async def count_to(n: int, ctx: Context) -> Count:
    """Counts to `n`, for the tests."""
    if n < 0:
        raise ProblemError(409, "negative", "Negative", "Count to a positive number.")
    if n == 13:
        raise ValueError("unlucky")
    return Count(count=n + len(tool_call(ctx).scopes) * 0)


class Part(BaseModel):
    name: str
    size: int | None


class Described(BaseModel):
    count: int
    note: str | None
    parts: dict[str, int | None]
    values: list[int | None]
    part: Part


async def describe_count(ctx: Context) -> Described:
    """Describes a count with null fields, for the tests."""
    return Described(
        count=len(tool_call(ctx).scopes),
        note=None,
        parts={"a": None, "b": 2},
        values=[1, None],
        part=Part(name="p", size=None),
    )


async def start_count(ctx: Context) -> Count:
    """Starts a count, for the tests."""
    return Count(count=len(tool_call(ctx).scopes))


async def count_heavily(ctx: Context) -> Count:
    """Counts what costs much, for the tests."""
    return Count(count=len(tool_call(ctx).scopes))


TEST_TOOLS = (
    ToolSpec(
        count_heavily,
        name="count_heavily",
        title="Count heavily",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example({}, Count(count=1)),
        cost=HEAVY,
    ),
    ToolSpec(
        count_to,
        name="count_to",
        title="Count",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example({"n": 1}, Count(count=1)),
    ),
    ToolSpec(
        describe_count,
        name="describe_count",
        title="Describe a count",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            {}, Described(count=1, note=None, parts={}, values=[], part=Part(name="p", size=1))
        ),
    ),
    ToolSpec(
        start_count,
        name="start_count",
        title="Start a count",
        scopes=(Scope.backtests,),
        annotations=changes(destructive=False, idempotent=False),
        example=Example({}, Count(count=1)),
    ),
)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The app with the test tools next to the real ones."""
    real: CandleStackMCP = app.state.mcp_server
    app.state.mcp_server = CandleStackMCP((*real.specs.values(), *TEST_TOOLS), version=real.version)
    return app


@pytest.mark.parametrize(("mode", "protocol"), [("auto", "2026-07-28"), ("legacy", "2025-11-25")])
def test_a_client_gets_the_instructions_and_the_tools(mcp, mode: str, protocol: str) -> None:
    async def connect(session: Client) -> tuple[str, str | None, Any, Any]:
        return (
            session.protocol_version,
            session.instructions,
            session.server_info,
            await session.list_tools(),
        )

    version, instructions, info, tools = mcp(connect, mode=mode)

    assert version == protocol
    assert instructions is not None
    assert AGENTS_DOCS in instructions
    assert MCP_DOCS in instructions
    # It asks the agent to give the user the app's pages the answers link.
    assert "Give the user these links" in instructions
    assert info.name == "candlestack"
    assert info.version == "1.2.3"
    assert info.title == "CandleStack"
    assert [(icon.src, icon.mime_type) for icon in info.icons] == [
        ("https://candlestack.tech/favicon.svg", "image/svg+xml"),
        ("https://candlestack.tech/apple-touch-icon.png", "image/png"),
    ]
    get_me = next(tool for tool in tools.tools if tool.name == "get_me")
    assert get_me.annotations.read_only_hint is True
    assert get_me.annotations.destructive_hint is False
    assert get_me.annotations.idempotent_hint is True
    assert get_me.output_schema["required"] == [
        "id",
        "display_name",
        "email",
        "created_at",
        "scopes",
    ]
    assert get_me.description.endswith("Docs: https://candlestack.tech/docs/mcp/tools/#get_me")


def test_the_docs_resource_links_the_docs(mcp) -> None:
    async def read(session: Client) -> Any:
        listed = await session.list_resources()
        return listed.resources, await session.read_resource(DOCS_URI)

    resources, read_result = mcp(read)

    assert [resource.uri for resource in resources] == [DOCS_URI]
    [contents] = read_result.contents
    assert isinstance(contents, TextResourceContents)
    assert contents.mime_type == "text/markdown"
    for link in (AGENTS_DOCS, MCP_DOCS, MANIFEST_DOCS, RESULT_DOCS):
        assert link in contents.text


def test_a_tool_answers_structured_content(mcp) -> None:
    result = mcp(lambda session: session.call_tool("count_to", {"n": 3}))

    assert not result.is_error
    assert result.structured_content == {"count": 3}


def test_a_result_s_text_is_compact_json_without_the_null_fields(mcp) -> None:
    result = mcp(lambda session: session.call_tool("describe_count", {}))

    assert not result.is_error
    [content] = result.content
    assert content.type == "text"
    # A model's null fields are left out of the text only; a null in a list or as the value of
    # a map stays, as its place there means something.
    assert content.text == (
        '{"count":1,"parts":{"a":null,"b":2},"values":[1,null],"part":{"name":"p"}}'
    )
    assert result.structured_content == {
        "count": 1,
        "note": None,
        "parts": {"a": None, "b": 2},
        "values": [1, None],
        "part": {"name": "p", "size": None},
    }


def test_a_problem_the_tool_raises_is_its_error(mcp) -> None:
    result = mcp(lambda session: session.call_tool("count_to", {"n": -1}))

    assert problem(result) == {
        "type": PROBLEMS + "negative",
        "title": "Negative",
        "status": 409,
        "detail": "Count to a positive number.",
    }


def test_arguments_that_do_not_fit_are_a_validation_problem(mcp) -> None:
    result = mcp(lambda session: session.call_tool("count_to", {"n": "three"}))

    body = problem(result)
    assert body["type"] == PROBLEMS + "validation"
    assert body["status"] == 422
    assert body["detail"].startswith("argument 'n': ")
    assert body["errors"][0]["loc"] == ["n"]
    assert "three" not in json.dumps(body)


def test_an_unknown_tool_is_not_found(mcp) -> None:
    body = problem(mcp(lambda session: session.call_tool("get_you", {})))

    assert body["type"] == PROBLEMS + "not-found"
    assert "get_you" in body["detail"]


def test_a_crash_is_an_internal_error_for_a_developer(
    mcp, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="candlestack.mcp.server"):
        body = problem(mcp(lambda session: session.call_tool("count_to", {"n": 13})))

    assert body["type"] == PROBLEMS + "internal-error"
    assert "unlucky" not in body["detail"]
    [record] = [r for r in caplog.records if r.name == "candlestack.mcp.server"]
    assert record.getMessage() == "The tool count_to failed"
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], ValueError)


def test_a_key_without_the_tool_s_scope_gets_missing_scope(mcp) -> None:
    body = problem(mcp(lambda session: session.call_tool("start_count", {})))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["status"] == 403
    assert body["scope"] == "backtests"


def test_a_key_with_the_scope_runs_the_tool(mcp, keys) -> None:
    result = mcp(lambda session: session.call_tool("start_count", {}), key=keys.backtests_key)

    assert result.structured_content == {"count": 1}


def test_a_read_tool_needs_read(mcp, keys) -> None:
    call = mcp(lambda session: session.call_tool("count_to", {"n": 1}), key=keys.backtests_key)
    body = problem(call)

    assert body["scope"] == "read"


# --- What a tool costs of the rate limits -----------------------------------------------------

BUDGET = Budget(
    "accounts:key-rl:key_readaaaaaa",
    120,
    "API key",
    "usr_adaaaaaaaa",
    "accounts:key-rl-free:key_readaaaaaa",
)


@dataclass
class FakeLimiter:
    hits: list[tuple[str, int]] = field(default_factory=list)
    # The counters over their limit, by their prefix.
    over: str | None = None

    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        self.hits.append((key, cost))
        return 7.0 if self.over is not None and key.startswith(self.over) else 0.0


def _work(limiter: FakeLimiter) -> list[tuple[str, int]]:
    """What counted against the key's and the account's budgets: all but the light requests."""
    return [(key, cost) for key, cost in limiter.hits if key != BUDGET.light_counter]


@pytest.fixture
def limiter(app: FastAPI, mcp_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> FakeLimiter:
    """Keys that sign in with ``BUDGET``, counted by a fake limiter (the sign-in itself is
    faked; the requests are counted by their method, and what the tools charge on top)."""

    async def sign_in(token: str, **_clients: Any) -> Caller:
        return Caller(ADA, frozenset({Scope.read}), budget=BUDGET)

    monkeypatch.setattr(http, "key_caller", sign_in)
    fake = FakeLimiter()
    app.state.rate_limiter = fake
    return fake


def test_a_heavy_tool_pays_its_cost_beyond_the_request(mcp, limiter: FakeLimiter) -> None:
    result = mcp(lambda session: session.call_tool("count_heavily", {}))

    assert not result.is_error
    # The request counts 1 against the key's and the account's budgets, the tool the rest.
    assert _work(limiter) == [
        (BUDGET.counter, 1),
        ("accounts:user-rl:usr_adaaaaaaaa", 1),
        (BUDGET.counter, HEAVY - 1),
        ("accounts:user-rl:usr_adaaaaaaaa", HEAVY - 1),
    ]


def test_a_plain_tool_costs_only_its_request(mcp, limiter: FakeLimiter) -> None:
    assert not mcp(lambda session: session.call_tool("count_to", {"n": 1})).is_error
    assert _work(limiter) == [(BUDGET.counter, 1), ("accounts:user-rl:usr_adaaaaaaaa", 1)]
    # Connecting and listing count only against the key's light limit, never the account's.
    assert {key for key, _ in limiter.hits} - {key for key, _ in _work(limiter)} == {
        BUDGET.light_counter
    }


def test_a_heavy_tool_past_the_account_s_budget_does_not_run(mcp, limiter: FakeLimiter) -> None:
    limiter.over = "accounts:user-rl:"

    body = problem(mcp(lambda session: session.call_tool("count_heavily", {})))

    assert body["type"] == PROBLEMS + "rate-limited"
    assert body["status"] == 429
    assert body["limit"] == USER_RATE_LIMIT
    assert "Retry in 7 seconds" in body["detail"]


@pytest.mark.parametrize(
    ("arguments", "cost"),
    [
        ({"backtest_id": "bt_7f3k2m9qx4"}, 1),
        ({"backtest_id": "bt_7f3k2m9qx4", "trades_per_page": 0, "equity_points": 0}, 1),
        ({"backtest_id": "bt_7f3k2m9qx4", "trades_per_page": 50}, HEAVY),
        ({"backtest_id": "bt_7f3k2m9qx4", "equity_points": 200}, HEAVY),
    ],
)
def test_get_backtest_costs_heavy_only_when_it_asks_for_trades_or_points(
    arguments: dict[str, Any], cost: int
) -> None:
    specs = {spec.name: spec for spec in TOOLS}

    assert specs["get_backtest"].cost_of(arguments) == cost
    assert specs["compare_backtests"].cost_of({"ids": ["bt_7f3k2m9qx4"]}) == HEAVY
