"""The OAuth 2.1 authorization server through which MCP clients (claude.ai, ChatGPT) sign in
as a user, as the MCP authorization spec asks: the authorization code with PKCE (S256 only),
refresh tokens that rotate, clients that register themselves (RFC 7591) or name themselves by
a client ID metadata document, and tokens bound to the MCP server (RFC 8707).

The flow: the client sends the browser to ``authorize``, which checks the whole request, keeps
it in Redis, ties it to that browser with a cookie and sends the browser to the consent page
(through the normal sign-in when it is not signed in). A request that fails a check is refused
right there and never sent back to the client: anyone can register a redirect URI, so going
back before the user acted would make the server an open redirector (RFC 9700, section
4.11.2). The page shows the request (``describe``) and sends the user's decision (``decide``),
both only from the browser that started it, so a consent link passed to someone else is
useless. The decision answers where the browser returns: the client's redirect URI with a code,
or ``error=access_denied``. The client trades the code and its PKCE verifier for an access token
(an hour) and a refresh token (30 days) at ``token``.

Tokens are kept like API keys: only their SHA-256, with their scopes and expiry, revocable, the
connected app's last use written at most once a minute. A connected app is the user and the
client; each authorization of it is a family of tokens of its own, so one client ID shared by
many installs (a metadata document) connects each of them. A refresh token works once: the
answer holds a new one, and bringing a used one again ends every token of that authorization,
as a stolen copy is in play; within ``REFRESH_GRACE`` of its use it is only refused, as that is
a retry or a second session of the same client. Every use checks the beta list, so taking
someone off it stops their apps at once.
"""

import base64
import hashlib
import logging
import re
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import unquote, urlencode, urlsplit, urlunsplit

from sqlalchemy.orm.attributes import set_committed_value

from candlestack.accounts.client_metadata import (
    ClientDocumentFailed,
    ClientDocuments,
    is_document_url,
    parse_metadata,
    redirect_uri_matches,
)
from candlestack.accounts.errors import (
    AuthorizeRefused,
    NoSuchAuthorizationRequest,
    NoSuchConnectedApp,
    NotAdmitted,
    OAuthError,
    ScopeNotRequested,
)
from candlestack.accounts.models import (
    AppToken,
    ConnectedApp,
    RegisteredClient,
    User,
    new_client_id,
    new_connected_app_id,
)
from candlestack.accounts.oauth_pending import (
    REQUEST_TTL,
    OAuthPending,
    PendingAuthorization,
    PendingCode,
)
from candlestack.accounts.oauth_store import OAuthStore
from candlestack.accounts.schemas import (
    SCOPE_ORDER,
    SCOPE_WORDS,
    AuthorizationRequestOut,
    ClientMetadataIn,
    ClientOut,
    ClientRegistrationOut,
    ConnectedAppOut,
    ConnectedAppsOut,
    ConsentIn,
    ConsentOut,
    Decision,
    Scope,
    ScopeOut,
    TokenEndpointAuthMethod,
    TokenOut,
)
from candlestack.accounts.service import admitted
from candlestack.core import DATABASE_ERRORS, Settings

logger = logging.getLogger(__name__)

# Where the issuer and the MCP server live under PUBLIC_URL.
ISSUER_PATH = "/api/v1/oauth"
RESOURCE_PATH = "/mcp"
CONSENT_PAGE = "/oauth/consent"
SIGN_IN_PAGE = "/signin"
ACCESS_PREFIX = "csa_"
REFRESH_PREFIX = "csr_"
ACCESS_LIFETIME = timedelta(hours=1)
REFRESH_LIFETIME = timedelta(days=30)
CODE_LIFETIME = timedelta(minutes=5)
# A used refresh token that comes again this soon is refused without ending its family: a
# client retrying after a lost answer, or two of its sessions sharing one token.
REFRESH_GRACE = timedelta(seconds=60)
# A fetched client ID metadata document is used this long before it is fetched again.
DOCUMENT_TTL = timedelta(hours=1)
# A connected app in use writes its last use at most this often.
LAST_USED_REFRESH = timedelta(minutes=1)
_ACCESS = "access"
_REFRESH = "refresh"
# RFC 7636: a verifier is 43 to 128 unreserved characters, an S256 challenge 43 of base64url.
_VERIFIER = re.compile(r"[A-Za-z0-9\-._~]{43,128}")
_CHALLENGE = re.compile(r"[A-Za-z0-9\-_]{43}")
_STATE_MAX = 1024
# The value of the cookie that ties authorization requests to a browser.
_BROWSER = re.compile(r"[A-Za-z0-9\-_]{43}")


