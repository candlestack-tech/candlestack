"""What the routes of every module depend on to know who sends a request, the browser's
session or an API key, and the sign-in rate limit.

Which dependency a route takes decides what an API key may do there; a new route is closed to
keys until it says otherwise:

- ``CurrentUserDep``: the session, or on a GET a key with the scope ``read``. A key on a route
  that changes something gets 403 ``session-required``.
- ``BacktestsUserDep``, ``ModelsUserDep``: the session, or a key with that scope.
- ``CallerDep``: the session or any valid key; the route checks the scopes itself with
  ``Caller.require``, for a body whose fields need different scopes.
- ``SessionUserDep``: the session only (managing keys, deleting the account, sign-in methods).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Response, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, SecurityScopes
from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.accounts.cookies import SESSION, Cookies, CookiesDep
from candlestack.accounts.keys import ApiKeys
from candlestack.accounts.models import User
from candlestack.accounts.oauth import OAuthClient
from candlestack.accounts.pending import PendingStore
from candlestack.accounts.schemas import Scope
from candlestack.accounts.service import SESSION_MAX_AGE, AccountDeletionHook, AccountsService
from candlestack.accounts.store import AccountStore
from candlestack.core import (
    DbSessionDep,
    HttpClientDep,
    ProblemError,
    RateLimiter,
    RateLimiterDep,
    RedisDep,
    Settings,
    SettingsDep,
    bearer_token,
    client_address,
)

# Sign-in requests (start, callback, dev sign-in) per minute and client address: a person needs
# a few; more is someone trying states or codes.
SIGN_IN_RATE_LIMIT = 20
# Requests per minute and API key: a script or an agent at a steady two a second.
API_KEY_RATE_LIMIT = 120
# The name of the API key scheme in the API reference.
API_KEY_SCHEME = "apiKey"

_BEARER = HTTPBearer(
    scheme_name=API_KEY_SCHEME,
    bearerFormat="cs_...",
    description=(
        "An API key from Settings, API and MCP: `Authorization: Bearer cs_...`. Its scopes "
        "decide what it may do: `read` every GET, `backtests` start, rename and star backtests, "
        "`models` upload models, add their manifests, rename, star and delete them, `publish` "
        "publish and unpublish backtests. Managing keys, deleting the account, sign-in methods "
        "and signing out need the browser session. The web app signs in with its session "
        "cookie instead."
    ),
    auto_error=False,
)
_READ_METHODS = frozenset({"GET", "HEAD"})


def get_deletion_hooks(request: Request) -> Sequence[AccountDeletionHook]:
    """What else goes with a deleted account: the hooks ``candlestack.main`` puts on the app."""
    return request.app.state.account_deletion_hooks


def get_accounts_service(
    settings: SettingsDep,
    redis: RedisDep,
    db: DbSessionDep,
    http: HttpClientDep,
    deletion_hooks: Annotated[Sequence[AccountDeletionHook], Depends(get_deletion_hooks)],
) -> AccountsService:
    return AccountsService(
        settings,
        AccountStore(db),
        PendingStore(redis),
        OAuthClient(settings, http),
        deletion_hooks=deletion_hooks,
    )


AccountsServiceDep = Annotated[AccountsService, Depends(get_accounts_service)]


def get_api_keys(settings: SettingsDep, db: DbSessionDep) -> ApiKeys:
    return ApiKeys(settings, AccountStore(db))


ApiKeysDep = Annotated[ApiKeys, Depends(get_api_keys)]


@dataclass(frozen=True, slots=True)
class BrowserSession:
    """The session whose cookie came with a request: its token and user."""

    token: str
    user: User
    extended: bool
    cookies: Cookies

    def slide_on(self, response: Response) -> None:
        """Sends the cookie again with the new expiry on `response` when the session was just
        extended, so a session in use slides on. Every response to a signed-in browser
        passes here."""
        if self.extended:
            self.cookies.set(response, SESSION, self.token, SESSION_MAX_AGE)


async def browser_session(
    request: Request, service: AccountsService, cookies: Cookies
) -> BrowserSession | None:
    """The session whose cookie came with the request; None without a cookie, or when its
    session ended. Looking it up may extend it: the caller sends the response it returns
    through `BrowserSession.slide_on`."""
    token = cookies.read(request, SESSION)
    signed_in = None if token is None else await service.signed_in(token)
    if token is None or signed_in is None:
        return None
    return BrowserSession(token, signed_in.user, signed_in.extended, cookies)


@dataclass(frozen=True, slots=True)
class Caller:
    """Who sent a request: the user, and the scopes of the API key it came with; ``scopes`` is
    None for the browser session, which may do everything."""

    user: User
    scopes: frozenset[Scope] | None = None

    def require(self, *scopes: Scope) -> None:
        """403 ``missing-scope``, naming the first scope the key lacks; the session passes."""
        for scope in scopes:
            if self.scopes is not None and scope not in self.scopes:
                raise ProblemError(
                    403,
                    "missing-scope",
                    "Missing scope",
                    f"This API key does not have the scope {scope.value}, which this request "
                    "needs. Create a key with it in Settings, API and MCP.",
                    extensions={"scope": scope.value},
                )


def _session_required(settings: Settings) -> ProblemError:
    return ProblemError(
        403,
        "session-required",
        "Session required",
        "API keys cannot do this: it needs the signed-in browser. Do it in the CandleStack app "
        f"at {settings.public_url}.",
    )


async def _session_user(
    request: Request, response: Response, service: AccountsService, cookies: Cookies
) -> User:
    """The user whose session cookie came with the request; 401 ``not-signed-in`` without one.

    The session slides on through the response FastAPI builds from what the route returns. A
    route that returns a ``Response`` of its own loses that cookie, so routes of the signed-in
    user return their data.
    """
    session = await browser_session(request, service, cookies)
    if session is None:
        raise ProblemError(
            401,
            "not-signed-in",
            "Not signed in",
            "This needs a signed-in user, and the request has no session or its session ended. "
            "Sign in again.",
        )
    session.slide_on(response)
    return session.user


async def _key_caller(token: str, keys: ApiKeys, limiter: RateLimiter) -> Caller:
    """The caller of a request with the API key `token`: 401 ``invalid-api-key`` unless it
    signs in, 429 ``rate-limited`` beyond ``API_KEY_RATE_LIMIT`` requests a minute."""
    signed_in = await keys.sign_in(token)
    if signed_in is None:
        raise ProblemError(
            401,
            "invalid-api-key",
            "Invalid API key",
            "The API key is unknown, revoked or expired, or its account is no longer on the beta "
            "list. Create a new key in Settings, API and MCP.",
            headers={"WWW-Authenticate": 'Bearer error="invalid_token"'},
        )
    wait = await limiter.hit(f"accounts:key-rl:{signed_in.key.id}", API_KEY_RATE_LIMIT)
    if wait > 0:
        seconds = max(1, math.ceil(wait))
        raise ProblemError(
            429,
            "rate-limited",
            "Too many requests",
            f"More than {API_KEY_RATE_LIMIT} requests in one minute with this API key. Retry in "
            f"{seconds} seconds.",
            headers={"Retry-After": str(seconds)},
            extensions={"limit": API_KEY_RATE_LIMIT},
        )
    await keys.mark_used(signed_in.key)
    return Caller(signed_in.user, signed_in.scopes)


async def key_caller(
    token: str, *, settings: Settings, db: AsyncSession, limiter: RateLimiter
) -> Caller:
    """The caller of a request with the API key `token`, for what signs requests in outside the
    routes' dependencies (the MCP server): the same checks, rate limit and last use as a route's,
    on the session `db`. Scopes are not checked here.

    Raises:
        ProblemError: 401 ``invalid-api-key`` or 429 ``rate-limited``.
    """
    return await _key_caller(token, ApiKeys(settings, AccountStore(db)), limiter)


async def authenticate(  # noqa: PLR0913 (the request and what signs it in)
    request: Request,
    response: Response,
    *,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER)],
    service: AccountsServiceDep,
    keys: ApiKeysDep,
    cookies: CookiesDep,
    limiter: RateLimiterDep,
) -> Caller:
    """Who sent the request: the API key of its ``Authorization: Bearer`` header (any cookie is
    then ignored), else the session of its cookie. Scopes are not checked here."""
    if credentials is not None:
        return await _key_caller(credentials.credentials, keys, limiter)
    return Caller(await _session_user(request, response, service, cookies))


async def current_caller(
    security_scopes: SecurityScopes,
    request: Request,
    settings: SettingsDep,
    caller: Annotated[Caller, Depends(authenticate)],
) -> Caller:
    """The caller, when its key has the scopes the route declares (``Security(...,
    scopes=[...])``), or ``read`` on a GET route that declares none; a key on any other route
    gets 403 ``session-required``."""
    if caller.scopes is None:
        return caller
    declared = [Scope(scope) for scope in security_scopes.scopes]
    if not declared:
        if request.method not in _READ_METHODS:
            raise _session_required(settings)
        declared = [Scope.read]
    caller.require(*declared)
    return caller


async def current_user(caller: Annotated[Caller, Security(current_caller)]) -> User:
    return caller.user


def reject_api_keys(request: Request, settings: SettingsDep) -> None:
    """403 ``session-required`` for a request with an API key, whatever its key: for the
    routes that need the browser's session."""
    if bearer_token(request) is not None:
        raise _session_required(settings)


