"""The parity table against the API reference and the server: every endpoint has its tools,
its planned tools or the reason it stays web only."""

import asyncio

import pytest

from candlestack.core import Settings
from candlestack.main import create_app
from candlestack.mcp.parity import PARITY, Planned, Tools, WebOnly, endpoints_of, web_only
from candlestack.mcp.server import build_server

# The tasks that add the tools still missing (epic #367).
TOOL_TASKS = {399, 402, 403, 404, 405}


@pytest.fixture(scope="module")
def endpoints() -> set[str]:
    # Stage has every route the others have, and the Sentry check besides.
    schema = create_app(Settings(app_env="stage", app_version="parity")).openapi()
    return {
        f"{method.upper()} {path}"
        for path, operations in schema["paths"].items()
        for method in operations
    }


@pytest.fixture(scope="module")
def tools() -> set[str]:
    return {tool.name for tool in asyncio.run(build_server("parity").list_tools())}


def test_every_endpoint_has_an_entry(endpoints: set[str]) -> None:
    missing = sorted(endpoints - PARITY.keys())

    assert not missing, (
        f"No entry in candlestack/mcp/parity.py for {missing}: name the MCP tools that do the "
        "same, the task that adds them (Planned) or why it stays web only (WebOnly)"
    )


def test_every_entry_is_an_endpoint(endpoints: set[str]) -> None:
    assert not sorted(PARITY.keys() - endpoints)


def test_every_tool_named_exists(tools: set[str]) -> None:
    named = {name for entry in PARITY.values() if isinstance(entry, Tools) for name in entry.names}

    assert named <= tools


def test_a_tool_that_exists_is_no_longer_planned(tools: set[str]) -> None:
    planned = {
        name for entry in PARITY.values() if isinstance(entry, Planned) for name in entry.names
    }

    assert not sorted(planned & tools), "Move the tools that exist from Planned to Tools"


def test_planned_tools_belong_to_their_tasks() -> None:
    issues = {entry.issue for entry in PARITY.values() if isinstance(entry, Planned)}

    assert issues <= TOOL_TASKS


def test_every_web_only_endpoint_says_why() -> None:
    reasons = [entry.reason for entry in PARITY.values() if isinstance(entry, WebOnly)]

    assert all(reason.endswith(".") and len(reason) > 20 for reason in reasons)
    assert web_only()["DELETE /api/v1/me"] == "Deleting the account needs the signed-in browser."


def test_a_tool_knows_its_endpoints() -> None:
    assert endpoints_of("get_me") == ["GET /api/v1/me"]
    assert endpoints_of("top_instruments") == [
        "GET /api/v1/data/instruments",
        "GET /api/v1/data/top-instruments",
    ]
    assert endpoints_of("get_you") == []
