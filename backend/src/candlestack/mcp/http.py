"""The MCP server over HTTP: Streamable HTTP at ``/mcp`` in the app, signed in with an API key
or an OAuth access token.

Every request to ``/mcp`` needs ``Authorization: Bearer``: an API key (``cs_...``), checked as
on a route (the beta list, the key's rate limit, its last use), or an access token of the app's
OAuth authorization server (``csa_...``, what claude.ai and ChatGPT sign in with), checked the
same way and bound to this server; the user and scopes go to the tools on ``request.state``.
Without either the answer is 401 with ``WWW-Authenticate: Bearer resource_metadata=...``, as the
MCP authorization spec asks: the URL of the protected resource metadata (RFC 9728) at
``/.well-known/oauth-protected-resource/mcp``, which names the server, its authorization server,
its scopes and docs. A request from a browser page of another origin is refused, against DNS
rebinding.

The rate limit of the key or the connected app counts the requests that do work: a request that
sets up a session or lists what the server offers (``_FREE``) does not count. A ``tools/call``
over the limit is answered as the tool's error with the problem ``rate-limited`` and its
``retry_after_s``, which the model reads, rather than as an HTTP 429, which a client may only
show as a failed connection.

The transport is stateless with JSON answers: every request stands alone, so any process can
answer it and a deploy breaks no session. ``serving_mcp`` runs the SDK's session manager with
the app (its lifespan); ``mount_mcp`` adds the routes.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import orjson
from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import JSONRPCResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import Message, Receive, Scope, Send

from candlestack.accounts import Scope as KeyScope
from candlestack.accounts import is_access_token, key_caller, oauth_issuer, token_caller
from candlestack.core import ProblemError, Settings, bearer_token
from candlestack.mcp.convention import CALLER_STATE, problem_result
from candlestack.mcp.docs import MCP_DOCS
from candlestack.mcp.server import TITLE, build_server

MCP_PATH = "/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource" + MCP_PATH
# The methods that set up a session or list what the server offers, which a client sends when
# it connects: they do not count against the rate limit, nor do notifications.
_FREE = frozenset(
    {
        "initialize",
        "server/discover",
        "ping",
        "tools/list",
        "resources/list",
        "resources/templates/list",
        "prompts/list",
    }
)


def resource_url(settings: Settings) -> str:
    """The server's address, which identifies it as an OAuth protected resource."""
    return settings.public_url + MCP_PATH


def _challenge(settings: Settings, error: str | None = None) -> str:
    """The ``WWW-Authenticate`` of a 401: the metadata URL, and `error` for a key that failed."""
    error_param = "" if error is None else f'error="{error}", '
    return f'Bearer {error_param}resource_metadata="{settings.public_url}{METADATA_PATH}"'


def _token(request: Request, settings: Settings) -> str:
    """The request's bearer token; raises the problem that refuses a request without one or
    from a page of another site."""
    origin = request.headers.get("origin")
    if origin is not None and origin != settings.public_url:
        raise ProblemError(
            403,
            "cross-origin-request",
            "Cross-origin request",
            "The MCP server does not answer pages of other sites. Connect from an MCP client.",
        )
    token = bearer_token(request)
    if token is None:
        raise ProblemError(
            401,
            "not-signed-in",
            "Not signed in",
            "The MCP server needs a sign-in: connect from an MCP client with OAuth, or send an "
            "API key as Authorization: Bearer cs_... Create a key in Settings, API and MCP.",
            headers={"WWW-Authenticate": _challenge(settings)},
        )
    return token


async def _sign_in(request: Request, settings: Settings, token: str, cost: int) -> None:
    """Puts the caller of the request with `token` on ``request.state``, the request counting
    `cost` against the rate limit; raises the problem that refuses it."""
    state = request.app.state
    async with state.db.sessions() as db:
        try:
            if is_access_token(token):
                caller = await token_caller(
                    token,
                    resource=resource_url(settings),
                    settings=settings,
                    db=db,
                    limiter=state.rate_limiter,
                    cost=cost,
                )
            else:
                caller = await key_caller(
                    token, settings=settings, db=db, limiter=state.rate_limiter, cost=cost
                )
        except ProblemError as problem:
            if problem.status == 401:
                problem.headers["WWW-Authenticate"] = _challenge(settings, "invalid_token")
            raise
    setattr(request.state, CALLER_STATE, caller)


