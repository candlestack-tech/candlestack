"""HTTP API of the accounts module: ``/api/v1/auth/...`` and ``/api/v1/me``.

Sign-in starts at ``GET /api/v1/auth/{provider}/start?next=<path>``, which redirects to GitHub
or Google; the provider returns to ``/api/v1/auth/{provider}/callback``, which sets the session
cookie and redirects to ``next`` (default ``/dashboard``), or to ``/signin?error=denied`` (not on
the beta list: ``GET /api/v1/auth/denied`` names the account) or ``/signin?error=failed``, both
with ``next`` too when it is not the default, so the sign-in page goes on to it. A
provider's account that never signed in joins the user with its email, so the email links a
user's GitHub and Google.
With ``intent=connect`` the same round trip, started from a link of Settings, adds a sign-in
method to the signed-in user and returns to Settings (``/settings/sign-in``, with
``?error=taken`` or ``?error=failed``).

The user's API keys are ``/api/v1/me/api-keys``, managed with the browser's session only: a
key cannot create, list or revoke keys.
"""

import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import RedirectResponse

from candlestack.accounts.cookies import DENIED, SESSION, STATE, Cookies, CookiesDep
from candlestack.accounts.dependencies import (
    AccountsServiceDep,
    ApiKeysDep,
    BacktestsUserDep,
    CurrentUserDep,
    SessionUserDep,
    browser_session,
    limit_sign_ins,
    reject_api_keys,
    sign_in_wait,
)
from candlestack.accounts.errors import (
    LastSignInMethod,
    NoSuchApiKey,
    NoSuchSignInMethod,
    SignInDenied,
    SignInFailed,
    TooManyApiKeys,
)
from candlestack.accounts.pending import TTL
from candlestack.accounts.schemas import (
    API_KEYS_MAX,
    ApiKeyCreated,
    ApiKeyIn,
    ApiKeysOut,
    Appearance,
    AppearanceUpdate,
    BacktestDefaults,
    BacktestDefaultsIn,
    DeniedSignInOut,
    MeOut,
    MeUpdate,
    PreferencesOut,
    Provider,
    SignInIntent,
)
from candlestack.accounts.service import (
    CONNECT_NEXT,
    DEFAULT_NEXT,
    SESSION_MAX_AGE,
    AccountsService,
    SignInStart,
    is_connect_state,
    safe_next,
)
from candlestack.core import (
    DATABASE_ERRORS,
    NO_STORE,
    ProblemError,
    Settings,
    SettingsDep,
    is_same_origin,
    problem_doc,
    require_same_origin,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["accounts"])

SIGN_IN_PAGE = "/signin"
_PROVIDER_NAMES = {Provider.github: "GitHub", Provider.google: "Google"}

IntentParam = Annotated[
    SignInIntent,
    Query(description="`sign-in` (default), or `connect` to add a sign-in method in Settings"),
]

NextPath = Annotated[
    str | None,
    Query(
        description="Path of the app to open after signing in (default `/dashboard`)",
        examples=["/dashboard"],
    ),
]


_NOT_SIGNED_IN = problem_doc(401, "Not signed in (`not-signed-in`).")
_CROSS_ORIGIN = problem_doc(
    403, "The request does not come from the app's own pages (`cross-origin-request`)."
)

ApiKeyId = Annotated[str, Path(description="Public ID of the API key", examples=["key_7f3k2m4qx4"])]


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=302)


def _error_page(page: str, code: str, next_path: str | None = None) -> str:
    """`page` (the sign-in page or Settings) with the error's `code`. The sign-in page also
    gets `next_path`, the page the sign-in was going to (an app's consent page, say), when it
    is a page of the app other than the default, so signing in again goes on to it."""
    query = {"error": code}
    if page == SIGN_IN_PAGE and (path := safe_next(next_path)) != DEFAULT_NEXT:
        query["next"] = path
    return f"{page}?{urlencode(query, safe='/')}"


def _failed(provider: Provider, error: SignInFailed, page: str) -> RedirectResponse:
    """Back to `page` (the sign-in page or Settings) with the error's code, and on the sign-in
    page the page the sign-in was going to."""
    logger.warning("Sign-in with %s failed: %s", provider, error.reason)
    return _redirect(_error_page(page, error.code, error.next_path))


