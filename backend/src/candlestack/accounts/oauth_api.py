"""HTTP API of the OAuth authorization server for MCP clients: ``/api/v1/oauth/...``, the
authorization server metadata (RFC 8414) and the user's connected apps,
``/api/v1/me/connected-apps``.

The client's own endpoints (``authorize``, ``token``, ``register``, ``revoke``) speak OAuth:
errors of ``token``, ``register`` and ``revoke`` are ``{"error", "error_description"}`` JSON, and
``authorize`` sends its errors back to the client's redirect URI, except when the client or that
URI is unknown (a problem then, as nothing proves where to send the browser). The consent page
of the web app reads and decides the request at ``/api/v1/oauth/requests/{request_id}``.
"""

import logging
from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from candlestack.accounts.authorization import (
    ISSUER_PATH,
    AuthorizeParams,
    issuer,
)
from candlestack.accounts.cookies import CookiesDep
from candlestack.accounts.dependencies import (
    AccountsServiceDep,
    AuthorizationServerDep,
    SessionUserDep,
    browser_session,
    limit_registrations,
    sign_in_wait,
)
from candlestack.accounts.errors import (
    AuthorizeRefused,
    NoSuchAuthorizationRequest,
    NoSuchConnectedApp,
    NotAdmitted,
    OAuthError,
    ScopeNotRequested,
)
from candlestack.accounts.schemas import (
    SCOPE_ORDER,
    AuthorizationRequestOut,
    ClientMetadataIn,
    ClientRegistrationOut,
    ConnectedAppsOut,
    ConsentIn,
    ConsentOut,
    OAuthErrorOut,
    TokenEndpointAuthMethod,
    TokenOut,
)
from candlestack.core import (
    NO_STORE,
    ProblemError,
    Settings,
    SettingsDep,
    problem_doc,
    require_same_origin,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["oauth"])

# What a token answer carries (RFC 6749, section 5.1), and an OAuth error.
_TOKEN_HEADERS = {**NO_STORE, "Pragma": "no-cache"}
# The longest form body a token or revocation request may have.
_FORM_MAX_BYTES = 16 * 1024
METADATA_PATHS = (
    "/.well-known/oauth-authorization-server" + ISSUER_PATH,
    ISSUER_PATH + "/.well-known/oauth-authorization-server",
)

_NOT_SIGNED_IN = problem_doc(401, "Not signed in (`not-signed-in`).")
_CROSS_ORIGIN = problem_doc(
    403, "The request does not come from the app's own pages (`cross-origin-request`)."
)
_REQUEST_NOT_FOUND = problem_doc(
    404,
    "No authorization request has this ID: it was decided, expired (10 minutes) or never was "
    "(`authorization-request-not-found`).",
)


def _oauth_error_doc(status: int, description: str) -> dict[int | str, dict[str, Any]]:
    return {status: {"model": OAuthErrorOut, "description": description}}


