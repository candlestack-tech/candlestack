"""Accounts: the OAuth round trip, the beta list, sessions and what a user changes in
Settings (name, sign-in methods, preferences, deleting the account).

A sign-in starts with a random ``state`` kept in Redis (with the PKCE verifier and where to go
next) and in a cookie of the browser; the callback must bring back both, so nobody can finish a
sign-in that another browser started (login CSRF). The account the provider vouches for signs
in only when its email is on the beta list. A provider account signs in to the user it was
added to; one that never signed in joins the user with its email, so the email links a user's
GitHub and Google (and a sign-in method the user removed comes back when they sign in with it
again), else it makes a new user. A session is a random token in the browser's cookie; Postgres
keeps only its SHA-256.

Connecting a sign-in method in Settings is the same round trip, started by a signed-in user:
the pending state remembers the session (its hash), and the callback adds the provider's
account to its user instead of signing in, only while the browser is still signed in with that
session. So a session that was signed out (everywhere, too) cannot finish a connect it started.
Its state starts with ``connect.``, so the callback knows where to send the browser back even
when the round trip failed before its pending state was found.
"""

import hashlib
import logging
import re
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.accounts.errors import (
    LastSignInMethod,
    NoSuchSignInMethod,
    SignInDenied,
    SignInFailed,
    SignInMethodTaken,
)
from candlestack.accounts.models import (
    Identity,
    User,
    UserSession,
    new_user_id,
)
from candlestack.accounts.oauth import OAuthClient, ProviderAccount
from candlestack.accounts.pending import PendingSignIn, PendingStore
from candlestack.accounts.schemas import (
    DISPLAY_NAME_MAX,
    Appearance,
    AppearanceUpdate,
    BacktestDefaults,
    BacktestDefaultsIn,
    DeniedSignInOut,
    MeOut,
    PreferencesOut,
    Provider,
    SignInMethodOut,
)
from candlestack.accounts.store import AccountStore
from candlestack.core import Settings

logger = logging.getLogger(__name__)

DEFAULT_NEXT = "/dashboard"
# Where connecting a sign-in method returns to by default: its section of Settings.
CONNECT_NEXT = "/settings/sign-in"
_CONNECT_STATE_PREFIX = "connect."
SESSION_LIFETIME = timedelta(days=30)
SESSION_MAX_AGE = int(SESSION_LIFETIME.total_seconds())
# A session in use is extended at most this often, so a busy user costs one write a day.
SESSION_REFRESH = timedelta(days=1)
# The fixed account of the dev sign-in (local runs and tests only).
DEV_EMAIL = "dev@candlestack.tech"
DEV_NAME = "Dev user"
# Environments with the dev sign-in: never stage or prod, whatever their settings say.
_DEV_SIGN_IN_ENVS = frozenset({"local", "test"})
# Longer values than the app ever sends are not looked up.
_MAX_STATE = 64
_MAX_NEXT = 2048
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _now() -> datetime:
    """The time; patched by tests to move the clock."""
    return datetime.now(UTC)


def dev_sign_in_allowed(app_env: str) -> bool:
    return app_env in _DEV_SIGN_IN_ENVS


def admitted(settings: Settings, email: str) -> bool:
    """Whether the user with `email` may use the app: on the beta list, or the dev user where
    the dev sign-in is allowed."""
    return email in settings.beta_allowlist or (
        email == DEV_EMAIL and dev_sign_in_allowed(settings.app_env)
    )


def safe_next(path: str | None, default: str = DEFAULT_NEXT) -> str:
    """`path` when it is a page of the app, else `default`: never another site (``//host``, a
    backslash, ``https://...``) and never the API."""
    if (
        not path
        or len(path) > _MAX_NEXT
        or not path.startswith("/")
        or path.startswith(("//", "/api/", "/api?", "/api#"))
        or path == "/api"
        or "\\" in path
        or _CONTROL.search(path)
    ):
        return default
    return path


def is_connect_state(state: str | None) -> bool:
    """Whether the callback's `state` is that of connecting a sign-in method; it may be forged,
    so it only picks the page a failure returns to."""
    return state is not None and state.startswith(_CONNECT_STATE_PREFIX)


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


@dataclass(frozen=True, slots=True)
class SignInStart:
    """Where to send the browser, and the state to put in its cookie."""

    url: str
    state: str