def _database_failed(provider: Provider, error: Exception, page: str) -> RedirectResponse:
    """Back to `page` with ``error=failed`` when Postgres failed: it is down, or another first
    sign-in or connect of the same account was just saved. The browser is on its way to or back
    from the provider, so it gets a page, not a problem. The error reaches Sentry by its type
    only: its message and the variables of its frames may hold the account's email."""
    logger.error("Sign-in with %s failed: Postgres failed (%s)", provider, type(error).__name__)
    return _redirect(_error_page(page, SignInFailed.code))


def _to_provider(start: SignInStart, cookies: Cookies) -> RedirectResponse:
    response = _redirect(start.url)
    cookies.set(response, STATE, start.state, TTL)
    return response


@router.get(
    "/api/v1/auth/{provider}/start",
    status_code=302,
    response_class=RedirectResponse,
)
async def start_sign_in(  # noqa: PLR0913 (the query parameters and the dependencies)
    provider: Provider,
    *,
    request: Request,
    service: AccountsServiceDep,
    cookies: CookiesDep,
    settings: SettingsDep,
    wait: Annotated[int, Depends(sign_in_wait)],
    next: NextPath = None,  # noqa: A002 (the parameter's name in the URL)
    intent: IntentParam = SignInIntent.sign_in,
) -> RedirectResponse:
    """Redirects to the provider's sign-in page, or to `/signin?error=failed` (with `next`
    when it is not the default) when sign-in with it is not available or this address made
    too many sign-in requests in a minute. A
    provider's account that never signed in joins the CandleStack user with its email, so the
    email links a user's GitHub and Google; it signs in to a new user when no user has it.

    With `intent=connect` (signed in, a link of Settings) the provider's account is added to
    the signed-in user's sign-in methods instead: the callback returns to `next` (default
    `/settings/sign-in`), or to `/settings/sign-in?error=taken` when that account already
    signs in to another CandleStack user, or `/settings/sign-in?error=failed`, also right away
    when the request does not come from the app's own pages."""
    page = CONNECT_NEXT if intent is SignInIntent.connect else SIGN_IN_PAGE
    try:
        if wait:
            raise SignInFailed("too many sign-in requests from this address")
        if intent is SignInIntent.connect:
            return await _start_connect(
                provider, request, service, cookies, settings, next_path=next
            )
        start = await service.start_sign_in(provider, next)
    except SignInFailed as error:
        error.next_path = next
        return _failed(provider, error, page)
    except DATABASE_ERRORS as error:  # a connect looks up the session
        return _database_failed(provider, error, page)
    return _to_provider(start, cookies)


async def _start_connect(
    provider: Provider,
    request: Request,
    service: AccountsService,
    cookies: Cookies,
    settings: Settings,
    *,
    next_path: str | None,
) -> RedirectResponse:
    """Starts connecting a sign-in method; a browser that is not signed in signs in first.

    Raises:
        SignInFailed: The request does not come from the app's own pages, the provider is
            not configured, or Redis failed.
    """
    # A link of another site must not connect the provider's account the browser is signed in
    # to there: SameSite=Lax sends the session cookie along on a navigation.
    if not is_same_origin(request, settings):
        raise SignInFailed("the connect does not come from the app's own pages")
    session = await browser_session(request, service, cookies)
    if session is None:
        query = urlencode({"next": safe_next(next_path, CONNECT_NEXT)}, safe="/")
        return _redirect(f"{SIGN_IN_PAGE}?{query}")
    response = _to_provider(
        await service.start_connect(provider, session.token, next_path), cookies
    )
    session.slide_on(response)
    return response