async def session_user(
    request: Request,
    response: Response,
    service: AccountsServiceDep,
    cookies: CookiesDep,
    _no_key: Annotated[None, Depends(reject_api_keys)],
) -> User:
    """The user of the browser's session; a request with an API key gets 403
    ``session-required``."""
    return await _session_user(request, response, service, cookies)


# The signed-in user: the session, or on a GET an API key with the scope `read`.
CurrentUserDep = Annotated[User, Security(current_user)]
# The session, or an API key with the scope `backtests` (start, rename and star backtests).
BacktestsUserDep = Annotated[User, Security(current_user, scopes=[Scope.backtests.value])]
# The session, or an API key with the scope `models` (upload, manifest, rename, star, delete).
ModelsUserDep = Annotated[User, Security(current_user, scopes=[Scope.models.value])]
# The session or any valid API key: the route calls `Caller.require` with what the request needs.
CallerDep = Annotated[Caller, Depends(authenticate)]
# The browser's session only.
SessionUserDep = Annotated[User, Depends(session_user)]


async def sign_in_wait(request: Request, limiter: RateLimiterDep) -> int:
    """Counts a sign-in request of the client; the seconds it must wait, 0 within the limit."""
    wait = await limiter.hit(f"accounts:rl:{client_address(request)}", SIGN_IN_RATE_LIMIT)
    return max(1, math.ceil(wait)) if wait > 0 else 0


async def limit_sign_ins(wait: Annotated[int, Depends(sign_in_wait)]) -> None:
    """429 ``rate-limited`` beyond ``SIGN_IN_RATE_LIMIT`` sign-in requests a minute."""
    if wait:
        raise ProblemError(
            429,
            "rate-limited",
            "Too many requests",
            f"More than {SIGN_IN_RATE_LIMIT} sign-in requests in one minute from this address. "
            f"Retry in {wait} seconds.",
            headers={"Retry-After": str(wait)},
            extensions={"limit": SIGN_IN_RATE_LIMIT},
        )
