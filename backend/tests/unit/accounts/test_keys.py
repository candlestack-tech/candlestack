"""The API keys service against a store in memory; the same flows on Postgres and over HTTP are
integration tests (tests/integration/accounts/test_api_keys.py)."""

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from candlestack.accounts import keys
from candlestack.accounts.errors import NoSuchApiKey, TooManyApiKeys
from candlestack.accounts.keys import ApiKeys, KeyUser, key_hash, new_key, ordered
from candlestack.accounts.models import ApiKey, User
from candlestack.accounts.schemas import API_KEYS_MAX, ApiKeyCreated, ApiKeyIn, Scope
from candlestack.accounts.service import DEV_EMAIL
from candlestack.core import Settings

if TYPE_CHECKING:
    from candlestack.accounts.store import AccountStore

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")


class FakeStore:
    """The key queries of ``AccountStore`` on a list."""

    def __init__(self, users: list[User] | None = None) -> None:
        self.keys: list[ApiKey] = []
        self.users = {user.id: user for user in users or [ADA]}
        self.commits = 0
        self.locked: list[str] = []
        self.lookups = 0

    def add(self, row: ApiKey) -> None:
        self.keys.append(row)

    async def commit(self) -> None:
        self.commits += 1

    async def lock_user(self, user_id: str) -> None:
        self.locked.append(user_id)

    async def api_keys(self, user_id: str) -> list[ApiKey]:
        return [key for key in self.keys if key.user_id == user_id and key.revoked_at is None]

    async def active_api_keys(self, user_id: str, now: datetime) -> int:
        return sum(
            1
            for key in await self.api_keys(user_id)
            if key.expires_at is None or key.expires_at > now
        )

    async def api_key(self, user_id: str, key_id: str) -> ApiKey | None:
        found = [key for key in await self.api_keys(user_id) if key.id == key_id]
        return found[0] if found else None

    async def api_key_by_prefix(self, prefix: str) -> tuple[ApiKey, User] | None:
        self.lookups += 1
        found = [key for key in self.keys if key.prefix == prefix]
        return (found[0], self.users[found[0].user_id]) if found else None


def _keys(settings: Settings, store: "FakeStore") -> ApiKeys:
    return ApiKeys(settings, cast("AccountStore", store))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """The time the service sees: ``clock[0]``, moved by the test."""
    now = [NOW]
    monkeypatch.setattr(keys, "_now", lambda: now[0])
    return now


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def service(store: FakeStore) -> ApiKeys:
    settings = Settings(app_env="prod", beta_allowlist=frozenset({"ada@example.com"}))
    return _keys(settings, store)


def _new(**fields: object) -> ApiKeyIn:
    return ApiKeyIn.model_validate({"name": "laptop", "scopes": ["read"], **fields})


def test_a_key_is_the_prefix_and_200_random_bits() -> None:
    made = {new_key() for _ in range(50)}

    assert len(made) == 50
    assert all(re.fullmatch(r"cs_[a-z2-7]{40}", key) for key in made)
    assert key_hash("cs_x") == hashlib.sha256(b"cs_x").digest()


def test_scopes_are_listed_once_in_their_order() -> None:
    assert ordered(["publish", "read", "publish"]) == [Scope.read, Scope.publish]


async def test_create_keeps_only_the_hash(service: ApiKeys, store: FakeStore, clock) -> None:
    created = await service.create(
        ADA, _new(name="CI", scopes=["publish", "read", "read"], expires_in_days=30)
    )

    [row] = store.keys
    assert created.secret.startswith(created.prefix)
    assert row.prefix == created.prefix == created.secret[:11]
    assert row.secret_hash == hashlib.sha256(created.secret.encode()).digest()
    assert created.secret not in repr(vars(row))
    assert row.scopes == ["read", "publish"]
    assert created.scopes == [Scope.read, Scope.publish]
    assert created.expires_at == NOW + timedelta(days=30)
    assert created.last_used_at is None
    assert store.locked == [ADA.id]
    assert store.commits == 1


async def test_without_expiry_a_key_works_until_revoked(service: ApiKeys, clock) -> None:
    assert (await service.create(ADA, _new())).expires_at is None


def test_the_secret_is_not_in_the_repr() -> None:
    created = ApiKeyCreated(
        id="key_aaaaaaaaaa",
        name="laptop",
        scopes=[Scope.read],
        prefix="cs_aaaaaaaa",
        created_at=NOW,
        expires_at=None,
        last_used_at=None,
        secret="cs_aaaaaaaa" + "b" * 32,
    )

    assert "bbbb" not in repr(created)
    assert "bbbb" not in str(created)


