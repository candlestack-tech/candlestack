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

The transport is stateless with JSON answers: every request stands alone, so any process can
answer it and a deploy breaks no session. ``serving_mcp`` runs the SDK's session manager with
the app (its lifespan); ``mount_mcp`` adds the routes.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from candlestack.accounts import Scope as KeyScope
from candlestack.accounts import is_access_token, key_caller, oauth_issuer, token_caller
from candlestack.core import ProblemError, Settings, bearer_token
from candlestack.mcp.convention import CALLER_STATE
from candlestack.mcp.docs import MCP_DOCS
from candlestack.mcp.server import TITLE, build_server

MCP_PATH = "/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource" + MCP_PATH


def resource_url(settings: Settings) -> str:
    """The server's address, which identifies it as an OAuth protected resource."""
    return settings.public_url + MCP_PATH


def _challenge(settings: Settings, error: str | None = None) -> str:
    """The ``WWW-Authenticate`` of a 401: the metadata URL, and `error` for a key that failed."""
    error_param = "" if error is None else f'error="{error}", '
    return f'Bearer {error_param}resource_metadata="{settings.public_url}{METADATA_PATH}"'


async def _sign_in(request: Request, settings: Settings) -> None:
    """Puts the caller of the request on ``request.state``; raises the problem that refuses it."""
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
                )
            else:
                caller = await key_caller(
                    token, settings=settings, db=db, limiter=state.rate_limiter
                )
        except ProblemError as problem:
            if problem.status == 401:
                problem.headers["WWW-Authenticate"] = _challenge(settings, "invalid_token")
            raise
    setattr(request.state, CALLER_STATE, caller)


class _McpEndpoint:
    """The ``/mcp`` endpoint, an ASGI app for the SDK: signs the request in, then hands it on."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope, receive)
        state = request.app.state
        await _sign_in(request, state.settings)
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