@router.get("/api/v1/auth/{provider}/callback", include_in_schema=False)
async def finish_sign_in(  # noqa: PLR0913 (the provider's query parameters)
    provider: Provider,
    *,
    request: Request,
    service: AccountsServiceDep,
    cookies: CookiesDep,
    wait: Annotated[int, Depends(sign_in_wait)],
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    # A failed connect returns to Settings, a failed sign-in to the sign-in page.
    page = CONNECT_NEXT if is_connect_state(state) else SIGN_IN_PAGE
    try:
        if wait:
            raise SignInFailed("too many sign-in requests from this address")
        done = await service.finish_sign_in(
            provider,
            state=state,
            browser_state=cookies.read(request, STATE),
            session_token=cookies.read(request, SESSION),
            code=code,
            error=error,
        )
    except SignInDenied as denied:
        response = _redirect(_error_page(SIGN_IN_PAGE, "denied", denied.next_path))
        cookies.set(response, DENIED, denied.denial_id, TTL)
    except SignInFailed as failed:
        response = _failed(provider, failed, page)
    except DATABASE_ERRORS as failed:
        response = _database_failed(provider, failed, page)
    else:
        response = _redirect(done.next_path)
        if done.session_token is not None:
            cookies.set(response, SESSION, done.session_token, SESSION_MAX_AGE)
    cookies.delete(response, STATE)
    return response


@router.post(
    "/api/v1/auth/sign-out",
    status_code=204,
    responses=problem_doc(
        403,
        "The request does not come from the app's own pages (`cross-origin-request`), or it "
        "came with an API key (`session-required`).",
    ),
    dependencies=[Depends(require_same_origin), Depends(reject_api_keys)],
)
async def sign_out(request: Request, service: AccountsServiceDep, cookies: CookiesDep) -> Response:
    """Ends this browser's session; 204 also when it had none."""
    token = cookies.read(request, SESSION)
    if token is not None:
        await service.sign_out(token)
    response = Response(status_code=204)
    cookies.delete(response, SESSION)
    return response


@router.get(
    "/api/v1/auth/denied",
    responses=problem_doc(404, "This browser was not turned away lately (`not-found`)."),
)
async def denied_sign_in(
    request: Request, response: Response, service: AccountsServiceDep, cookies: CookiesDep
) -> DeniedSignInOut:
    """The account the beta list just turned away, for the sign-in page; 404 when this browser
    was not turned away in the last 10 minutes."""
    denial_id = cookies.read(request, DENIED)
    denied = await service.denial(denial_id) if denial_id else None
    if denied is None:
        raise ProblemError(
            404,
            "not-found",
            "Not Found",
            "This browser was not turned away by the beta list in the last 10 minutes.",
        )
    response.headers.update(NO_STORE)
    return denied


@router.get(
    "/api/v1/me",
    responses=problem_doc(401, "No session came with the request, or it ended (`not-signed-in`)."),
)
async def me(user: CurrentUserDep, service: AccountsServiceDep, response: Response) -> MeOut:
    """The signed-in user."""
    response.headers.update(NO_STORE)
    return await service.me(user)


@router.patch(
    "/api/v1/me",
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN},
    dependencies=[Depends(require_same_origin)],
)
async def update_me(
    body: MeUpdate, user: CurrentUserDep, service: AccountsServiceDep, response: Response
) -> MeOut:
    """Changes the display name."""
    response.headers.update(NO_STORE)
    return await service.rename(user, body.display_name)


@router.delete(
    "/api/v1/me",
    status_code=204,
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN},
    dependencies=[Depends(require_same_origin)],
)
async def delete_me(
    user: SessionUserDep, service: AccountsServiceDep, cookies: CookiesDep
) -> Response:
    """Deletes the account with everything it owns and ends all its sessions."""
    await service.delete_account(user)
    response = Response(status_code=204)
    cookies.delete(response, SESSION)
    return response


@router.delete(
    "/api/v1/me/sign-in-methods/{provider}",
    status_code=204,
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **problem_doc(404, "The account does not sign in with this provider (`not-found`)."),
        **problem_doc(409, "It is the account's last sign-in method (`last-sign-in-method`)."),
    },
    dependencies=[Depends(require_same_origin)],
)
async def disconnect(provider: Provider, user: SessionUserDep, service: AccountsServiceDep) -> None:
    """Removes a sign-in method; the last one cannot be removed. Signing in later with an
    account of that provider whose email is the user's adds it again."""
    name = _PROVIDER_NAMES[provider]
    try:
        await service.disconnect(user, provider)
    except NoSuchSignInMethod:
        raise ProblemError(
            404, "not-found", "Not Found", f"Your account does not sign in with {name}."
        ) from None
    except LastSignInMethod:
        raise ProblemError(
            409,
            "last-sign-in-method",
            "Last sign-in method",
            f"{name} is the only way to sign in to your account, so it stays. Connect another "
            "sign-in method first.",
        ) from None


