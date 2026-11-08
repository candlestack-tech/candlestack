"""The chat widget: a page a chat client that shows interactive UI (MCP Apps in Claude, ChatGPT's
Apps SDK) draws a tool's answer with, as charts and tiles; a client without it reads the text,
which the widget leaves as it was.

The page is ``widget.html``, built from ``frontend/src/widget/`` (``npm run build:widget``) into
one file with its script and styles inline. The server serves it as the ``ui://`` resource
``WIDGET_URI``, whose name changes with its content, so a client that keeps resources fetches a
new page after a deploy. It loads nothing from the network (its CSP allows no domain) and calls
no API: it draws what the tool answered.

A tool that shows it carries ``tool_meta()`` in its ``_meta``: the MCP Apps link
(``ui.resourceUri``), which ChatGPT follows as well. The text of its answer stays what it was.
What the widget draws beyond the answer (the series of a chart, which a model has no use for
and which would cost it thousands of tokens) goes in the result's ``_meta`` under ``CHART_KEY``
(``attach``): a host gives ``_meta`` to the widget and not to the model. A tool adds it only for
a client that shows the widget (``shows_widget``); where a client shows it without saying so,
the widget asks for it itself, calling a tool through the host.
"""

import hashlib
from pathlib import Path
from typing import Any

from mcp.server.apps import APP_MIME_TYPE, Apps, client_supports_apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.resources import TextResource
from starlette.requests import Request

WIDGET_HTML = Path(__file__).with_name("widget.html").read_text(encoding="utf-8")
WIDGET_VERSION = hashlib.sha256(WIDGET_HTML.encode()).hexdigest()[:12]
WIDGET_URI = f"ui://candlestack/widget-{WIDGET_VERSION}.html"
# Any other version's address answers the current page: a client may hold an older one (its list
# of tools from before a deploy, a past conversation), and an older page reads the same answers.
WIDGET_TEMPLATE = "ui://candlestack/widget-{version}.html"
# Where a result's ``_meta`` has the series of its charts, for the widget.
CHART_KEY = "candlestack.tech/chart"
# Where the HTTP request of a tool call keeps what its result's ``_meta`` gets (``attach``).
_RESULT_META_STATE = "mcp_result_meta"
# The prefix of the keys ChatGPT puts in the ``_meta`` of every tool call (openai/subject, ...).
_CHATGPT_META = "openai/"

_DESCRIPTION = (
    "Draws a CandleStack backtest, comparison or model: the equity and drawdown charts, the "
    "statistics, how the model reads the market, and links to open them in the app. It shows "
    "what the tool answered and adds no finding of its own."
)


# The resource's _meta: no domain in its CSP, so the page loads and connects to nothing.
_RESOURCE_META = {
    "ui": {"csp": {}, "prefersBorder": True},
    # Tells ChatGPT's model what the user already sees, so it need not repeat it.
    "openai/widgetDescription": _DESCRIPTION,
}


def tool_meta(invoking: str, invoked: str) -> dict[str, Any]:
    """The ``_meta`` of a tool the widget shows: the MCP Apps link to it (ChatGPT reads it too),
    and what ChatGPT says while the tool runs (`invoking`) and once it answered (`invoked`)."""
    return {
        "ui": {"resourceUri": WIDGET_URI},
        "openai/toolInvocation/invoking": invoking,
        "openai/toolInvocation/invoked": invoked,
    }


def widget_extension() -> Apps:
    """The MCP Apps extension of the server, with the widget as its one resource: the server
    then says in its capabilities that it has MCP Apps."""
    apps = Apps()
    apps.add_resource(
        TextResource(
            uri=WIDGET_URI,
            name="widget",
            title="CandleStack widget",
            description=_DESCRIPTION,
            mime_type=APP_MIME_TYPE,
            text=WIDGET_HTML,
            meta=_RESOURCE_META,
        )
    )
    return apps


def add_widget_versions(server: MCPServer) -> None:
    """Serves the current page at every version's address (``WIDGET_TEMPLATE``)."""

    def widget(version: str) -> str:
        del version  # Every version gets the page there is now.
        return WIDGET_HTML

    server.resource(
        WIDGET_TEMPLATE,
        name="widget-version",
        title="CandleStack widget, any version",
        description=_DESCRIPTION,
        mime_type=APP_MIME_TYPE,
        meta=_RESOURCE_META,
    )(widget)


def shows_widget(ctx: Context) -> bool:
    """The client of the call shows the widget: it said it shows MCP Apps (in its capabilities,
    which a call carries in the 2026-07-28 protocol), or it is ChatGPT, which always does and
    marks each call's ``_meta`` with its ``openai/`` keys. A tool then adds its charts' series."""
    if client_supports_apps(ctx):
        return True
    meta = ctx.request_context.meta or {}
    return any(key.startswith(_CHATGPT_META) for key in meta)


def _request(ctx: Context) -> Request | None:
    request = ctx.request_context.request
    return request if isinstance(request, Request) else None


def attach(ctx: Context, chart: dict[str, Any]) -> None:
    """Gives the result of the call `chart` in its ``_meta``, for the widget alone."""
    request = _request(ctx)
    if request is not None:
        setattr(request.state, _RESULT_META_STATE, {CHART_KEY: chart})


def attached(ctx: Context) -> dict[str, Any] | None:
    """What the tool of the call gave its result's ``_meta`` (``attach``), if anything."""
    request = _request(ctx)
    return None if request is None else getattr(request.state, _RESULT_META_STATE, None)
