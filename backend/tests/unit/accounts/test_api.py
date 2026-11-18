"""The accounts routes up to where they would need Postgres or Redis; the flows with both are
integration tests (tests/integration/accounts)."""

import logging
from dataclasses import dataclass, field

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.routing import NoMatchFound

from candlestack.accounts import User, dev_sign_in_allowed
from candlestack.accounts.dependencies import current_user
from candlestack.core import Settings
from candlestack.core.ratelimit import get_rate_limiter
from candlestack.main import create_app

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}
# What a browser sends with a navigation from a page of the app, e.g. the Connect link.
LINK = {"Sec-Fetch-Site": "same-origin"}


@dataclass
class FakeLimiter:
    wait: float = 0.0
    keys: list[str] = field(default_factory=list)

    async def hit(self, key: str, limit: int, cost: int = 1, window: int = 60) -> float:
        self.keys.append(key)
        return self.wait


@pytest.fixture
def limiter() -> FakeLimiter:
    return FakeLimiter()


@pytest.fixture
def app(app: FastAPI, limiter: FakeLimiter) -> FastAPI:
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    return app


@pytest.mark.parametrize(
    ("app_env", "allowed"),
    [("local", True), ("test", True), ("stage", False), ("prod", False)],
)
def test_dev_sign_in_only_where_allowed(app_env: str, allowed: bool) -> None:
    app = create_app(Settings(app_env=app_env))

    assert dev_sign_in_allowed(app_env) is allowed
    try:
        app.url_path_for("dev_sign_in")
    except NoMatchFound:
        registered = False
    else:
        registered = True
    assert registered is allowed


def test_dev_sign_in_is_not_in_the_api_reference(client: TestClient) -> None:
    paths = client.get("/api/v1/openapi.json").json()["paths"]

    assert "/api/v1/auth/dev" not in paths
    assert "/api/v1/auth/{provider}/callback" not in paths


def test_me_without_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


def test_denied_without_a_denial(client: TestClient) -> None:
    response = client.get("/api/v1/auth/denied")

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "not-found"


def test_sign_out_without_a_session(client: TestClient) -> None:
    response = client.post("/api/v1/auth/sign-out", headers=ORIGIN)

    assert response.status_code == 204
    assert response.headers["set-cookie"].startswith('session=""; ')


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "http://testserver.evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_sign_out_from_another_origin(client: TestClient, headers: dict[str, str]) -> None:
    response = client.post("/api/v1/auth/sign-out", headers=headers)

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"
    assert "set-cookie" not in response.headers


def test_sign_out_same_origin_by_fetch_metadata(client: TestClient) -> None:
    response = client.post("/api/v1/auth/sign-out", headers={"Sec-Fetch-Site": "same-origin"})

    assert response.status_code == 204


