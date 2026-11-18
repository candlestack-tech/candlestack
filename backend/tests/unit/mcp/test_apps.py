"""The chat widget (``apps``) through the SDK's client over HTTP: the ``ui://`` resource of MCP
Apps, the server's capability, the tools that link the widget, and which calls count as from a
client that shows it. What each tool adds to its result's ``_meta`` is in test_run.py and
test_models.py."""

import hashlib
import re
from typing import Any

import pytest
from fastapi import FastAPI
from mcp import Client
from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, RequestParamsMeta, TextResourceContents
from pydantic import BaseModel

from candlestack.accounts import Scope
from candlestack.mcp import apps
from candlestack.mcp.apps import CHART_KEY, WIDGET_HTML, WIDGET_URI
from candlestack.mcp.convention import Example, ToolSpec, reads
from candlestack.mcp.server import TOOLS, CandleStackMCP

APP_MIME_TYPE = "text/html;profile=mcp-app"
SHOWN = {"get_backtest", "compare_backtests", "get_model"}


def test_the_widget_is_a_resource_of_mcp_apps(mcp) -> None:
    async def run(session: Client) -> Any:
        listed = await session.list_resources()
        read = await session.read_resource(WIDGET_URI)
        return listed, read

    listed, read = mcp(run)

    [widget] = [resource for resource in listed.resources if resource.uri == WIDGET_URI]
    assert widget.mime_type == APP_MIME_TYPE
    # No domain in its CSP: the page loads and connects to nothing.
    assert widget.meta is not None
    assert widget.meta["ui"] == {"csp": {}, "prefersBorder": True}
    [content] = read.contents
    assert isinstance(content, TextResourceContents)
    assert content.mime_type == APP_MIME_TYPE
    assert content.text == WIDGET_HTML


def test_an_older_address_of_the_widget_answers_the_current_page(mcp) -> None:
    """A client's list of tools from before a deploy, or a past conversation, names an older
    version: it gets the page there is now, not a blank frame."""

    async def run(session: Client) -> Any:
        templates = await session.list_resource_templates()
        return templates, await session.read_resource("ui://candlestack/widget-0123456789ab.html")

    templates, read = mcp(run)

    [template] = templates.resource_templates
    assert template.uri_template == "ui://candlestack/widget-{version}.html"
    assert template.mime_type == APP_MIME_TYPE
    [content] = read.contents
    assert isinstance(content, TextResourceContents)
    assert content.text == WIDGET_HTML
    assert content.mime_type == APP_MIME_TYPE


def test_the_widget_s_name_changes_with_the_page() -> None:
    """A client may keep a resource: a new page after a deploy has a new URI."""
    version = hashlib.sha256(WIDGET_HTML.encode()).hexdigest()[:12]

    assert re.fullmatch(r"ui://candlestack/widget-[0-9a-f]{12}\.html", WIDGET_URI)
    assert version in WIDGET_URI


def test_the_page_is_one_small_file_that_loads_nothing() -> None:
    assert len(WIDGET_HTML.encode()) < 150 * 1024
    assert WIDGET_HTML.startswith("<!doctype html>")
    assert not re.search(r"<script[^>]+src=|<link[^>]+href=|@import", WIDGET_HTML)


def test_the_server_says_it_has_mcp_apps(mcp) -> None:
    """In the 2026-07-28 protocol; the SDK leaves capability extensions out of the initialize
    of earlier ones, where a host goes by the tools' links alone."""

    async def run(session: Client) -> Any:
        return session.server_capabilities

    capabilities = mcp(run)

    assert capabilities.extensions is not None
    assert "io.modelcontextprotocol/ui" in capabilities.extensions


def test_the_tools_that_draw_a_result_link_the_widget(mcp) -> None:
    async def run(session: Client) -> Any:
        return await session.list_tools()

    tools = {tool.name: tool for tool in mcp(run).tools}

    linked = {name for name, tool in tools.items() if tool.meta and "ui" in tool.meta}
    assert linked == SHOWN
    for name in SHOWN:
        meta = tools[name].meta or {}
        assert meta["ui"] == {"resourceUri": WIDGET_URI}
        assert meta["openai/toolInvocation/invoking"]
    assert {spec.name for spec in TOOLS if spec.meta} == SHOWN


class Shown(BaseModel):
    shows: bool


async def show_count(ctx: Context) -> Shown:
    """Says whether the client shows the widget, and gives the widget a chart if so, for the
    tests."""
    shows = apps.shows_widget(ctx)
    if shows:
        apps.attach(ctx, {"points": [1, 2]})
    return Shown(shows=shows)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The app with ``show_count`` next to the real tools."""
    real: CandleStackMCP = app.state.mcp_server
    spec = ToolSpec(
        show_count,
        name="show_count",
        title="Show a count",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example({}, Shown(shows=True)),
    )
    app.state.mcp_server = CandleStackMCP((*real.specs.values(), spec), version=real.version)
    return app


def _show(
    mcp, *, apps_client: bool = False, mode: str = "auto", meta: RequestParamsMeta | None = None
):
    async def run(session: Client) -> CallToolResult:
        return await session.call_tool("show_count", {}, meta=meta)

    return mcp(run, mode=mode, apps=apps_client)


def test_a_client_that_says_it_shows_mcp_apps_gets_the_chart_in_meta(mcp) -> None:
    result = _show(mcp, apps_client=True)

    assert result.structured_content == {"shows": True}
    assert (result.meta or {})[CHART_KEY] == {"points": [1, 2]}
    # The text a model reads has none of it.
    [content] = result.content
    assert content.type == "text"
    assert content.text == '{"shows":true}'


def test_chatgpt_is_known_by_the_meta_of_its_calls(mcp) -> None:
    chatgpt: RequestParamsMeta = {"openai/subject": "v1/abc", "openai/locale": "en-US"}
    result = _show(mcp, meta=chatgpt)

    assert result.structured_content == {"shows": True}
    assert (result.meta or {})[CHART_KEY] == {"points": [1, 2]}


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_any_other_client_gets_the_answer_alone(mcp, mode: str) -> None:
    """Claude Code, Codex, Cursor: the result is as it was, without the widget's chart. A
    legacy client's capabilities came with its initialize, which a stateless call does not
    carry: the widget asks for its chart itself in such a client."""
    result = _show(mcp, mode=mode)

    assert result.structured_content == {"shows": False}
    assert CHART_KEY not in (result.meta or {})