def _form_schema(required: list[str], properties: dict[str, str]) -> dict[str, Any]:
    """The OpenAPI request body of a form-encoded OAuth request, as the route reads it."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "required": required,
                        "properties": {
                            name: {"type": "string", "description": description}
                            for name, description in properties.items()
                        },
                    }
                }
            },
        }
    }


_CLIENT_FIELDS = {
    "client_id": "The client's ID, unless it authenticates with HTTP Basic",
    "client_secret": "With `client_secret_post`: the client's secret",
}


def _oauth_error_response(error: OAuthError) -> JSONResponse:
    headers = dict(_TOKEN_HEADERS)
    if error.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="CandleStack"'
    return JSONResponse(
        OAuthErrorOut(error=error.error, error_description=error.description).model_dump(),
        status_code=error.status,
        headers=headers,
    )


async def _form(request: Request) -> dict[str, str]:
    """The form of a token or revocation request; a parameter given twice is an error (RFC
    6749, section 3.2).

    Raises:
        OAuthError: ``invalid_request`` for another media type, a body too large or a repeated
            parameter.
    """
    media_type = request.headers.get("content-type", "").partition(";")[0].strip().lower()
    if media_type != "application/x-www-form-urlencoded":
        raise OAuthError("invalid_request", "The body must be application/x-www-form-urlencoded.")
    body = await request.body()
    if len(body) > _FORM_MAX_BYTES:
        raise OAuthError("invalid_request", f"The body is larger than {_FORM_MAX_BYTES} bytes.")
    form: dict[str, str] = {}
    for name, value in parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True):
        if name in form:
            raise OAuthError("invalid_request", f"{name} is given more than once.")
        form[name] = value
    return form


def authorization_server_metadata(settings: Settings) -> dict[str, Any]:
    """The authorization server metadata (RFC 8414)."""
    base = issuer(settings)
    methods = [method.value for method in TokenEndpointAuthMethod]
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "revocation_endpoint": f"{base}/revoke",
        "scopes_supported": [scope.value for scope in SCOPE_ORDER],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": methods,
        "revocation_endpoint_auth_methods_supported": methods,
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": "https://candlestack.tech/docs/mcp/",
    }


async def _metadata(settings: SettingsDep) -> JSONResponse:
    return JSONResponse(
        authorization_server_metadata(settings), headers={"Cache-Control": "public, max-age=3600"}
    )


for _path in METADATA_PATHS:
    router.add_api_route(_path, _metadata, methods=["GET"], include_in_schema=False)


def authorize_params(  # noqa: PLR0913 (the OAuth parameters)
    *,
    response_type: str | None = None,
    client_id: str | None = None,
    redirect_uri: str | None = None,
    state: str | None = None,
    code_challenge: str | None = None,
    code_challenge_method: str | None = None,
    scope: str | None = None,
    resource: Annotated[
        str | None, Query(description="The MCP server's URL (RFC 8707), `<PUBLIC_URL>/mcp`")
    ] = None,
) -> AuthorizeParams:
    return AuthorizeParams(
        response_type=response_type,
        client_id=client_id,
        redirect_uri=redirect_uri,
        state=state,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        resource=resource,
    )


@router.get(
    "/api/v1/oauth/authorize",
    status_code=302,
    response_class=RedirectResponse,
    responses={
        **problem_doc(
            400,
            "The client_id or the redirect_uri is unknown, so the browser cannot be sent back "
            "(`invalid-authorization-request`).",
        ),
        **problem_doc(429, "Too many sign-in requests from this address (`rate-limited`)."),
    },
)
async def authorize(
    request: Request,
    *,
    params: Annotated[AuthorizeParams, Depends(authorize_params)],
    server: AuthorizationServerDep,
    service: AccountsServiceDep,
    cookies: CookiesDep,
    wait: Annotated[int, Depends(sign_in_wait)],
) -> RedirectResponse:
    """The authorization endpoint (OAuth 2.1, authorization code with PKCE S256): an MCP
    client sends the browser here. Redirects to the consent page `/oauth/consent?request=<id>`,
    through `/signin?next=...` when the browser is not signed in, or back to the client's
    `redirect_uri` with `error` (`unsupported_response_type`, `invalid_request` without an S256
    `code_challenge`, `invalid_target` for another `resource`), `state` and `iss`. `scope` names
    any of read, backtests, models and publish (all of them when it names none); the user picks
    on the consent page."""
    if wait:
        raise ProblemError(
            429,
            "rate-limited",
            "Too many requests",
            f"Too many sign-in requests from this address. Retry in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    session = await browser_session(request, service, cookies)
    try:
        url = await server.authorize(params, signed_in=session is not None)
    except AuthorizeRefused as refused:
        raise ProblemError(
            400, "invalid-authorization-request", "Invalid authorization request", refused.reason
        ) from None
    response = RedirectResponse(url, status_code=302)
    if session is not None:
        session.slide_on(response)
    return response


@router.post(
    "/api/v1/oauth/token",
    response_model=TokenOut,
    responses={
        **_oauth_error_doc(
            400,
            "`invalid_request`, `invalid_grant` (the code or refresh token is unknown, used, "
            "expired or revoked, the verifier does not match, the account left the beta list), "
            "`invalid_scope`, `invalid_target` or `unsupported_grant_type`",
        ),
        **_oauth_error_doc(401, "`invalid_client`"),
    },
    openapi_extra=_form_schema(
        ["grant_type"],
        {
            "grant_type": "`authorization_code` or `refresh_token`",
            "code": "With `authorization_code`: the code from the redirect",
            "redirect_uri": "With `authorization_code`: the redirect URI of the request",
            "code_verifier": "With `authorization_code`: the PKCE verifier",
            "refresh_token": "With `refresh_token`: the refresh token, used once",
            "scope": "With `refresh_token`: fewer scopes than granted, space-separated",
            "resource": "The MCP server's URL (RFC 8707)",
            **_CLIENT_FIELDS,
        },
    ),
)
async def token(request: Request, server: AuthorizationServerDep) -> Response:
    """The token endpoint: trades a code and its PKCE verifier, or a refresh token, for an
    access token (`csa_...`, an hour) and a new refresh token (`csr_...`, 30 days, works once;
    bringing a used one again revokes every token of that sign-in). A public client sends its
    `client_id`; a client with a secret authenticates as it registered."""
    try:
        tokens = await server.token(await _form(request), request.headers.get("authorization"))
    except OAuthError as error:
        return _oauth_error_response(error)
    return JSONResponse(tokens.model_dump(), headers=_TOKEN_HEADERS)


@router.post(
    "/api/v1/oauth/register",
    status_code=201,
    response_model=ClientRegistrationOut,
    responses={
        **_oauth_error_doc(400, "`invalid_client_metadata` or `invalid_redirect_uri`"),
        **problem_doc(429, "Too many registrations from this address (`rate-limited`)."),
    },
    dependencies=[Depends(limit_registrations)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        key: value
                        for key, value in ClientMetadataIn.model_json_schema(
                            ref_template="#/components/schemas/{model}"
                        ).items()
                        if key != "$defs"
                    }
                }
            },
        }
    },
)
async def register(request: Request, server: AuthorizationServerDep) -> Response:
    """Dynamic client registration (RFC 7591): an MCP client registers itself and gets its
    `client_id`, and a `client_secret` when it asked for `client_secret_post` or
    `client_secret_basic` (the default). Clients that publish a client ID metadata document use
    its URL as their `client_id` instead and need no registration."""
    try:
        body = await request.json()
    except ValueError:
        return _oauth_error_response(
            OAuthError("invalid_client_metadata", "The body must be a JSON object.")
        )
    try:
        registered = await server.register(body)
    except OAuthError as error:
        return _oauth_error_response(error)
    return JSONResponse(
        registered.model_dump(mode="json", exclude_none=True),
        status_code=201,
        headers=_TOKEN_HEADERS,
    )


@router.post(
    "/api/v1/oauth/revoke",
    status_code=200,
    responses={
        **_oauth_error_doc(400, "`invalid_request`: no token"),
        **_oauth_error_doc(401, "`invalid_client`"),
    },
    openapi_extra=_form_schema(
        ["token"],
        {
            "token": "The access or refresh token to revoke",
            "token_type_hint": "`access_token` or `refresh_token`; not needed",
            **_CLIENT_FIELDS,
        },
    ),
)
async def revoke(request: Request, server: AuthorizationServerDep) -> Response:
    """Token revocation (RFC 7009): a refresh token ends with every token of its sign-in, an
    access token alone. 200 also for a token that is unknown or another client's."""
    try:
        await server.revoke(await _form(request), request.headers.get("authorization"))
    except OAuthError as error:
        return _oauth_error_response(error)
    return Response(status_code=200, headers=_TOKEN_HEADERS)


