"""Who sends a request, the session or an API key, and what a key may do: the dependencies of
``accounts.dependencies`` on routes of their own, with the keys and sessions faked. The real
routes on Postgres are integration tests (tests/integration/accounts/test_api_keys.py)."""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated

import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from candlestack.accounts import (
    BacktestsUserDep,
    CallerDep,
    CurrentUserDep,
    ModelsUserDep,
    Scope,
    User,
)
from candlestack.accounts.dependencies import (
    API_KEY_RATE_LIMIT,
    Caller,
    SessionUserDep,
    get_accounts_service,
    get_api_keys,
    reject_api_keys,
)
from candlestack.accounts.keys import KeyUser
from candlestack.accounts.models import ApiKey
from candlestack.accounts.service import SignedIn
from candlestack.core import (
    ProblemError,
    Settings,
    install_error_handlers,
    require_same_origin,
)
from candlestack.core.ratelimit import get_rate_limiter

PROBLEMS = "https://candlestack.tech/problems/"
ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")
SESSION = {"Cookie": "session=good-session"}
ORIGIN = {"Origin": "http://testserver"}
READ_KEY = "cs_readaaaa" + "a" * 32
ALL_KEY = "cs_allaaaaa" + "a" * 32
BACKTESTS_KEY = "cs_btaaaaaa" + "a" * 32
PUBLISH_KEY = "cs_pubaaaaa" + "a" * 32
SCOPES = {
    READ_KEY: [Scope.read],
    ALL_KEY: list(Scope),
    BACKTESTS_KEY: [Scope.backtests],
    PUBLISH_KEY: [Scope.publish],
}


def bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


@dataclass
class FakeKeys:
    used: list[str] = field(default_factory=list)

    async def sign_in(self, token: str) -> KeyUser | None:
        if token not in SCOPES:
            return None
        key = ApiKey(
            id=f"key_{token[3:13]}",
            user_id=ADA.id,
            scopes=[scope.value for scope in SCOPES[token]],
            created_at=datetime(2026, 11, 8, tzinfo=UTC),
        )
        return KeyUser(key, ADA)

    async def mark_used(self, key: ApiKey) -> None:
        self.used.append(key.id)


class FakeAccounts:
    async def signed_in(self, token: str) -> SignedIn | None:
        return SignedIn(ADA, extended=False) if token == "good-session" else None


@dataclass
class FakeLimiter:
    wait: float = 0.0
    keys: list[str] = field(default_factory=list)

    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        self.keys.append(f"{key} {limit}")
        return self.wait


@pytest.fixture
def keys() -> FakeKeys:
    return FakeKeys()


@pytest.fixture
def limiter() -> FakeLimiter:
    return FakeLimiter()


router = APIRouter()
CHANGES = [Depends(require_same_origin)]


@router.get("/read")
async def read(user: CurrentUserDep) -> str:
    return user.id


@router.post("/change", dependencies=CHANGES)
async def change(user: CurrentUserDep) -> str:
    return user.id


@router.post("/backtests", dependencies=CHANGES)
async def start(user: BacktestsUserDep) -> str:
    return user.id


@router.delete("/models", dependencies=CHANGES)
async def delete(user: ModelsUserDep) -> str:
    return user.id


@router.patch("/backtest", dependencies=CHANGES)
async def update(caller: CallerDep, public: bool = False) -> str:
    caller.require(*([Scope.backtests, Scope.publish] if public else [Scope.backtests]))
    return caller.user.id


@router.get("/session-only")
async def session_only(user: SessionUserDep) -> str:
    return user.id


@router.post("/sign-out", dependencies=[*CHANGES, Depends(reject_api_keys)])
async def sign_out() -> None:
    return None


async def needs_user(user: CurrentUserDep) -> User:
    return user


@router.get("/both", dependencies=[Depends(needs_user)])
async def both(user: CurrentUserDep, again: Annotated[User, Depends(needs_user)]) -> str:
    return user.id + again.id


@pytest.fixture
def client(keys: FakeKeys, limiter: FakeLimiter) -> TestClient:
    app = FastAPI()
    app.state.settings = Settings(app_env="test")
    install_error_handlers(app)
    app.dependency_overrides[get_api_keys] = lambda: keys
    app.dependency_overrides[get_accounts_service] = FakeAccounts
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.include_router(router)
    return TestClient(app)


def test_the_session_does_everything(client: TestClient) -> None:
    for method, path in [("GET", "/read"), ("POST", "/change"), ("POST", "/backtests")]:
        assert client.request(method, path, headers={**SESSION, **ORIGIN}).json() == ADA.id
    assert client.patch("/backtest?public=true", headers={**SESSION, **ORIGIN}).status_code == 200
    assert client.get("/session-only", headers=SESSION).json() == ADA.id


def test_without_a_session_or_a_key(client: TestClient) -> None:
    response = client.get("/read")

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


def test_a_key_with_read_reads(client: TestClient, keys: FakeKeys, limiter: FakeLimiter) -> None:
    response = client.get("/read", headers=bearer(READ_KEY))

    assert response.status_code == 200
    assert response.json() == ADA.id
    assert keys.used == ["key_readaaaaaa"]
    assert limiter.keys == [f"accounts:key-rl:key_readaaaaaa {API_KEY_RATE_LIMIT}"]