async def test_the_limit_counts_keys_neither_revoked_nor_expired(
    service: ApiKeys, store: FakeStore, clock
) -> None:
    for _ in range(API_KEYS_MAX - 1):
        await service.create(ADA, _new())
    expiring = await service.create(ADA, _new(expires_in_days=1))

    with pytest.raises(TooManyApiKeys):
        await service.create(ADA, _new())

    clock[0] = NOW + timedelta(days=1)  # the last one expired
    await service.create(ADA, _new())
    assert [key.id for key in (await service.list_keys(ADA)).items][-2] == expiring.id


async def test_list_leaves_revoked_keys_out(service: ApiKeys, clock) -> None:
    kept = await service.create(ADA, _new(name="kept"))
    gone = await service.create(ADA, _new(name="gone"))

    await service.revoke(ADA, gone.id)

    listed = await service.list_keys(ADA)
    assert [key.id for key in listed.items] == [kept.id]
    assert listed.limit == API_KEYS_MAX


async def test_revoke_a_key_that_is_not_there(service: ApiKeys, clock) -> None:
    key = await service.create(ADA, _new())
    await service.revoke(ADA, key.id)

    with pytest.raises(NoSuchApiKey):
        await service.revoke(ADA, key.id)
    with pytest.raises(NoSuchApiKey):
        await service.revoke(ADA, "key_nothinghere")


async def test_sign_in(service: ApiKeys, clock) -> None:
    created = await service.create(ADA, _new(scopes=["read", "models"]))

    signed_in = await service.sign_in(created.secret)

    assert isinstance(signed_in, KeyUser)
    assert signed_in.user is ADA
    assert signed_in.key.id == created.id
    assert signed_in.scopes == {Scope.read, Scope.models}


@pytest.mark.parametrize(
    "token",
    ["", "cs_", "cs_short", "CS_" + "a" * 40, "cs_" + "a" * 41, "cs_" + "1" * 40, "Bearer x"],
)
async def test_a_token_of_another_form_is_not_looked_up(
    service: ApiKeys, store: FakeStore, token: str
) -> None:
    assert await service.sign_in(token) is None
    assert store.lookups == 0


async def test_a_wrong_secret_with_a_known_prefix(service: ApiKeys, clock) -> None:
    created = await service.create(ADA, _new())
    wrong = created.secret[:11] + ("a" if created.secret[11] != "a" else "b") + created.secret[12:]

    assert await service.sign_in(wrong) is None
    assert await service.sign_in("cs_zzzzzzzz" + "a" * 32) is None


async def test_a_revoked_key_does_not_sign_in(service: ApiKeys, clock) -> None:
    created = await service.create(ADA, _new())
    await service.revoke(ADA, created.id)

    assert await service.sign_in(created.secret) is None


async def test_an_expired_key_does_not_sign_in(service: ApiKeys, clock) -> None:
    created = await service.create(ADA, _new(expires_in_days=7))

    clock[0] = NOW + timedelta(days=7) - timedelta(seconds=1)
    assert await service.sign_in(created.secret) is not None
    clock[0] = NOW + timedelta(days=7)
    assert await service.sign_in(created.secret) is None


async def test_a_user_off_the_beta_list_loses_the_keys(store: FakeStore, clock) -> None:
    on_list = _keys(Settings(app_env="prod", beta_allowlist=frozenset({ADA.email})), store)
    created = await on_list.create(ADA, _new())
    off_list = _keys(Settings(app_env="prod", beta_allowlist=frozenset()), store)

    assert await on_list.sign_in(created.secret) is not None
    assert await off_list.sign_in(created.secret) is None


@pytest.mark.parametrize(
    ("app_env", "signs_in"), [("local", True), ("test", True), ("prod", False)]
)
async def test_the_dev_user_signs_in_where_the_dev_sign_in_is(
    clock, app_env: str, signs_in: bool
) -> None:
    dev = User(id="usr_devaaaaaa", display_name="Dev user", email=DEV_EMAIL)
    store = FakeStore([dev])
    service = _keys(Settings(app_env=app_env), store)
    created = await service.create(dev, _new())

    assert (await service.sign_in(created.secret) is not None) is signs_in


async def test_last_use_is_written_at_most_once_a_minute(
    service: ApiKeys, store: FakeStore, clock
) -> None:
    created = await service.create(ADA, _new())
    [key] = store.keys
    commits = store.commits

    await service.mark_used(key)
    clock[0] = NOW + timedelta(seconds=59)
    await service.mark_used(key)

    assert key.last_used_at == NOW
    assert store.commits == commits + 1
    clock[0] = NOW + timedelta(minutes=1)
    await service.mark_used(key)
    assert key.last_used_at == NOW + timedelta(minutes=1)
    assert (await service.list_keys(ADA)).items[0].last_used_at == key.last_used_at
    assert created.id == key.id