RequestId = Annotated[str, Path(description="The ID from the consent page's address")]


def _no_such_request() -> ProblemError:
    return ProblemError(
        404,
        "authorization-request-not-found",
        "Authorization request not found",
        "This request to connect an app was decided already, expired after 10 minutes or never "
        "was. Start connecting again from the app.",
    )


@router.get("/api/v1/oauth/requests/{request_id}", responses=_REQUEST_NOT_FOUND)
async def authorization_request(
    request_id: RequestId, server: AuthorizationServerDep, response: Response
) -> AuthorizationRequestOut:
    """An app's request for access, for the consent page: the app as it names itself, the host
    it returns to and the scopes it asks for in plain words. Needs no session, so the page can
    name the app before the user signs in; the ID is the secret."""
    response.headers.update(NO_STORE)
    try:
        return await server.describe(request_id)
    except NoSuchAuthorizationRequest:
        raise _no_such_request() from None


@router.post(
    "/api/v1/oauth/requests/{request_id}",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403,
            "The request does not come from the app's own pages (`cross-origin-request`), or "
            "the account is not on the beta list (`not-on-beta-list`).",
        ),
        **_REQUEST_NOT_FOUND,
        **problem_doc(422, "A scope the app did not ask for (`scope-not-requested`; `scope`)."),
    },
    dependencies=[Depends(require_same_origin)],
)
async def decide(
    request_id: RequestId,
    body: ConsentIn,
    user: SessionUserDep,
    server: AuthorizationServerDep,
    response: Response,
) -> ConsentOut:
    """The user's decision on an app's request, once: `allow` with the scopes to grant (read
    always), or `deny`. Answers where the browser goes back to the app: its redirect URI with a
    `code` (5 minutes, used once) or `error=access_denied`, with `state` and `iss`."""
    response.headers.update(NO_STORE)
    try:
        return await server.decide(user, request_id, body)
    except NoSuchAuthorizationRequest:
        raise _no_such_request() from None
    except NotAdmitted:
        raise ProblemError(
            403,
            "not-on-beta-list",
            "Not on the beta list",
            "Your account is not on the beta list, so it cannot connect apps.",
        ) from None
    except ScopeNotRequested as error:
        raise ProblemError(
            422,
            "scope-not-requested",
            "Scope not requested",
            f"The app did not ask for the scope {error.scope}, so it cannot be granted.",
            extensions={"scope": error.scope},
        ) from None


AppId = Annotated[
    str, Path(description="Public ID of the connected app", examples=["app_7f3k2m4qx4"])
]


@router.get("/api/v1/me/connected-apps", responses=_NOT_SIGNED_IN)
async def connected_apps(
    user: SessionUserDep, server: AuthorizationServerDep, response: Response
) -> ConnectedAppsOut:
    """The apps (MCP clients such as Claude and ChatGPT) the user allowed to act for them, the
    most recently connected first."""
    response.headers.update(NO_STORE)
    return await server.connected_apps(user)


@router.delete(
    "/api/v1/me/connected-apps/{app_id}",
    status_code=204,
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **problem_doc(404, "The user has no connected app with this ID (`not-found`)."),
    },
    dependencies=[Depends(require_same_origin)],
)
async def disconnect_app(
    app_id: AppId, user: SessionUserDep, server: AuthorizationServerDep
) -> None:
    """Disconnects the app: its tokens stop working at once. It can connect again with a new
    consent."""
    try:
        await server.disconnect(user, app_id)
    except NoSuchConnectedApp:
        raise ProblemError(
            404, "not-found", "Not Found", "You have no connected app with this ID."
        ) from None