def test_the_bearer_scheme_is_case_insensitive(client: TestClient) -> None:
    assert client.get("/read", headers={"Authorization": f"bearer {READ_KEY}"}).status_code == 200


@pytest.mark.parametrize("key", ["cs_unknown", READ_KEY.upper()])
def test_an_invalid_key(client: TestClient, keys: FakeKeys, key: str) -> None:
    response = client.get("/read", headers=bearer(key))

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "invalid-api-key"
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'
    assert keys.used == []


def test_a_key_wins_over_a_cookie(client: TestClient) -> None:
    """A request that sends a key is the key's, whatever cookie comes with it."""
    response = client.get("/read", headers={**bearer("cs_unknown"), **SESSION})

    assert response.status_code == 401


def test_a_key_without_read(client: TestClient) -> None:
    response = client.get("/read", headers=bearer(BACKTESTS_KEY))

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "missing-scope"
    assert response.json()["scope"] == "read"


def test_a_key_without_a_scope_is_told_to_create_a_key_with_it() -> None:
    with pytest.raises(ProblemError) as refused:
        Caller(ADA, frozenset({Scope.read})).require(Scope.read, Scope.models)

    assert refused.value.status == 403
    assert refused.value.slug == "missing-scope"
    assert refused.value.extensions == {"scope": "models"}
    assert refused.value.detail == (
        "This API key does not have the scope models, which this request needs. Create a key "
        "with it in Settings, API and MCP."
    )


def test_a_connected_app_without_a_scope_is_told_to_connect_again() -> None:
    with pytest.raises(ProblemError) as refused:
        Caller(ADA, frozenset({Scope.read}), app=True).require(Scope.read, Scope.models)

    assert refused.value.status == 403
    assert refused.value.slug == "missing-scope"
    assert refused.value.extensions == {"scope": "models"}
    assert refused.value.detail == (
        "This connected app was not allowed the scope models, which this request needs. "
        "Disconnect it in Settings, API and MCP and connect it again, allowing that scope."
    )


def test_the_session_has_every_scope() -> None:
    Caller(ADA).require(*Scope)


def test_a_key_cannot_change_what_declares_no_scope(client: TestClient) -> None:
    response = client.post("/change", headers=bearer(ALL_KEY))

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "session-required"


def test_a_declared_scope(client: TestClient) -> None:
    assert client.post("/backtests", headers=bearer(BACKTESTS_KEY)).json() == ADA.id
    missing = client.delete("/models", headers=bearer(BACKTESTS_KEY))
    assert missing.status_code == 403
    assert missing.json()["scope"] == "models"
    assert "models" in missing.json()["detail"]


def test_a_route_that_checks_the_scopes_itself(client: TestClient) -> None:
    assert client.patch("/backtest", headers=bearer(BACKTESTS_KEY)).status_code == 200
    publish = client.patch("/backtest?public=true", headers=bearer(BACKTESTS_KEY))
    assert publish.status_code == 403
    assert publish.json()["scope"] == "publish"
    assert client.patch("/backtest", headers=bearer(PUBLISH_KEY)).json()["scope"] == "backtests"
    assert client.patch("/backtest?public=true", headers=bearer(ALL_KEY)).status_code == 200


@pytest.mark.parametrize("key", [ALL_KEY, "cs_unknown"])
def test_session_only_routes_refuse_any_key(client: TestClient, key: str) -> None:
    for response in (
        client.get("/session-only", headers={**bearer(key), **SESSION}),
        client.post("/sign-out", headers={**bearer(key), **ORIGIN}),
    ):
        assert response.status_code == 403
        assert response.json()["type"] == PROBLEMS + "session-required"


def test_the_rate_limit_per_key(client: TestClient, keys: FakeKeys, limiter: FakeLimiter) -> None:
    limiter.wait = 12.2

    response = client.get("/read", headers=bearer(READ_KEY))

    assert response.status_code == 429
    assert response.json()["type"] == PROBLEMS + "rate-limited"
    assert response.json()["limit"] == API_KEY_RATE_LIMIT
    assert response.headers["retry-after"] == "13"
    assert keys.used == []


def test_a_key_signs_in_once_per_request(client: TestClient, keys: FakeKeys) -> None:
    assert client.get("/both", headers=bearer(READ_KEY)).status_code == 200
    assert keys.used == ["key_readaaaaaa"]


# --- The same-origin check -------------------------------------------------------------------


def test_a_key_without_cookies_skips_the_origin_check(client: TestClient) -> None:
    response = client.post("/backtests", headers=bearer(ALL_KEY))

    assert response.status_code == 200


def test_a_key_with_a_cookie_keeps_the_origin_check(client: TestClient) -> None:
    response = client.post("/backtests", headers={**bearer(ALL_KEY), "Cookie": "other=1"})

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"
    assert "must not send cookies" in response.json()["detail"]
    allowed = client.post("/backtests", headers={**bearer(ALL_KEY), "Cookie": "a=1", **ORIGIN})
    assert allowed.status_code == 200


@pytest.mark.parametrize("authorization", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer ", "Token cs_x"])
def test_other_authorization_keeps_the_origin_check(client: TestClient, authorization: str) -> None:
    response = client.post("/backtests", headers={"Authorization": authorization})

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"
    assert "must not send cookies" not in response.json()["detail"]