def _now() -> datetime:
    """The time; patched by tests to move the clock."""
    return datetime.now(UTC)


def issuer(settings: Settings) -> str:
    """The authorization server's issuer identifier (RFC 8414)."""
    return settings.public_url + ISSUER_PATH


def protected_resource(settings: Settings) -> str:
    """The MCP server's address, the one resource tokens are for (RFC 8707)."""
    return settings.public_url + RESOURCE_PATH


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _browser_hash(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


def _s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _ordered(scopes: set[Scope] | list[Scope] | list[str]) -> list[Scope]:
    given = {Scope(scope) for scope in scopes}
    return [scope for scope in SCOPE_ORDER if scope in given]


def requested_scopes(scope: str | None) -> list[Scope]:
    """The scopes of an authorize request's `scope`; names the server does not know are
    ignored (``offline_access``, ``openid``), and without any it asks for all of them, so the
    user picks on the consent screen."""
    known = {item.value for item in Scope}
    asked = [item for item in (scope or "").split() if item in known]
    return _ordered(asked) if asked else list(SCOPE_ORDER)


def _granted(requested: list[Scope], chosen: list[Scope]) -> list[Scope]:
    """What the consent grants: the scopes chosen, read always.

    Raises:
        ScopeNotRequested: A scope the app did not ask for.
    """
    for scope in chosen:
        if scope not in requested and scope is not Scope.read:
            raise ScopeNotRequested(scope.value)
    return _ordered({Scope.read, *chosen})


def _narrowed(current: list[str], scope: str | None) -> list[Scope]:
    """The scopes of a refresh with `scope`: those it names, never more than `current`, read
    always; names the server does not know are ignored.

    Raises:
        OAuthError: ``invalid_scope`` for a scope the token does not have.
    """
    held = _ordered(current)
    if scope is None:
        return held
    known = {item.value for item in Scope}
    asked = {Scope(item) for item in scope.split() if item in known}
    if not asked.issubset(held):
        raise OAuthError("invalid_scope", "A refresh cannot add scopes the app was not granted.")
    return _ordered({Scope.read, *asked}) if asked else held


def _client_out(client: RegisteredClient, redirect_uri: str) -> ClientOut:
    host = urlsplit(redirect_uri).hostname or ""
    return ClientOut(
        name=client.name or host,
        logo_uri=client.logo_uri,
        client_uri=client.client_uri,
        redirect_host=host,
        document_host=urlsplit(client.client_id).hostname if client.metadata_document else None,
    )


def _with_query(uri: str, params: Mapping[str, str | None]) -> str:
    """`uri` with `params` added to its query (those that are None left out)."""
    parts = urlsplit(uri)
    added = urlencode({key: value for key, value in params.items() if value is not None})
    query = f"{parts.query}&{added}" if parts.query else added
    return urlunsplit(parts._replace(query=query))


def _basic_credentials(authorization: str | None) -> tuple[str, str] | None:
    """The client ID and secret of an ``Authorization: Basic`` header (RFC 6749, section
    2.3.1: each form-encoded); None without one.

    Raises:
        OAuthError: ``invalid_client`` for a Basic header that does not decode.
    """
    if authorization is None:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode()
    except ValueError:
        raise OAuthError(
            "invalid_client", "The Basic credentials do not decode.", status=401
        ) from None
    client_id, colon, secret = decoded.partition(":")
    if not colon:
        raise OAuthError("invalid_client", "The Basic credentials do not decode.", status=401)
    return unquote(client_id.replace("+", " ")), unquote(secret.replace("+", " "))


def _invalid_client() -> OAuthError:
    return OAuthError(
        "invalid_client",
        "The client is unknown, or did not authenticate the way it registered.",
        status=401,
    )


def _invalid_grant(description: str) -> OAuthError:
    return OAuthError("invalid_grant", description)


@dataclass(frozen=True, slots=True)
class AuthorizeParams:
    """The query parameters of an authorize request, as they came."""

    response_type: str | None
    client_id: str | None
    redirect_uri: str | None
    state: str | None
    code_challenge: str | None
    code_challenge_method: str | None
    scope: str | None
    resource: str | None


@dataclass(frozen=True, slots=True)
class Authorized:
    """Where an authorize request sends the browser, and the value of the cookie that ties the
    request to it."""

    location: str
    browser: str


@dataclass(frozen=True, slots=True)
class TokenUser:
    """The user an access token signs in as, its app and its scopes."""

    app: ConnectedApp
    user: User
    scopes: frozenset[Scope]


class AuthorizationServer:
    def __init__(
        self,
        settings: Settings,
        store: OAuthStore,
        pending: OAuthPending,
        documents: ClientDocuments,
    ) -> None:
        self._settings = settings
        self._store = store
        self._pending = pending
        self._documents = documents

    # --- registration ---------------------------------------------------------------------------

    async def register(self, body: object) -> ClientRegistrationOut:
        """Registers a client (RFC 7591); the answer holds its secret, which nothing keeps.

        Raises:
            OAuthError: ``invalid_client_metadata`` or ``invalid_redirect_uri``.
        """
        metadata = parse_metadata(body)
        method = metadata.token_endpoint_auth_method
        secret = None if method is TokenEndpointAuthMethod.none else secrets.token_urlsafe(32)
        now = _now()
        client = RegisteredClient(
            client_id=new_client_id(),
            metadata_document=False,
            created_at=now,
            fetched_at=None,
            secret_hash=None if secret is None else _hash(secret),
        )
        self._describe_client(client, metadata)
        self._store.add(client)
        await self._store.commit()
        return ClientRegistrationOut(
            client_id=client.client_id,
            client_secret=secret,
            client_id_issued_at=int(now.timestamp()),
            client_secret_expires_at=None if secret is None else 0,
            redirect_uris=metadata.redirect_uris,
            client_name=metadata.client_name,
            client_uri=metadata.client_uri,
            logo_uri=metadata.logo_uri,
            token_endpoint_auth_method=method,
            grant_types=metadata.grant_types,
            response_types=metadata.response_types,
        )

    @staticmethod
    def _describe_client(client: RegisteredClient, metadata: ClientMetadataIn) -> None:
        client.name = metadata.client_name or None
        client.client_uri = metadata.client_uri
        client.logo_uri = metadata.logo_uri
        client.redirect_uris = metadata.redirect_uris
        client.token_endpoint_auth_method = metadata.token_endpoint_auth_method.value

    # --- authorize ------------------------------------------------------------------------------

    async def _client_of(self, client_id: str | None) -> RegisteredClient:
        """The client an authorize request names.

        Raises:
            AuthorizeRefused: No client has the ID, or its metadata document cannot be used.
        """
        if not client_id:
            raise AuthorizeRefused("The request names no client (client_id).")
        if is_document_url(client_id):
            return await self._document_client(client_id)
        client = await self._store.client(client_id)
        if client is None or client.metadata_document:
            raise AuthorizeRefused("No app is registered with this client_id.")
        return client

    async def _document_client(self, url: str) -> RegisteredClient:
        """The client of the metadata document at `url`, fetched again once it is older than
        ``DOCUMENT_TTL``."""
        client = await self._store.client(url)
        now = _now()
        if client is not None and client.fetched_at and now - client.fetched_at < DOCUMENT_TTL:
            return client
        try:
            metadata = await self._documents.fetch(url)
        except ClientDocumentFailed as error:
            logger.warning("The client ID metadata document cannot be used: %s", error.reason)
            raise AuthorizeRefused(
                f"The app's client ID metadata document cannot be used: {error.reason}."
            ) from None
        if client is None:
            client = RegisteredClient(client_id=url, metadata_document=True, created_at=now)
            self._store.add(client)
        client.secret_hash = None
        client.fetched_at = now
        self._describe_client(client, metadata)
        await self._store.commit()
        return client

    async def authorize(
        self, params: AuthorizeParams, *, signed_in: bool, browser: str | None
    ) -> Authorized:
        """Where the browser goes from an authorize request: the consent page, through the
        sign-in page when it is not signed in. Nothing goes back to the client before the user
        acted on that page. The request is tied to the browser's cookie `browser`, a new one
        when it has none, so requests open in two of its tabs both stay valid.

        Raises:
            AuthorizeRefused: The client or its redirect URI is unknown, or the request is not
                valid (no S256 PKCE, another response type or resource, a state too long).
        """
        client = await self._client_of(params.client_id)
        redirect_uri = params.redirect_uri
        if not redirect_uri or not redirect_uri_matches(client.redirect_uris, redirect_uri):
            raise AuthorizeRefused("The redirect_uri is missing or not one the app registered.")
        if browser is None or not _BROWSER.fullmatch(browser):
            browser = secrets.token_urlsafe(32)
        try:
            pending = self._pending_request(client, redirect_uri, params, browser)
        except OAuthError as error:
            raise AuthorizeRefused(error.description, error=error.error) from None
        request_id = secrets.token_urlsafe(24)
        await self._pending.put_request(request_id, pending)
        consent = f"{CONSENT_PAGE}?{urlencode({'request': request_id})}"
        location = consent if signed_in else f"{SIGN_IN_PAGE}?{urlencode({'next': consent})}"
        return Authorized(location, browser)

    def _pending_request(
        self, client: RegisteredClient, redirect_uri: str, params: AuthorizeParams, browser: str
    ) -> PendingAuthorization:
        """The request to keep for the decision, tied to the browser with the cookie `browser`.

        Raises:
            OAuthError: What is wrong with it.
        """
        if params.response_type != "code":
            raise OAuthError("unsupported_response_type", "Only response_type=code is supported.")
        challenge = params.code_challenge
        if not challenge or not _CHALLENGE.fullmatch(challenge):
            raise OAuthError("invalid_request", "PKCE is required: send an S256 code_challenge.")
        if params.code_challenge_method != "S256":
            raise OAuthError("invalid_request", "Only code_challenge_method=S256 is supported.")
        if params.state is not None and len(params.state) > _STATE_MAX:
            raise OAuthError("invalid_request", f"state is longer than {_STATE_MAX} characters.")
        return PendingAuthorization(
            client_id=client.client_id,
            redirect_uri=redirect_uri,
            state=params.state,
            code_challenge=challenge,
            scopes=requested_scopes(params.scope),
            resource=self._resource(params.resource),
            expires_at=_now() + timedelta(seconds=REQUEST_TTL),
            browser=_browser_hash(browser),
        )

    def _resource(self, given: str | None) -> str:
        """The resource a request is for: the MCP server, also when it names none.

        Raises:
            OAuthError: ``invalid_target`` for any other resource.
        """
        expected = protected_resource(self._settings)
        if given is None or given.rstrip("/") == expected:
            return expected
        raise OAuthError("invalid_target", f"The only resource is the MCP server, {expected}.")

    def _back(self, redirect_uri: str, state: str | None, **params: str) -> str:
        """The client's redirect URI with `params`, the state and the issuer (RFC 9207)."""
        return _with_query(redirect_uri, {**params, "state": state, "iss": issuer(self._settings)})

    # --- consent --------------------------------------------------------------------------------

    async def _pending_of(
        self, request_id: str, browser: str | None
    ) -> tuple[PendingAuthorization, RegisteredClient]:
        """The request with the ID, when `browser` is the cookie of the browser it started in.

        Raises:
            NoSuchAuthorizationRequest: It was decided, expired or never was, or it started in
                another browser, or its app no longer registers its redirect URI.
        """
        pending = await self._pending.request(request_id)
        if (
            pending is None
            or browser is None
            or not secrets.compare_digest(pending.browser, _browser_hash(browser))
        ):
            raise NoSuchAuthorizationRequest
        client = await self._store.client(pending.client_id)
        # The browser goes back only to a redirect URI the app still registers: a metadata
        # document refreshed since the authorize step may have dropped it.
        if client is None or not redirect_uri_matches(client.redirect_uris, pending.redirect_uri):
            raise NoSuchAuthorizationRequest
        return pending, client

    async def describe(self, request_id: str, browser: str | None) -> AuthorizationRequestOut:
        """The request for the consent screen of the browser it started in (its cookie
        `browser`).

        Raises:
            NoSuchAuthorizationRequest: It was decided, expired or never was, or it started in
                another browser.
        """
        pending, client = await self._pending_of(request_id, browser)
        return AuthorizationRequestOut(
            client=_client_out(client, pending.redirect_uri),
            scopes=[
                ScopeOut(scope=scope, description=SCOPE_WORDS[scope]) for scope in pending.scopes
            ],
            expires_at=pending.expires_at,
        )

    async def decide(
        self, user: User, request_id: str, consent: ConsentIn, browser: str | None
    ) -> ConsentOut:
        """Decides the request for `user`, in the browser it started in (its cookie `browser`):
        where the browser returns to the client, with a code for the scopes granted, or with
        ``error=access_denied``. A request is decided once.

        Raises:
            NoSuchAuthorizationRequest: It was decided, expired or never was, or it started in
                another browser.
            NotAdmitted: The user is not on the beta list (when allowing).
            ScopeNotRequested: The consent grants a scope the app did not ask for.
        """
        pending, _client = await self._pending_of(request_id, browser)
        granted: list[Scope] = []
        if consent.decision is Decision.allow:
            if not admitted(self._settings, user.email):
                raise NotAdmitted
            granted = _granted(pending.scopes, consent.scopes)
        if await self._pending.take_request(request_id) is None:
            raise NoSuchAuthorizationRequest  # decided meanwhile
        if consent.decision is Decision.deny:
            return ConsentOut(
                redirect_to=self._back(
                    pending.redirect_uri,
                    pending.state,
                    error="access_denied",
                    error_description="The user did not allow access.",
                )
            )
        code = secrets.token_urlsafe(32)
        await self._pending.put_code(
            code,
            PendingCode(
                client_id=pending.client_id,
                redirect_uri=pending.redirect_uri,
                code_challenge=pending.code_challenge,
                scopes=granted,
                resource=pending.resource,
                user_id=user.id,
                expires_at=_now() + CODE_LIFETIME,
            ),
            int(CODE_LIFETIME.total_seconds()),
        )
        return ConsentOut(redirect_to=self._back(pending.redirect_uri, pending.state, code=code))

    # --- token ----------------------------------------------------------------------------------

    async def token(self, form: Mapping[str, str], authorization: str | None) -> TokenOut:
        """Answers a token request: trades a code or a refresh token for new tokens.

        Raises:
            OAuthError: What the client gets instead (RFC 6749, section 5.2).
        """
        client = await self._authenticate(form, authorization)
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            return await self._exchange(client, form)
        if grant_type == "refresh_token":
            return await self._refresh(client, form)
        raise OAuthError(
            "unsupported_grant_type", "grant_type must be authorization_code or refresh_token."
        )

    async def _authenticate(
        self, form: Mapping[str, str], authorization: str | None
    ) -> RegisteredClient:
        """The client of a token or revocation request, authenticated the way it registered.

        Raises:
            OAuthError: ``invalid_client``.
        """
        basic = _basic_credentials(authorization)
        if basic is not None:
            client_id, secret = basic
            method = TokenEndpointAuthMethod.client_secret_basic
            if form.get("client_id", client_id) != client_id:
                raise _invalid_client()
        else:
            client_id, secret = form.get("client_id"), form.get("client_secret")
            method = (
                TokenEndpointAuthMethod.client_secret_post
                if secret
                else TokenEndpointAuthMethod.none
            )
        client = None if not client_id else await self._store.client(client_id)
        if client is None or client.token_endpoint_auth_method != method.value:
            raise _invalid_client()
        if method is not TokenEndpointAuthMethod.none and not (
            client.secret_hash is not None
            and secret
            and secrets.compare_digest(client.secret_hash, _hash(secret))
        ):
            raise _invalid_client()
        return client

    async def _exchange(self, client: RegisteredClient, form: Mapping[str, str]) -> TokenOut:
        value = form.get("code")
        if not value:
            raise OAuthError("invalid_request", "code is missing.")
        ttl = int(CODE_LIFETIME.total_seconds())
        # The family is remembered as the code is taken, so a second exchange that comes at any
        # time after can end it.
        family = secrets.token_hex(16)
        code = await self._pending.take_code(value, family, ttl)
        if code is None:
            await self._end_reused_code(value, ttl)
            raise _invalid_grant("The code is unknown, used or expired.")
        self._check_code(client, code, form)
        user = await self._store.user(code.user_id)
        if user is None or not admitted(self._settings, user.email):
            raise _invalid_grant("The account is not on the beta list.")
        now = _now()
        app = await self._connect(user, client, code, now)
        tokens = self._issue(app, family, code.scopes, code.resource, now)
        await self._store.commit()
        if await self._pending.code_reused(value):
            # A second exchange came before these tokens were committed, so it found none.
            await self._store.delete_family(family)
            await self._store.commit()
            logger.warning("An authorization code was used twice: the tokens it gave are revoked")
            raise _invalid_grant("The code is unknown, used or expired.")
        return tokens

    def _check_code(
        self, client: RegisteredClient, code: PendingCode, form: Mapping[str, str]
    ) -> None:
        """Checks that the code is the client's, fresh, for the same redirect URI and resource,
        and that the verifier matches its challenge.

        Raises:
            OAuthError: ``invalid_grant``, or ``invalid_target`` for another resource.
        """
        if code.expires_at <= _now():
            raise _invalid_grant("The code is unknown, used or expired.")
        if code.client_id != client.client_id:
            raise _invalid_grant("The code was issued to another client.")
        if form.get("redirect_uri") != code.redirect_uri:
            raise _invalid_grant("redirect_uri is not the one of the authorization request.")
        verifier = form.get("code_verifier") or ""
        if not _VERIFIER.fullmatch(verifier) or not secrets.compare_digest(
            _s256(verifier), code.code_challenge
        ):
            raise _invalid_grant("The code_verifier does not match the code_challenge (PKCE).")
        if self._resource(form.get("resource")) != code.resource:
            raise OAuthError("invalid_target", "The resource is not the one of the code.")

    async def _end_reused_code(self, value: str, ttl: int) -> None:
        """Ends the tokens a code gave when it is brought again (RFC 6749, section 4.1.2)."""
        family = await self._pending.reuse_code(value, ttl)
        if family is not None:
            await self._store.delete_family(family)
            await self._store.commit()
            logger.warning("An authorization code was used twice: the tokens it gave are revoked")

    async def _connect(
        self, user: User, client: RegisteredClient, code: PendingCode, now: datetime
    ) -> ConnectedApp:
        """The user's connected app of the client, with the scopes of the new consent (its
        `code`) and of its earlier authorizations that still work, and the redirect URI the
        consent returned to. Those keep their tokens: a client ID can be shared by many installs
        of one app (a metadata document), and connecting one must not disconnect the others."""
        await self._store.lock_user(user.id)
        app = await self._store.app(user.id, client.client_id)
        granted = {scope.value for scope in code.scopes}
        if app is None:
            app = ConnectedApp(
                id=new_connected_app_id(),
                user_id=user.id,
                client_id=client.client_id,
                created_at=now,
                last_used_at=None,
            )
            self._store.add(app)
        else:
            granted |= await self._store.granted_scopes(app.id, now)
        app.scopes = [scope.value for scope in _ordered(list(granted))]
        app.redirect_uri = code.redirect_uri
        await self._store.flush()  # the tokens refer to it
        return app

    def _issue(
        self, app: ConnectedApp, family: str, scopes: list[Scope], resource: str, now: datetime
    ) -> TokenOut:
        """New access and refresh tokens of the app in `family`; the caller commits."""
        access = ACCESS_PREFIX + secrets.token_urlsafe(32)
        refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
        values = [scope.value for scope in scopes]
        for token, kind, lifetime in (
            (access, _ACCESS, ACCESS_LIFETIME),
            (refresh, _REFRESH, REFRESH_LIFETIME),
        ):
            self._store.add(
                AppToken(
                    token_hash=_hash(token),
                    kind=kind,
                    app_id=app.id,
                    family=family,
                    scopes=values,
                    resource=resource,
                    created_at=now,
                    expires_at=now + lifetime,
                    rotated_at=None,
                )
            )
        return TokenOut(
            access_token=access,
            expires_in=int(ACCESS_LIFETIME.total_seconds()),
            refresh_token=refresh,
            scope=" ".join(values),
        )

    async def _refresh(self, client: RegisteredClient, form: Mapping[str, str]) -> TokenOut:
        value = form.get("refresh_token")
        if not value:
            raise OAuthError("invalid_request", "refresh_token is missing.")
        found = await self._store.token(_hash(value), _REFRESH, lock=True)
        if found is None or found[1].client_id != client.client_id:
            raise _invalid_grant("The refresh token is unknown, expired or revoked.")
        token, app, user = found
        now = _now()
        if token.rotated_at is not None:
            if now - token.rotated_at < REFRESH_GRACE:
                # A retry, or another session of the client that holds the same token; logged,
                # as it could also be a stolen copy used in time.
                logger.warning(
                    "A used refresh token of the connected app %s came again within %d s: "
                    "refused, its family of tokens kept",
                    app.id,
                    REFRESH_GRACE.total_seconds(),
                )
                raise _invalid_grant("The refresh token was used already.")
            await self._store.delete_family(token.family)
            await self._store.commit()
            logger.warning("A used refresh token came again: its family of tokens is revoked")
            raise _invalid_grant("The refresh token was used already; sign in again.")
        if token.expires_at <= now or not admitted(self._settings, user.email):
            raise _invalid_grant("The refresh token is unknown, expired or revoked.")
        if self._resource(form.get("resource")) != token.resource:
            raise OAuthError("invalid_target", "The resource is not the one of the token.")
        scopes = _narrowed(token.scopes, form.get("scope"))
        token.rotated_at = now
        await self._store.delete_expired_tokens(app.id, now)
        tokens = self._issue(app, token.family, scopes, token.resource, now)
        await self._store.commit()
        return tokens

    # --- revocation -----------------------------------------------------------------------------

    async def revoke(self, form: Mapping[str, str], authorization: str | None) -> None:
        """Revokes the client's token (RFC 7009): a refresh token with every token of its
        authorization, an access token alone. A token that is unknown or another client's is
        left as it is, and the answer is the same.

        Raises:
            OAuthError: ``invalid_client``, or ``invalid_request`` without a token.
        """
        client = await self._authenticate(form, authorization)
        value = form.get("token")
        if not value:
            raise OAuthError("invalid_request", "token is missing.")
        kind = _REFRESH if value.startswith(REFRESH_PREFIX) else _ACCESS
        found = await self._store.token(_hash(value), kind)
        if found is None or found[1].client_id != client.client_id:
            return
        token = found[0]
        if kind == _REFRESH:
            await self._store.delete_family(token.family)
        else:
            await self._store.delete_token(token.token_hash)
        await self._store.commit()

    # --- Settings -------------------------------------------------------------------------------

    async def connected_apps(self, user: User) -> ConnectedAppsOut:
        return ConnectedAppsOut(
            items=[
                ConnectedAppOut(
                    id=app.id,
                    # The host the user saw on the consent page and the browser returned to,
                    # not just any URI the app registered.
                    client=_client_out(client, app.redirect_uri or client.redirect_uris[0]),
                    scopes=_ordered(app.scopes),
                    connected_at=app.created_at,
                    last_used_at=app.last_used_at,
                )
                for app, client in await self._store.apps(user.id)
            ]
        )

    async def disconnect(self, user: User, app_id: str) -> None:
        """Disconnects the user's app: its tokens stop working at once.

        Raises:
            NoSuchConnectedApp: The user has no connected app with the ID.
        """
        app = await self._store.user_app(user.id, app_id)
        if app is None:
            raise NoSuchConnectedApp
        await self._store.delete_app(app.id)
        await self._store.commit()


class AccessTokens:
    """What signs requests to the MCP server in with an access token."""

    def __init__(self, settings: Settings, store: OAuthStore) -> None:
        self._settings = settings
        self._store = store

    async def sign_in(self, token: str, resource: str) -> TokenUser | None:
        """The user the access token `token` signs in as at `resource`: a token whose hash is
        known, unexpired, for that resource, of a user on the beta list. None otherwise,
        whatever the reason."""
        if not token.startswith(ACCESS_PREFIX):
            return None
        found = await self._store.token(_hash(token), _ACCESS)
        if found is None:
            return None
        row, app, user = found
        if row.expires_at <= _now() or row.resource != resource:
            return None
        if not admitted(self._settings, user.email):
            return None
        return TokenUser(app, user, frozenset(Scope(scope) for scope in row.scopes))

    async def mark_used(self, app: ConnectedApp) -> None:
        """Records that the app signed a request in, at most once a ``LAST_USED_REFRESH``; best
        effort, like an API key's last use."""
        now = _now()
        if app.last_used_at is not None and now - app.last_used_at < LAST_USED_REFRESH:
            return
        try:
            await self._store.touch_app(app.id, now)
        except DATABASE_ERRORS:
            logger.warning(
                "Could not record the last use of connected app %s", app.id, exc_info=True
            )
            return
        set_committed_value(app, "last_used_at", now)