def test_start_with_a_provider_that_is_not_configured(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        response = client.get("/api/v1/auth/google/start", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/signin?error=failed"
    assert "set-cookie" not in response.headers
    assert "Sign-in with google failed: google sign-in is not configured" in caplog.text


@pytest.mark.parametrize(
    ("next_path", "location"),
    [
        ("/oauth/consent?request=abc", "/signin?error=failed&next=/oauth/consent%3Frequest%3Dabc"),
        ("/dashboard", "/signin?error=failed"),
        ("//evil.example/", "/signin?error=failed"),
        ("https://evil.example/", "/signin?error=failed"),
        ("/api/v1/me", "/signin?error=failed"),
    ],
)
def test_a_failed_start_keeps_a_next_of_the_app(
    client: TestClient, limiter: FakeLimiter, next_path: str, location: str
) -> None:
    limiter.wait = 12.3

    for provider in ("google", "github"):  # not configured, and over the rate limit
        response = client.get(
            f"/api/v1/auth/{provider}/start", params={"next": next_path}, follow_redirects=False
        )
        assert response.headers["location"] == location


def test_a_failed_connect_start_does_not_carry_next(
    client: TestClient, limiter: FakeLimiter
) -> None:
    limiter.wait = 12.3

    response = client.get(
        "/api/v1/auth/github/start",
        params={"intent": "connect", "next": "/settings/sign-in?from=banner"},
        headers=LINK,
        follow_redirects=False,
    )

    assert response.headers["location"] == "/settings/sign-in?error=failed"


def test_start_with_an_unknown_provider(client: TestClient) -> None:
    response = client.get("/api/v1/auth/facebook/start", follow_redirects=False)

    assert response.status_code == 422


def test_start_over_the_rate_limit_fails(
    client: TestClient, limiter: FakeLimiter, caplog: pytest.LogCaptureFixture
) -> None:
    limiter.wait = 12.3

    response = client.get("/api/v1/auth/github/start", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/signin?error=failed"
    assert "set-cookie" not in response.headers
    assert limiter.keys == ["accounts:rl:testclient"]
    assert "Sign-in with github failed: too many sign-in requests" in caplog.text


def test_dev_sign_in_rate_limit(client: TestClient, limiter: FakeLimiter) -> None:
    limiter.wait = 12.3

    response = client.post("/api/v1/auth/dev", headers=ORIGIN)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "13"
    assert response.json()["type"] == PROBLEMS + "rate-limited"
    assert limiter.keys == ["accounts:rl:testclient"]


def test_callback_over_the_rate_limit_fails(client: TestClient, limiter: FakeLimiter) -> None:
    limiter.wait = 5

    response = client.get("/api/v1/auth/github/callback?code=c&state=s", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/signin?error=failed"


def test_callback_of_a_connect_over_the_rate_limit_returns_to_settings(
    client: TestClient, limiter: FakeLimiter
) -> None:
    limiter.wait = 5

    response = client.get(
        "/api/v1/auth/github/callback?code=c&state=connect.s", follow_redirects=False
    )

    assert response.headers["location"] == "/settings/sign-in?error=failed"


@pytest.mark.parametrize(
    ("next_path", "location"),
    [
        (None, "/signin?next=/settings/sign-in"),
        ("/settings/sign-in?from=banner", "/signin?next=/settings/sign-in%3Ffrom%3Dbanner"),
        ("https://evil.example/", "/signin?next=/settings/sign-in"),
    ],
)
def test_connect_without_a_session_signs_in_first(
    client: TestClient, next_path: str | None, location: str
) -> None:
    params = {"intent": "connect"} | ({"next": next_path} if next_path else {})

    response = client.get(
        "/api/v1/auth/google/start", params=params, headers=LINK, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == location
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Sec-Fetch-Site": "none"},
        {"Origin": "https://evil.example"},
    ],
)
def test_connect_from_another_site_fails(
    client: TestClient, headers: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """A link of another site must not connect the provider's account the browser is signed in
    to there, even when the provider asks for no consent again."""
    client.cookies.set("session", "some-token")

    with caplog.at_level(logging.WARNING):
        response = client.get(
            "/api/v1/auth/github/start",
            params={"intent": "connect"},
            headers=headers,
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/settings/sign-in?error=failed"
    assert "set-cookie" not in response.headers
    assert "the connect does not come from the app's own pages" in caplog.text


def test_connect_over_the_rate_limit_returns_to_settings(
    client: TestClient, limiter: FakeLimiter
) -> None:
    limiter.wait = 5
    client.cookies.set("session", "some-token")

    response = client.get(
        "/api/v1/auth/github/start", params={"intent": "connect"}, follow_redirects=False
    )

    assert response.headers["location"] == "/settings/sign-in?error=failed"


def test_start_with_an_unknown_intent(client: TestClient) -> None:
    response = client.get(
        "/api/v1/auth/github/start", params={"intent": "merge"}, follow_redirects=False
    )

    assert response.status_code == 422


SETTINGS_ROUTES = [
    ("PATCH", "/api/v1/me", {"display_name": "Ada"}),
    ("DELETE", "/api/v1/me", None),
    ("DELETE", "/api/v1/me/sign-in-methods/github", None),
    ("POST", "/api/v1/auth/sign-out-everywhere", None),
    ("PUT", "/api/v1/me/preferences/backtest-defaults", {}),
    ("PATCH", "/api/v1/me/preferences/appearance", {}),
]


@pytest.mark.parametrize(("method", "path", "body"), SETTINGS_ROUTES)
def test_settings_need_a_session(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    response = client.request(method, path, json=body, headers=ORIGIN)

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


def test_preferences_need_a_session(client: TestClient) -> None:
    response = client.get("/api/v1/me/preferences")

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


@pytest.mark.parametrize(("method", "path", "body"), SETTINGS_ROUTES)
def test_settings_need_the_apps_origin(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    response = client.request(method, path, json=body, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"


@pytest.fixture
def signed_in(app: FastAPI) -> None:
    """The requests come from a signed-in user; anything past validation needs Postgres."""
    app.dependency_overrides[current_user] = lambda: User(id="usr_aaaaaaaaaa")


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize(
    ("path", "body", "field"),
    [
        ("/api/v1/me", {"display_name": "   "}, "display_name"),
        ("/api/v1/me", {"display_name": "A" * 81}, "display_name"),
        ("/api/v1/me", {}, "display_name"),
        ("/api/v1/me/preferences/appearance", {"theme": "sepia"}, "theme"),
        ("/api/v1/me/preferences/appearance", {"reduce_motion": "sometimes"}, "reduce_motion"),
    ],
)
def test_invalid_changes(
    client: TestClient, path: str, body: dict[str, object], field: str
) -> None:
    response = client.patch(path, json=body, headers=ORIGIN)

    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "validation"
    assert [error["loc"] for error in problem["errors"]] == [["body", field]]


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize(
    "body",
    [
        {"period_start": "2024-01-01", "period_end": "2023-01-01"},
        {"threshold": 0.6},
        {"threshold": 0.055},
        {"stop_loss_pct": 0},
        {"fees_pct": -0.1},
        {"timeframe": "4h"},
        {"benchmark": "VTI"},
    ],
)
def test_invalid_backtest_defaults(client: TestClient, body: dict[str, object]) -> None:
    response = client.put("/api/v1/me/preferences/backtest-defaults", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"