@dataclass(frozen=True, slots=True)
class SignInDone:
    """The page to open, and the token of the session to put in the browser's cookie: a new
    session's, or that of the session that connected a sign-in method when it was extended
    (else none: the browser stays in its session)."""

    next_path: str
    session_token: str | None


@dataclass(frozen=True, slots=True)
class SignedIn:
    """The user of a session; ``extended`` when the cookie must be sent again to slide on."""

    user: User
    extended: bool


# What an account deletion hook does once the deletion is committed.
AfterDeletion = Callable[[], Awaitable[None]]


class AccountDeletionHook(Protocol):
    """Deletes what another module keeps for a user whose account is being deleted, e.g. their
    models or backtests, in two phases.

    The call runs in the deletion's transaction (`db`), before the user's row goes: it deletes
    the module's rows, and when it raises, the whole deletion rolls back. It must not touch
    storage outside Postgres (files on disk) yet, as a later hook or the commit may still fail;
    it returns what deletes that instead (None when there is nothing to delete), which runs
    only once the deletion is committed. Then the account is gone, so an error there is
    logged (by its type only) and the rest still run: it leaves an orphaned file at worst.

    Hooks are wired in ``candlestack.main``, so this module never imports the modules that own
    the data."""

    async def __call__(self, user_id: str, db: AsyncSession, /) -> AfterDeletion | None: ...


