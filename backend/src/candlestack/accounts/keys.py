"""API keys: what scripts and agents sign in with instead of the browser's session.

A key is ``cs_`` and 40 random characters of lowercase base32 (200 bits): the first 8 are its
prefix, which the app shows to tell keys apart and which finds its row, the other 32 (160
bits) are the secret. Postgres keeps only the SHA-256 of the whole key, compared in constant
time, so the table cannot be used to sign in and the key is shown once, when it is created.

A key signs in as its user while it is neither revoked nor expired and the user's email is on
the beta list, checked on every request, so taking a user off the list stops their keys at
once. Its scopes (``schemas.Scope``) decide what it may do; the dependencies enforce them. Keys
are managed with the browser session only.
"""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from candlestack.accounts.errors import NoSuchApiKey, TooManyApiKeys
from candlestack.accounts.models import ApiKey, User, new_api_key_id
from candlestack.accounts.schemas import (
    API_KEYS_MAX,
    SCOPE_ORDER,
    ApiKeyCreated,
    ApiKeyIn,
    ApiKeyOut,
    ApiKeysOut,
    Scope,
)
from candlestack.accounts.service import admitted
from candlestack.accounts.store import AccountStore
from candlestack.core import Settings

KEY_PREFIX = "cs_"
_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
_SHOWN = 8
_SECRET = 32
_PREFIX_LENGTH = len(KEY_PREFIX) + _SHOWN
_KEY = re.compile(rf"{KEY_PREFIX}[{_ALPHABET}]{{{_SHOWN + _SECRET}}}")
# A key in use writes its last use at most this often, so a busy script costs one write a
# minute.
LAST_USED_REFRESH = timedelta(minutes=1)


def _now() -> datetime:
    """The time; patched by tests to move the clock."""
    return datetime.now(UTC)


def new_key() -> str:
    """A new random key, ``cs_`` and 40 characters."""
    return KEY_PREFIX + "".join(secrets.choice(_ALPHABET) for _ in range(_SHOWN + _SECRET))


def key_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def ordered(scopes: list[Scope] | list[str]) -> list[Scope]:
    """`scopes` once each, in the order of ``Scope``."""
    given = {Scope(scope) for scope in scopes}
    return [scope for scope in SCOPE_ORDER if scope in given]


def describe(key: ApiKey) -> ApiKeyOut:
    return ApiKeyOut(
        id=key.id,
        name=key.name,
        scopes=ordered(key.scopes),
        prefix=key.prefix,
        created_at=key.created_at,
        expires_at=key.expires_at,
        last_used_at=key.last_used_at,
    )


@dataclass(frozen=True, slots=True)
class KeyUser:
    """The user a key signs in as, and the key."""

    key: ApiKey
    user: User

    @property
    def scopes(self) -> frozenset[Scope]:
        return frozenset(Scope(scope) for scope in self.key.scopes)


class ApiKeys:
    def __init__(self, settings: Settings, store: AccountStore) -> None:
        self._settings = settings
        self._store = store

    async def list_keys(self, user: User) -> ApiKeysOut:
        return ApiKeysOut(
            items=[describe(key) for key in await self._store.api_keys(user.id)],
            limit=API_KEYS_MAX,
        )

    async def create(self, user: User, new: ApiKeyIn) -> ApiKeyCreated:
        """Creates a key; the answer holds its secret, which nothing keeps.

        Raises:
            TooManyApiKeys: The user has ``API_KEYS_MAX`` keys that are neither revoked nor
                expired.
        """
        now = _now()
        await self._store.lock_user(user.id)  # two creates at once could pass the limit
        if await self._store.active_api_keys(user.id, now) >= API_KEYS_MAX:
            raise TooManyApiKeys
        secret = new_key()
        key = ApiKey(
            id=new_api_key_id(),
            user_id=user.id,
            name=new.name,
            scopes=[scope.value for scope in ordered(new.scopes)],
            prefix=secret[:_PREFIX_LENGTH],
            secret_hash=key_hash(secret),
            created_at=now,
            expires_at=None
            if new.expires_in_days is None
            else now + timedelta(days=new.expires_in_days),
            last_used_at=None,
            revoked_at=None,
        )
        self._store.add(key)
        await self._store.commit()
        return ApiKeyCreated(**describe(key).model_dump(), secret=secret)

    async def revoke(self, user: User, key_id: str) -> None:
        """Revokes the user's key: it stops working at once and leaves the list.

        Raises:
            NoSuchApiKey: The user has no key with the ID that is not revoked.
        """
        key = await self._store.api_key(user.id, key_id)
        if key is None:
            raise NoSuchApiKey
        key.revoked_at = _now()
        await self._store.commit()

    async def sign_in(self, token: str) -> KeyUser | None:
        """The key `token` and its user, when it signs in: a key of the right form whose hash
        matches, neither revoked nor expired, of a user on the beta list. None otherwise,
        whatever the reason, so an answer tells nothing about other people's keys."""
        if not _KEY.fullmatch(token):
            return None
        found = await self._store.api_key_by_prefix(token[:_PREFIX_LENGTH])
        if found is None:
            return None
        key, user = found
        if not secrets.compare_digest(key.secret_hash, key_hash(token)):
            return None
        now = _now()
        if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now):
            return None
        if not admitted(self._settings, user.email):
            return None
        return KeyUser(key, user)

    async def mark_used(self, key: ApiKey) -> None:
        """Records that the key signed a request in, at most once a ``LAST_USED_REFRESH``."""
        now = _now()
        if key.last_used_at is None or now - key.last_used_at >= LAST_USED_REFRESH:
            key.last_used_at = now
            await self._store.commit()
