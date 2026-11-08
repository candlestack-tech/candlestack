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

The key is checked first; then the body is read, at most ``BODY_MAX`` bytes (else
``413 request-too-large``), and the request is counted against the rate limit of the key or the
connected app by what it asks: a request that sets up a session or lists what the server offers
(``_LIGHT``) and notifications count against a looser limit of their own. A ``tools/call`` over
the limit is answered as the tool's error with the problem ``rate-limited`` and its
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

from candlestack.accounts import (
    Caller,
    count_request,
    is_access_token,
    key_caller,
    oauth_issuer,
    token_caller,
)
from candlestack.accounts import Scope as KeyScope
from candlestack.core import ProblemError, Settings, bearer_token
from candlestack.mcp.convention import CALLER_STATE, problem_result
from candlestack.mcp.docs import MCP_DOCS
from candlestack.mcp.server import TITLE, build_server

MCP_PATH = "/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource" + MCP_PATH
# The largest body a request may have: the largest a tool takes is a manifest of 1 MiB as JSON.
BODY_MAX = 2 * 1024 * 1024
# The methods that set up a session or list what the server offers, which a client sends when
# it connects: they count against the looser limit of light requests, as notifications do.
_LIGHT = frozenset(
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


async def _sign_in(request: Request, settings: Settings, token: str) -> Caller:
    """The caller of the request with `token`, the request not counted against the rate limit
    yet; raises the problem that refuses it."""
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
                    counted=False,
                )
            else:
                caller = await key_caller(
                    token, settings=settings, db=db, limiter=state.rate_limiter, counted=False
                )
        except ProblemError as problem:
            if problem.status == 401:
                problem.headers["WWW-Authenticate"] = _challenge(settings, "invalid_token")
            raise
    return caller


def _too_large() -> ProblemError:
    return ProblemError(
        413,
        "request-too-large",
        "Request too large",
        f"The request's body is larger than {BODY_MAX // 1024 // 1024} MB, the most the MCP "
        "server reads.",
    )


async def _body(request: Request) -> bytes:
    """The request's body, read until ``BODY_MAX``; raises ``413 request-too-large`` when it is
    larger, by its Content-Length before anything is read."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > BODY_MAX:
        raise _too_large()
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > BODY_MAX:
            raise _too_large()
        chunks.append(chunk)
    return b"".join(chunks)


def _message(body: bytes) -> dict[str, Any] | None:
    """The JSON-RPC message a POST carries; None for a body that is not one, which the SDK
    refuses."""
    try:
        message = orjson.loads(body)
    except orjson.JSONDecodeError:
        return None
    return message if isinstance(message, dict) else None


def _light(message: dict[str, Any] | None) -> bool:
    """Whether the request with `message` only connects or lists: a notification or one of the
    methods of ``_LIGHT``."""
    method = None if message is None else message.get("method")
    return isinstance(method, str) and (method in _LIGHT or method.startswith("notifications/"))


def _call_id(message: dict[str, Any] | None) -> str | int | None:
    """The JSON-RPC ID of a tools/call `message`, which its answer must carry; None for any
    other message or an ID that is not a string or an integer."""
    if message is None or message.get("method") != "tools/call":
        return None
    call_id = message.get("id")
    if isinstance(call_id, str) or (isinstance(call_id, int) and not isinstance(call_id, bool)):
        return call_id
    return None


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


def _tool_error(call_id: str | int, problem: ProblemError) -> Response:
    """The answer to the tools/call with the ID `call_id` as the tool's error with `problem`."""
    result = problem_result(problem).model_dump(mode="json", by_alias=True, exclude_none=True)
    answer = JSONRPCResponse(jsonrpc="2.0", id=call_id, result=result)
    return Response(
        answer.model_dump_json(by_alias=True, exclude_unset=True), media_type="application/json"
    )


class _McpEndpoint:
    """The ``/mcp`` endpoint, an ASGI app for the SDK: signs the request in, then hands it on."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        state = request.app.state
        token = _token(request, state.settings)
        caller = await _sign_in(request, state.settings, token)
        message = None
        if request.method == "POST":
            body = await _body(request)
            message = _message(body)
            receive = _replayed(body, receive)
        try:
            await count_request(state.rate_limiter, caller, light=_light(message))
        except ProblemError as problem:
            call_id = _call_id(message)
            if call_id is None:
                raise
            await _tool_error(call_id, problem)(scope, receive, send)
            return
        setattr(request.state, CALLER_STATE, caller)
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