class AccountsService:
    def __init__(
        self,
        settings: Settings,
        store: AccountStore,
        pending: PendingStore,
        oauth: OAuthClient,
        *,
        deletion_hooks: Sequence[AccountDeletionHook] = (),
    ) -> None:
        self._settings = settings
        self._store = store
        self._pending = pending
        self._oauth = oauth
        self._deletion_hooks = deletion_hooks

    async def start_sign_in(self, provider: Provider, next_path: str | None) -> SignInStart:
        """Where to send the browser to sign in with `provider`, and the state for its cookie.

        Raises:
            SignInFailed: The provider is not configured, or Redis failed.
        """
        return await self._start(
            provider, secrets.token_urlsafe(32), next_path=safe_next(next_path)
        )

    async def start_connect(
        self, provider: Provider, session_token: str, next_path: str | None
    ) -> SignInStart:
        """Like `start_sign_in`, but the callback adds the provider's account to the sign-in
        methods of the user of the session with `session_token`, if the browser is still signed
        in with it then, and returns to `next_path` (default ``/settings/sign-in``).

        Raises:
            SignInFailed: The provider is not configured, or Redis failed.
        """
        return await self._start(
            provider,
            _CONNECT_STATE_PREFIX + secrets.token_urlsafe(32),
            next_path=safe_next(next_path, CONNECT_NEXT),
            connect_session=_hash(session_token).hex(),
        )

    async def _start(
        self,
        provider: Provider,
        state: str,
        *,
        next_path: str,
        connect_session: str | None = None,
    ) -> SignInStart:
        verifier = secrets.token_urlsafe(48)
        url = self._oauth.authorization_url(provider, state=state, verifier=verifier)
        await self._pending.put_sign_in(
            state,
            PendingSignIn(
                provider=provider,
                verifier=verifier,
                next=next_path,
                connect_session=connect_session,
            ),
        )
        return SignInStart(url=url, state=state)

    async def finish_sign_in(
        self,
        provider: Provider,
        *,
        state: str | None,
        browser_state: str | None,
        session_token: str | None,
        code: str | None,
        error: str | None,
    ) -> SignInDone:
        """Signs in the account the provider returned with, or adds it to the user who
        started connecting it.

        `state`, `code` and `error` are the callback's query parameters, `browser_state` the
        state cookie of the browser and `session_token` its session cookie.

        Raises:
            SignInFailed: The sign-in or connect was cancelled or failed, or the session that
                started the connect ended or is not this browser's any more.
            SignInMethodTaken: The account to connect signs in to another user.
            SignInDenied: The account's email is not on the beta list (sign-in only).
        """
        if not state or not browser_state or len(state) > _MAX_STATE:
            raise SignInFailed("the state is missing or too long")
        if not secrets.compare_digest(state.encode(), browser_state.encode()):
            raise SignInFailed("the state does not match this browser's")
        pending = await self._pending.take_sign_in(state)
        if pending is None or pending.provider is not provider:
            raise SignInFailed("the state is unknown or expired")
        if error is not None or not code:
            raise SignInFailed(f"{provider} returned no code (cancelled or refused)")
        if pending.connect_session is not None:
            signed_in = await self._connecting(pending.connect_session, session_token)
            await self._connect(
                signed_in.user,
                await self._oauth.fetch_account(provider, code=code, verifier=pending.verifier),
            )
            return SignInDone(
                next_path=pending.next, session_token=session_token if signed_in.extended else None
            )
        account = await self._oauth.fetch_account(provider, code=code, verifier=pending.verifier)
        identity = await self._store.identity(account.provider, account.subject)
        known = None if identity is None else await self._store.user(identity.user_id)
        # The beta list admits users: an account that signed in before is checked by its user's
        # email (that of the first sign-in), so a method connected in Settings signs in too.
        if (known.email if known else account.email) not in self._settings.beta_allowlist:
            raise SignInDenied(await self._pending.put_denial(account.email, provider))
        user = await self._account_of(account, identity, known)
        return SignInDone(next_path=pending.next, session_token=await self._new_session(user))

    async def _account_of(
        self, account: ProviderAccount, identity: Identity | None, known: User | None
    ) -> User:
        """The user `account` signs in as: `known`, the one its `identity` signed in as before,
        else the one with its email, else a new one.

        Raises:
            SignInFailed: The user with its email signs in with another account of the
                provider (connected in Settings).
        """
        now = _now()
        if identity is not None:
            identity.email = account.email
            identity.login = account.login
            user = known
            assert user is not None  # the foreign key
        else:
            user = await self._store.user_by_email(account.email)
            if user is None:
                user = self._new_user(account.email, provider_display_name(account), now)
            else:
                await self._store.lock_user(user.id)
                if account.provider in await self._store.providers(user.id):
                    raise SignInFailed(f"the user signs in with another {account.provider} account")
            self._add_identity(user, account, now)
        user.last_sign_in_at = now
        return user

    async def _connecting(self, connect_session: str, session_token: str | None) -> SignedIn:
        """The session that started a connect (`connect_session`, its hash), when the browser
        is still signed in with it (`session_token`).

        Raises:
            SignInFailed: The browser has no session, or another one (it signed out, or in as
                someone else), or the session ended (signed out everywhere, the account
                deleted).
        """
        signed_in = None
        if session_token is not None and secrets.compare_digest(
            _hash(session_token).hex(), connect_session
        ):
            signed_in = await self.signed_in(session_token)
        if signed_in is None:
            raise SignInFailed("the session that started the connect ended")
        return signed_in

    async def _connect(self, user: User, account: ProviderAccount) -> None:
        """Adds `account` to the user's sign-in methods. The beta list does not apply (the user
        is in already), and the user's email stays that of their first sign-in. A user deleted
        meanwhile fails the commit (the foreign key).

        Raises:
            SignInMethodTaken: The account signs in to another user.
            SignInFailed: The user signs in with another account of the provider already.
        """
        identity = await self._store.identity(account.provider, account.subject)
        if identity is not None:
            if identity.user_id != user.id:
                raise SignInMethodTaken
            return  # connected already
        await self._store.lock_user(user.id)
        if account.provider in await self._store.providers(user.id):
            raise SignInFailed(f"the user signs in with another {account.provider} account")
        self._add_identity(user, account, _now())
        await self._store.commit()

    def _add_identity(self, user: User, account: ProviderAccount, now: datetime) -> None:
        self._store.add(
            Identity(
                provider=account.provider,
                subject=account.subject,
                user=user,
                email=account.email,
                login=account.login,
                created_at=now,
            )
        )

    def _new_user(self, email: str, display_name: str, now: datetime) -> User:
        user = User(
            id=new_user_id(),
            display_name=display_name,
            email=email,
            created_at=now,
            last_sign_in_at=now,
        )
        self._store.add(user)
        return user

    async def dev_sign_in(self) -> str:
        """Signs in as the fixed dev user, without a provider or the beta list; returns the
        session's token."""
        now = _now()
        user = await self._store.user_by_email(DEV_EMAIL)
        if user is None:
            user = self._new_user(DEV_EMAIL, DEV_NAME, now)
        user.last_sign_in_at = now
        return await self._new_session(user)

    async def _new_session(self, user: User) -> str:
        """Starts a session of `user`, commits the sign-in and returns the session's token; the
        user's expired sessions go."""
        now = _now()
        token = secrets.token_urlsafe(32)
        await self._store.delete_expired_sessions(user.id, now)
        self._store.add(
            UserSession(
                token_hash=_hash(token),
                user=user,
                created_at=now,
                last_seen_at=now,
                expires_at=now + SESSION_LIFETIME,
            )
        )
        await self._store.commit()
        return token

    async def signed_in(self, token: str) -> SignedIn | None:
        """The user of the session with `token`; None when there is none or it expired (an
        expired one is deleted). A session last extended a day or more ago is extended."""
        found = await self._store.session(_hash(token))
        if found is None:
            return None
        session, user = found
        now = _now()
        if session.expires_at <= now:
            await self._store.delete_session(session.token_hash)
            await self._store.commit()
            return None
        extended = now - session.last_seen_at >= SESSION_REFRESH
        if extended:
            session.last_seen_at = now
            session.expires_at = now + SESSION_LIFETIME
            await self._store.commit()
        return SignedIn(user=user, extended=extended)

    async def sign_out(self, token: str) -> None:
        await self._store.delete_session(_hash(token))
        await self._store.commit()

    async def me(self, user: User) -> MeOut:
        methods = [
            SignInMethodOut(
                provider=Provider(identity.provider), email=identity.email, login=identity.login
            )
            for identity in await self._store.identities(user.id)
        ]
        return MeOut(
            id=user.id,
            display_name=user.display_name,
            email=user.email,
            providers=[method.provider for method in methods],
            sign_in_methods=methods,
            created_at=user.created_at,
        )

    async def denial(self, denial_id: str) -> DeniedSignInOut | None:
        return await self._pending.denial(denial_id)

    async def sign_out_everywhere(self, user: User) -> None:
        await self._store.delete_sessions(user.id)
        await self._store.commit()

    async def rename(self, user: User, display_name: str) -> MeOut:
        user.display_name = display_name
        await self._store.commit()
        return await self.me(user)

    async def disconnect(self, user: User, provider: Provider) -> None:
        """Removes the user's sign-in method of `provider`. Signing in later with an account of
        `provider` whose email is the user's adds it again.

        Raises:
            NoSuchSignInMethod: The user does not sign in with `provider`.
            LastSignInMethod: It is the user's only sign-in method.
        """
        await self._store.lock_user(user.id)  # two removals at once could leave none
        providers = await self._store.providers(user.id)
        if provider not in providers:
            raise NoSuchSignInMethod
        if set(providers) == {provider}:
            raise LastSignInMethod
        await self._store.delete_identities(user.id, provider)
        await self._store.commit()

    async def delete_account(self, user: User) -> None:
        """Deletes the user with their sign-in methods, sessions and preferences, and through
        the deletion hooks what other modules keep for them: their rows in one transaction,
        then, once it is committed, what lies outside Postgres."""
        after_commit = [
            after
            for hook in self._deletion_hooks
            if (after := await hook(user.id, self._store.db)) is not None
        ]
        await self._store.delete_user(user.id)
        await self._store.commit()
        for after in after_commit:
            try:
                await after()
            except Exception as error:  # noqa: BLE001 (the account is gone; the others still run)
                # By its type only: its message may hold the user's data (a file's path).
                logger.error(
                    "Deleting an account's data after the commit failed (%s)",
                    type(error).__name__,
                )

    async def preferences(self, user: User) -> PreferencesOut:
        """The user's settings; what they never saved has its default."""
        saved = await self._store.preferences(user.id)
        return PreferencesOut(
            backtest_defaults=BacktestDefaults.model_validate(
                saved.backtest_defaults if saved else {}
            ),
            appearance=Appearance.model_validate(saved.appearance if saved else {}),
        )

    async def save_backtest_defaults(
        self, user: User, defaults: BacktestDefaultsIn
    ) -> BacktestDefaults:
        await self._store.save_backtest_defaults(user.id, defaults.model_dump(mode="json"), _now())
        await self._store.commit()
        return defaults

    async def update_appearance(self, user: User, update: AppearanceUpdate) -> Appearance:
        """Saves the settings `update` gives; a field left out or null stays as it is."""
        merged = await self._store.merge_appearance(
            user.id, update.model_dump(mode="json", exclude_none=True), _now()
        )
        await self._store.commit()
        return Appearance.model_validate(merged)


def provider_display_name(account: ProviderAccount) -> str:
    """The display name of a new user: GitHub's name or else login, Google's name, else the
    email's local part; without surrounding whitespace and cut to what the user may save, so
    saving Profile unchanged always passes."""
    name = (account.name or "").strip() or account.login or account.email.partition("@")[0]
    return name[:DISPLAY_NAME_MAX].rstrip()