@router.post(
    "/api/v1/auth/sign-out-everywhere",
    status_code=204,
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN},
    dependencies=[Depends(require_same_origin)],
)
async def sign_out_everywhere(
    user: SessionUserDep, service: AccountsServiceDep, cookies: CookiesDep
) -> Response:
    """Ends every session of the user, this one included."""
    await service.sign_out_everywhere(user)
    response = Response(status_code=204)
    cookies.delete(response, SESSION)
    return response


@router.get("/api/v1/me/preferences", responses=_NOT_SIGNED_IN)
async def preferences(
    user: CurrentUserDep, service: AccountsServiceDep, response: Response
) -> PreferencesOut:
    """The user's settings, defaults included."""
    response.headers.update(NO_STORE)
    return await service.preferences(user)


@router.put(
    "/api/v1/me/preferences/backtest-defaults",
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN},
    dependencies=[Depends(require_same_origin)],
)
async def save_backtest_defaults(
    body: BacktestDefaultsIn,
    user: BacktestsUserDep,
    service: AccountsServiceDep,
    response: Response,
) -> BacktestDefaults:
    """Saves how new backtests start (Save defaults): every setting, as a whole. An API key
    needs the scope `backtests`."""
    response.headers.update(NO_STORE)
    return await service.save_backtest_defaults(user, body)


@router.patch(
    "/api/v1/me/preferences/appearance",
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN},
    dependencies=[Depends(require_same_origin)],
)
async def update_appearance(
    body: AppearanceUpdate, user: CurrentUserDep, service: AccountsServiceDep, response: Response
) -> Appearance:
    """Changes some appearance settings; each control saves as soon as it changes."""
    response.headers.update(NO_STORE)
    return await service.update_appearance(user, body)


@router.get("/api/v1/me/api-keys", responses=_NOT_SIGNED_IN)
async def list_api_keys(user: SessionUserDep, keys: ApiKeysDep, response: Response) -> ApiKeysOut:
    """The user's API keys that are not revoked, oldest first, without their secrets; an
    expired key stays listed until it is revoked."""
    response.headers.update(NO_STORE)
    return await keys.list_keys(user)


@router.post(
    "/api/v1/me/api-keys",
    status_code=201,
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **problem_doc(
            409,
            f"The user has {API_KEYS_MAX} keys that are neither revoked nor expired "
            "(`too-many-api-keys`; `limit`).",
        ),
    },
    dependencies=[Depends(require_same_origin)],
)
async def create_api_key(
    body: ApiKeyIn, user: SessionUserDep, keys: ApiKeysDep, response: Response
) -> ApiKeyCreated:
    """Creates an API key for scripts and agents: `Authorization: Bearer <secret>` signs a
    request in as the user, for what its scopes allow. The secret is in this response only;
    CandleStack keeps just its SHA-256."""
    response.headers.update(NO_STORE)
    try:
        return await keys.create(user, body)
    except TooManyApiKeys:
        raise ProblemError(
            409,
            "too-many-api-keys",
            "Too many API keys",
            f"You have {API_KEYS_MAX} API keys, the most you can have. Revoke one you no longer "
            "use, then create the new one.",
            extensions={"limit": API_KEYS_MAX},
        ) from None


@router.delete(
    "/api/v1/me/api-keys/{key_id}",
    status_code=204,
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **problem_doc(
            404, "The user has no API key with this ID that is not revoked (`not-found`)."
        ),
    },
    dependencies=[Depends(require_same_origin)],
)
async def revoke_api_key(key_id: ApiKeyId, user: SessionUserDep, keys: ApiKeysDep) -> None:
    """Revokes the API key: it stops working at once and leaves the list."""
    try:
        await keys.revoke(user, key_id)
    except NoSuchApiKey:
        raise ProblemError(
            404, "not-found", "Not Found", "You have no API key with this ID that is not revoked."
        ) from None


# Registered only where ``dev_sign_in_allowed``; hidden from the API reference.
dev_router = APIRouter(include_in_schema=False)


@dev_router.post(
    "/api/v1/auth/dev",
    status_code=204,
    dependencies=[Depends(require_same_origin), Depends(limit_sign_ins)],
)
async def dev_sign_in(service: AccountsServiceDep, cookies: CookiesDep) -> Response:
    """Signs this browser in as the dev user, so the app can be tried without OAuth keys."""
    response = Response(status_code=204)
    cookies.set(response, SESSION, await service.dev_sign_in(), SESSION_MAX_AGE)
    return response
