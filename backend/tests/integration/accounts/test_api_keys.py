"""API keys over HTTP, against a real Postgres and Redis (conftest.py): creating, listing and
revoking them in the browser's session, and signing requests in with them on the real routes."""

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candlestack.accounts import dependencies, keys

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}
KEYS = "/api/v1/me/api-keys"


class Script:
    """A script with an API key: no cookies, no Origin, only ``Authorization``."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def request(self, method: str, url: str, key: str, **kwargs: Any) -> httpx2.Response:
        headers = {"Authorization": f"Bearer {key}", **kwargs.pop("headers", {})}
        self._client.cookies = httpx2.Cookies()
        return self._client.request(method, url, headers=headers, **kwargs)


@pytest.fixture
def script(client: TestClient) -> Script:
    return Script(client)


def create(browser, **body: Any) -> httpx2.Response:
    return browser.post(KEYS, json={"name": "laptop", "scopes": ["read"], **body}, headers=ORIGIN)


def problem(response: httpx2.Response, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["type"] == PROBLEMS + slug
    return body


def test_create_shows_the_secret_once_and_keeps_its_hash(browser, script, sql) -> None:
    browser.sign_in("github")

    created = create(browser, name="  Claude Code  ", scopes=["publish", "read"])

    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    key = created.json()
    secret = key["secret"]
    assert key["name"] == "Claude Code"
    assert key["scopes"] == ["read", "publish"]
    assert secret.startswith(key["prefix"])
    assert key["expires_at"] is None
    assert key["last_used_at"] is None
    [(prefix, secret_hash, scopes)] = sql("SELECT prefix, secret_hash, scopes FROM api_keys")
    assert prefix == key["prefix"]
    assert bytes(secret_hash) == hashlib.sha256(secret.encode()).digest()
    assert scopes == ["read", "publish"]
    listed = browser.get(KEYS)
    assert listed.headers["cache-control"] == "no-store"
    assert listed.json() == {
        "items": [{k: v for k, v in key.items() if k != "secret"}],
        "limit": 10,
    }
    assert secret not in listed.text

    me = script.request("GET", "/api/v1/me", secret)

    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"
    assert browser.get(KEYS).json()["items"][0]["last_used_at"] is not None


def test_an_invalid_body_is_refused(browser) -> None:
    browser.sign_in("github")

    for body in (
        {"scopes": []},
        {"scopes": ["admin"]},
        {"name": " "},
        {"expires_in_days": 0},
        {"expires_in_days": 366},
        {"owner": "eve"},
    ):
        problem(create(browser, **body), 422, "validation")


def test_keys_are_managed_with_the_session_only(browser, script) -> None:
    browser.sign_in("github")
    secret = create(browser, scopes=["read", "backtests", "models", "publish"]).json()["secret"]
    key_id = browser.get(KEYS).json()["items"][0]["id"]

    for method, path in [
        ("GET", KEYS),
        ("POST", KEYS),
        ("DELETE", f"{KEYS}/{key_id}"),
        ("DELETE", "/api/v1/me"),
        ("DELETE", "/api/v1/me/sign-in-methods/github"),
        ("POST", "/api/v1/auth/sign-out-everywhere"),
        ("POST", "/api/v1/auth/sign-out"),
        ("PATCH", "/api/v1/me"),
        ("PATCH", "/api/v1/me/preferences/appearance"),
    ]:
        body = {"name": "x", "scopes": ["read"]} if method == "POST" and path == KEYS else None
        problem(script.request(method, path, secret, json=body), 403, "session-required")
    assert browser.me().status_code == 200
    assert len(browser.get(KEYS).json()["items"]) == 1


def test_a_key_with_backtests_saves_the_backtest_defaults(browser, script) -> None:
    browser.sign_in("github")
    reader = create(browser, scopes=["read"]).json()["secret"]
    starter = create(browser, scopes=["backtests"]).json()["secret"]
    defaults = {
        "timeframe": "1h",
        "period_start": "2022-01-03",
        "period_end": "2023-12-29",
        "direction": "both",
        "threshold": 0.1,
        "stop_loss_pct": 5,
        "fees_pct": 0.05,
        "slippage_pct": 0.01,
        "benchmark": "SPY",
    }
    path = "/api/v1/me/preferences/backtest-defaults"

    refused = problem(script.request("PUT", path, reader, json=defaults), 403, "missing-scope")
    saved = script.request("PUT", path, starter, json=defaults)

    assert refused["scope"] == "backtests"
    assert saved.status_code == 200, saved.text
    assert saved.json() == defaults
    assert browser.get("/api/v1/me/preferences").json()["backtest_defaults"] == defaults


def test_revoke(browser, script) -> None:
    browser.sign_in("github")
    secret = create(browser).json()["secret"]
    key_id = browser.get(KEYS).json()["items"][0]["id"]
    assert script.request("GET", "/api/v1/me", secret).status_code == 200

    revoked = browser.delete(f"{KEYS}/{key_id}", headers=ORIGIN)

    assert revoked.status_code == 204
    body = problem(script.request("GET", "/api/v1/me", secret), 401, "invalid-api-key")
    assert secret not in body["detail"]
    assert browser.get(KEYS).json()["items"] == []
    problem(browser.delete(f"{KEYS}/{key_id}", headers=ORIGIN), 404, "not-found")


def test_revoke_needs_the_apps_origin(browser) -> None:
    browser.sign_in("github")
    create(browser)
    key_id = browser.get(KEYS).json()["items"][0]["id"]

    problem(browser.delete(f"{KEYS}/{key_id}"), 403, "cross-origin-request")
    problem(browser.post(KEYS, json={"name": "x", "scopes": ["read"]}), 403, "cross-origin-request")


def test_another_users_key_cannot_be_revoked(browser, other_browser) -> None:
    browser.sign_in("github")
    create(browser)
    key_id = browser.get(KEYS).json()["items"][0]["id"]
    other_browser.post("/api/v1/auth/dev", headers=ORIGIN)  # the dev user, not Ada

    problem(other_browser.delete(f"{KEYS}/{key_id}", headers=ORIGIN), 404, "not-found")
    assert other_browser.get(KEYS).json()["items"] == []
    assert len(browser.get(KEYS).json()["items"]) == 1


def test_an_expired_key(browser, script, monkeypatch) -> None:
    browser.sign_in("github")
    secret = create(browser, expires_in_days=1).json()["secret"]
    expires_at = datetime.fromisoformat(browser.get(KEYS).json()["items"][0]["expires_at"])
    assert script.request("GET", "/api/v1/me", secret).status_code == 200

    monkeypatch.setattr(keys, "_now", lambda: expires_at)

    problem(script.request("GET", "/api/v1/me", secret), 401, "invalid-api-key")
    assert len(browser.get(KEYS).json()["items"]) == 1  # listed until revoked


@pytest.mark.parametrize("token", ["cs_" + "a" * 40, "cs_short", "not-a-key", "cs_" + "a" * 41])
def test_an_unknown_key(script, token) -> None:
    response = script.request("GET", "/api/v1/me", token)

    problem(response, 401, "invalid-api-key")
    assert response.headers["www-authenticate"] == 'Bearer error="invalid_token"'


def test_a_wrong_secret_with_a_real_prefix(browser, script) -> None:
    browser.sign_in("github")
    secret = create(browser).json()["secret"]
    wrong = secret[:11] + ("a" if secret[-1] != "a" else "b") * 32

    problem(script.request("GET", "/api/v1/me", wrong), 401, "invalid-api-key")


def test_a_user_off_the_beta_list_loses_the_keys(app: FastAPI, browser, script) -> None:
    browser.sign_in("github")
    secret = create(browser).json()["secret"]
    assert script.request("GET", "/api/v1/me", secret).status_code == 200

    app.state.settings = app.state.settings.model_copy(update={"beta_allowlist": frozenset()})

    problem(script.request("GET", "/api/v1/me", secret), 401, "invalid-api-key")


def test_at_most_ten_active_keys(browser) -> None:
    browser.sign_in("github")
    for _ in range(10):
        assert create(browser).status_code == 201

    body = problem(create(browser), 409, "too-many-api-keys")

    assert body["limit"] == 10
    key_id = browser.get(KEYS).json()["items"][0]["id"]
    browser.delete(f"{KEYS}/{key_id}", headers=ORIGIN)
    assert create(browser).status_code == 201


def test_scopes_on_the_real_routes(browser, script) -> None:
    browser.sign_in("github")
    read = create(browser, scopes=["read"]).json()["secret"]
    models = create(browser, scopes=["models"]).json()["secret"]
    backtests = create(browser, scopes=["backtests"]).json()["secret"]
    publish = create(browser, scopes=["publish"]).json()["secret"]
    model = "/api/v1/models/mdl_nothingxx"
    backtest = "/api/v1/backtests/bt_nothingxx"

    assert script.request("GET", "/api/v1/models", read).status_code == 200
    assert script.request("GET", "/api/v1/backtests", read).status_code == 200
    body = problem(script.request("GET", "/api/v1/models", models), 403, "missing-scope")
    assert body["scope"] == "read"
    body = problem(
        script.request("PATCH", model, read, json={"starred": True}), 403, "missing-scope"
    )
    assert body["scope"] == "models"
    # Past the scope and the origin check, the route itself answers.
    problem(script.request("PATCH", model, models, json={"starred": True}), 404, "not-found")
    problem(script.request("DELETE", model, models), 404, "not-found")
    body = problem(
        script.request("POST", "/api/v1/backtests", models, json={}), 403, "missing-scope"
    )
    assert body["scope"] == "backtests"
    problem(script.request("POST", "/api/v1/backtests", backtests, json={}), 422, "validation")
    problem(script.request("PATCH", backtest, backtests, json={"name": "x"}), 404, "not-found")
    body = problem(script.request("POST", f"{backtest}/cancel", read), 403, "missing-scope")
    assert body["scope"] == "backtests"
    problem(script.request("POST", f"{backtest}/cancel", backtests), 404, "not-found")
    body = problem(
        script.request("PATCH", backtest, backtests, json={"public": True}), 403, "missing-scope"
    )
    assert body["scope"] == "publish"
    problem(script.request("PATCH", backtest, publish, json={"public": True}), 404, "not-found")
    body = problem(
        script.request("PATCH", backtest, publish, json={"public": True, "starred": True}),
        403,
        "missing-scope",
    )
    assert body["scope"] == "backtests"


def test_a_key_with_a_cookie_needs_the_apps_origin(browser, client) -> None:
    browser.sign_in("github")
    secret = create(browser, scopes=["models"]).json()["secret"]

    response = client.request(
        "DELETE",
        "/api/v1/models/mdl_nothingxx",
        headers={"Authorization": f"Bearer {secret}", "Cookie": "theme=dark"},
    )

    problem(response, 403, "cross-origin-request")


def test_the_rate_limit_per_key(browser, script, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "API_KEY_RATE_LIMIT", 2)
    browser.sign_in("github")
    first = create(browser).json()["secret"]
    second = create(browser).json()["secret"]

    assert script.request("GET", "/api/v1/me", first).status_code == 200
    assert script.request("GET", "/api/v1/me", first).status_code == 200
    limited = script.request("GET", "/api/v1/me", first)

    body = problem(limited, 429, "rate-limited")
    assert body["limit"] == 2
    assert 1 <= int(limited.headers["retry-after"]) <= 60
    assert script.request("GET", "/api/v1/me", second).status_code == 200  # its own budget


def test_last_use_is_written_at_most_once_a_minute(browser, script, sql, monkeypatch) -> None:
    browser.sign_in("github")
    secret = create(browser).json()["secret"]
    start = datetime.now(UTC)
    monkeypatch.setattr(keys, "_now", lambda: start)
    script.request("GET", "/api/v1/me", secret)

    monkeypatch.setattr(keys, "_now", lambda: start + timedelta(seconds=59))
    script.request("GET", "/api/v1/me", secret)
    assert sql("SELECT last_used_at FROM api_keys") == [(start,)]

    monkeypatch.setattr(keys, "_now", lambda: start + timedelta(minutes=1))
    script.request("GET", "/api/v1/me", secret)
    assert sql("SELECT last_used_at FROM api_keys") == [(start + timedelta(minutes=1),)]


def test_the_secret_is_never_logged(browser, script, caplog) -> None:
    browser.sign_in("github")
    with caplog.at_level(logging.DEBUG):
        secret = create(browser).json()["secret"]
        script.request("GET", "/api/v1/me", secret)
        script.request("POST", "/api/v1/auth/sign-out", secret)
        script.request("GET", "/api/v1/me", secret[:-1] + "a")

    assert secret[11:] not in caplog.text


def test_keys_go_with_the_account(browser, sql) -> None:
    browser.sign_in("github")
    create(browser)

    assert browser.delete("/api/v1/me", headers=ORIGIN).status_code == 204

    assert sql("SELECT id FROM api_keys") == []