def _message(body: bytes) -> dict[str, Any] | None:
    """The JSON-RPC message a POST carries; None for a body that is not one, which the SDK
    refuses."""
    try:
        message = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def _cost(message: dict[str, Any] | None) -> int:
    """What the request with `message` counts against the rate limit: 0 for a notification and
    the methods of ``_FREE``, else 1."""
    method = None if message is None else message.get("method")
    if isinstance(method, str) and (method in _FREE or method.startswith("notifications/")):
        return 0
    return 1


def _replayed(body: bytes, receive: Receive) -> Receive:
    """`receive` with the body that was read from it given again first, for the SDK."""
    pending = True

    async def replay() -> Message:
        nonlocal pending
        if pending:
            pending = False
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return replay


def _tool_error(message: dict[str, Any], problem: ProblemError) -> Response:
    """The answer to the tools/call `message` as the tool's error with `problem`."""
    result = problem_result(problem).model_dump(mode="json", by_alias=True, exclude_none=True)
    answer = JSONRPCResponse(jsonrpc="2.0", id=message["id"], result=result)
    return Response(
        answer.model_dump_json(by_alias=True, exclude_unset=True), media_type="application/json"
    )


class _McpEndpoint:
    """The ``/mcp`` endpoint, an ASGI app for the SDK: signs the request in, then hands it on."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        state = request.app.state
        token = _token(request, state.settings)
        message = None
        if request.method == "POST":
            body = await request.body()
            message = _message(body)
            receive = _replayed(body, receive)
        try:
            await _sign_in(request, state.settings, token, _cost(message))
        except ProblemError as problem:
            calls_a_tool = message is not None and message.get("method") == "tools/call"
            if problem.slug != "rate-limited" or not calls_a_tool or "id" not in message:
                raise
            await _tool_error(message, problem)(scope, receive, send)
            return
        await state.mcp_sessions.handle_request(scope, receive, send)


async def _metadata(request: Request) -> JSONResponse:
    """The protected resource metadata of the server (RFC 9728)."""
    settings: Settings = request.app.state.settings
    return JSONResponse(
        {
            "resource": resource_url(settings),
            "resource_name": TITLE,
            "authorization_servers": [oauth_issuer(settings)],
            "resource_documentation": MCP_DOCS,
            "bearer_methods_supported": ["header"],
            "scopes_supported": [scope.value for scope in KeyScope],
        },
        headers={"Cache-Control": "public, max-age=3600"},
    )


def mount_mcp(app: FastAPI) -> None:
    """Adds ``/mcp`` and its metadata to the app (not to the API reference) and builds the
    server on ``app.state.mcp_server``; ``serving_mcp`` must run in the app's lifespan."""
    # The SDK logs every stateless request at INFO ("Terminating session: None"); its warnings
    # and errors still come through, and the tools' failures are logged by the server.
    logging.getLogger("mcp").setLevel(logging.WARNING)
    app.state.mcp_server = build_server(app.state.settings.app_version)
    app.router.routes.append(Route(MCP_PATH, _McpEndpoint()))
    app.router.routes.append(Route(METADATA_PATH, _metadata, methods=["GET"]))


@asynccontextmanager
async def serving_mcp(app: FastAPI) -> AsyncIterator[None]:
    """Runs the SDK's session manager while the app runs; on shutdown the calls in progress
    are cancelled."""
    server = app.state.mcp_server
    # A new session manager for each run of the app (the SDK runs one only once). The Host
    # check of the SDK is off: the edge sets the host, and _sign_in checks the origin.
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    sessions = server.session_manager
    app.state.mcp_sessions = sessions
    async with sessions.run():
        yield
