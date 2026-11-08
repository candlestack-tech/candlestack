"""Signing in and out over HTTP, against a real Postgres and Redis, with GitHub and Google
mocked (conftest.py)."""

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from candlestack.accounts import dependencies, service, store

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}


def cookies_set(response) -> dict[str, str]:
    """The cookies a response sets or deletes, by name: the whole Set-Cookie line."""
    return {line.split("=", 1)[0]: line for line in response.headers.get_list("set-cookie")}


def test_start_sends_the_browser_to_github(browser) -> None:
    start = browser.start("github")

    assert start.status_code == 302
    location = urlsplit(start.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://github.com/login/oauth/authorize"
    )
    query = parse_qs(location.query)
    assert query["client_id"] == ["gh-client"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://testserver/api/v1/auth/github/callback"]
    state_cookie = cookies_set(start)["sign_in_state"]
    assert f"sign_in_state={query['state'][0]};" in state_cookie
    assert "HttpOnly" in state_cookie
    assert "Max-Age=600" in state_cookie


def test_redis_keys_hold_no_state_or_denial_id(browser, providers, auth_keys) -> None:
    start = browser.start("github")
    state = browser.state_of(start)
    providers.github_account(email="grace@example.com")

    pending = auth_keys()
    browser.callback("github", code="the-code", state=state)
    denied = auth_keys()

    # Redis keys show up in traces: they name the state and the denial by their SHA-256.
    assert pending == [f"auth:state:{hashlib.sha256(state.encode()).hexdigest()}"]
    denial_id = browser.cookie("sign_in_denied")
    assert denial_id
    assert denied == [f"auth:denied:{hashlib.sha256(denial_id.encode()).hexdigest()}"]


def test_the_callback_signs_in(browser, sql) -> None:
    callback = browser.sign_in("github")

    assert callback.status_code == 302
    assert callback.headers["location"] == "/dashboard"
    set_cookies = cookies_set(callback)
    assert "Max-Age=2592000" in set_cookies["session"]
    assert "SameSite=lax" in set_cookies["session"]
    assert set_cookies["sign_in_state"].startswith('sign_in_state=""')
    # Postgres keeps only the token's hash.
    token = browser.cookie("session")
    [(stored,)] = sql("SELECT token_hash FROM sessions")
    assert stored == hashlib.sha256(token.encode()).digest()


def test_me(browser) -> None:
    browser.sign_in("github")

    me = browser.me()

    assert me.status_code == 200
    assert me.headers["cache-control"] == "no-store"
    body = me.json()
    assert re.fullmatch(r"usr_[a-z2-7]{10}", body["id"])
    assert body["display_name"] == "Ada Lovelace"
    assert body["email"] == "ada@example.com"
    assert body["providers"] == ["github"]
    assert body["sign_in_methods"] == [
        {"provider": "github", "email": "ada@example.com", "login": "ada"}
    ]


def test_sign_out(browser, sql) -> None:
    browser.sign_in("github")

    sign_out = browser.sign_out()

    assert sign_out.status_code == 204
    assert cookies_set(sign_out)["session"].startswith('session=""')
    assert sql("SELECT token_hash FROM sessions") == []
    me = browser.me()
    assert me.status_code == 401
    assert me.json()["type"] == PROBLEMS + "not-signed-in"


def test_google_sign_in(browser) -> None:
    callback = browser.sign_in("google", next_path="/instruments?market=crypto")

    assert callback.headers["location"] == "/instruments?market=crypto"
    body = browser.me().json()
    assert (body["display_name"], body["email"], body["providers"]) == (
        "Ada L.",
        "ada@example.com",
        ["google"],
    )


@pytest.mark.parametrize(
    "next_path",
    [
        "https://evil.example/",
        "//evil.example/",
        "/" + chr(92) + "evil.example",
        "/api/v1/auth/sign-out",
        "dashboard",
    ],
)
def test_next_never_leaves_the_app(browser, next_path: str) -> None:
    assert browser.sign_in("github", next_path=next_path).headers["location"] == "/dashboard"


def test_an_account_not_on_the_beta_list(browser, other_browser, providers, sql) -> None:
    providers.github_account(email="Grace@Example.com", name="Grace")

    callback = browser.sign_in("github")

    assert callback.status_code == 302
    assert callback.headers["location"] == "/signin?error=denied"
    assert "grace" not in str(callback.headers).lower()
    assert "session" not in cookies_set(callback)
    assert sql("SELECT id FROM users") == []
    denied = browser.get("/api/v1/auth/denied")
    assert denied.status_code == 200
    assert denied.headers["cache-control"] == "no-store"
    assert denied.json() == {"email": "grace@example.com", "provider": "github"}
    assert browser.me().status_code == 401
    # Only the browser that was turned away learns which account it was.
    assert other_browser.get("/api/v1/auth/denied").status_code == 404


CONSENT = "/oauth/consent?request=req_abc"
CONSENT_QUERY = "next=/oauth/consent%3Frequest%3Dreq_abc"


def test_turned_away_from_an_app_s_consent_page_the_browser_keeps_next(browser, providers) -> None:
    providers.github_account(email="grace@example.com")

    callback = browser.sign_in("github", next_path=CONSENT)

    assert callback.headers["location"] == f"/signin?error=denied&{CONSENT_QUERY}"
    assert browser.get("/api/v1/auth/denied").json()["email"] == "grace@example.com"


def test_a_failed_sign_in_keeps_next(browser, providers) -> None:
    start = browser.start("google", CONSENT)
    cancelled = browser.callback("google", error="access_denied", state=browser.state_of(start))
    start = browser.start("github", CONSENT)
    providers.challenge = "another"  # the token request fails the PKCE check
    refused = browser.callback("github", code="the-code", state=browser.state_of(start))

    assert cancelled.headers["location"] == f"/signin?error=failed&{CONSENT_QUERY}"
    assert refused.headers["location"] == f"/signin?error=failed&{CONSENT_QUERY}"


@pytest.mark.parametrize(
    "next_path", ["https://evil.example/", "//evil.example/", "/api/v1/me", "/dashboard"]
)
def test_a_turned_away_sign_in_keeps_only_a_next_of_the_app(
    browser, providers, next_path: str
) -> None:
    providers.github_account(email="grace@example.com")

    callback = browser.sign_in("github", next_path=next_path)

    assert callback.headers["location"] == "/signin?error=denied"


def test_a_sign_in_whose_state_is_unknown_has_no_next(browser) -> None:
    start = browser.start("github", CONSENT)

    callback = browser.callback("github", code="the-code", state=browser.state_of(start) + "x")

    assert callback.headers["location"] == "/signin?error=failed"


def test_a_sign_in_cancelled_at_the_provider(browser, caplog) -> None:
    start = browser.start("github")
    state = browser.state_of(start)

    with caplog.at_level(logging.WARNING):
        callback = browser.callback("github", error="access_denied", state=state)

    assert callback.headers["location"] == "/signin?error=failed"
    assert "returned no code" in caplog.text
    # The state is spent: the same sign-in cannot be finished afterwards.
    assert browser.callback("github", code="the-code", state=state).headers["location"] == (
        "/signin?error=failed"
    )


def test_a_state_from_another_browser(browser, other_browser) -> None:
    """Login CSRF: a victim's browser must not finish the attacker's sign-in."""
    start = browser.start("github")
    state = browser.state_of(start)

    callback = other_browser.callback("github", code="the-code", state=state)

    assert callback.headers["location"] == "/signin?error=failed"
    assert other_browser.me().status_code == 401


@pytest.mark.parametrize("state", ["", "forged", "x" * 100, chr(0x17E) * 43])
def test_a_state_that_does_not_match(browser, state: str) -> None:
    browser.start("github")

    assert browser.callback("github", code="the-code", state=state).headers["location"] == (
        "/signin?error=failed"
    )


def test_a_state_of_another_provider(browser) -> None:
    start = browser.start("github")
    state = browser.state_of(start)

    callback = browser.callback("google", code="the-code", state=state)

    assert callback.headers["location"] == "/signin?error=failed"


def test_an_expired_state(browser, forget_pending) -> None:
    start = browser.start("github")
    state = browser.state_of(start)
    forget_pending()

    assert browser.callback("github", code="the-code", state=state).headers["location"] == (
        "/signin?error=failed"
    )


def test_a_code_the_provider_refuses(browser, providers) -> None:
    start = browser.start("github")
    state = browser.state_of(start)
    providers.challenge = "another"  # the token request fails the PKCE check

    assert browser.callback("github", code="the-code", state=state).headers["location"] == (
        "/signin?error=failed"
    )


def test_an_account_without_a_verified_email(browser, providers) -> None:
    providers.github_account(verified=False)

    assert browser.sign_in("github").headers["location"] == "/signin?error=failed"


def test_the_same_email_links_the_accounts(browser, sql) -> None:
    browser.sign_in("github")
    first = browser.me().json()
    browser.sign_out()

    browser.sign_in("google")
    second = browser.me().json()

    assert second["id"] == first["id"]
    assert second["display_name"] == "Ada Lovelace"
    assert second["providers"] == ["github", "google"]
    assert len(sql("SELECT id FROM users")) == 1


def test_the_email_does_not_link_a_second_account_of_a_provider(
    browser, other_browser, providers, sql, caplog
) -> None:
    """A user signs in with one account per provider: a GitHub account with the user's email
    does not join a user who connected another GitHub account."""
    providers.google_account(email="ada@example.com")
    browser.sign_in("google")
    providers.github_account(subject=7, email="ada@work.example")
    browser.connect("github")
    providers.github_account(subject=42, email="ada@example.com")

    with caplog.at_level(logging.WARNING):
        callback = other_browser.sign_in("github")

    assert callback.headers["location"] == "/signin?error=failed"
    assert "session" not in cookies_set(callback)
    assert "the user signs in with another github account" in caplog.text
    assert sql("SELECT provider, subject FROM identities ORDER BY provider") == [
        ("github", "7"),
        ("google", "1077"),
    ]


def test_a_known_identity_signs_in_as_its_user(browser, providers, sql) -> None:
    browser.sign_in("github")
    first = browser.me().json()
    # The account's email and login changed at GitHub since.
    providers.github_account(email="ada@example.com", login="ada-l")

    browser.sign_in("github")

    assert browser.me().json()["id"] == first["id"]
    assert sql("SELECT login FROM identities") == [("ada-l",)]
    assert len(sql("SELECT token_hash FROM sessions")) == 2


def test_the_display_name_falls_back_to_the_login(browser, providers) -> None:
    providers.github_account(name=None)

    browser.sign_in("github")

    assert browser.me().json()["display_name"] == "ada"


def test_a_long_name_is_cut_to_what_the_user_may_save(browser, providers) -> None:
    providers.github_account(name="A" * 300)

    assert browser.sign_in("github").headers["location"] == "/dashboard"
    name = browser.me().json()["display_name"]
    assert name == "A" * 80
    # Profile saved unchanged
    assert browser.patch("/api/v1/me", json={"display_name": name}, headers=ORIGIN).is_success


def test_a_first_sign_in_that_loses_a_race_fails(
    browser, other_browser, monkeypatch, sql, caplog
) -> None:
    other_browser.sign_in("google")

    # The GitHub sign-in of the same email looked for its user before the Google one saved it.
    async def no_user(_store, _email: str) -> None:
        return None

    monkeypatch.setattr(store.AccountStore, "user_by_email", no_user)
    with caplog.at_level(logging.ERROR):
        callback = browser.sign_in("github")

    assert callback.status_code == 302
    assert callback.headers["location"] == "/signin?error=failed"
    assert "session" not in cookies_set(callback)
    assert "Postgres failed (IntegrityError)" in caplog.text
    assert "ada@example.com" not in caplog.text
    assert sql("SELECT provider FROM identities") == [("google",)]


def test_a_sign_in_without_postgres_fails(browser_without_postgres, caplog) -> None:
    with caplog.at_level(logging.INFO):
        callback = browser_without_postgres.sign_in("github")

    assert callback.status_code == 302
    assert callback.headers["location"] == "/signin?error=failed"
    assert "session" not in cookies_set(callback)
    # asyncpg raises an OSError or a TimeoutError, which SQLAlchemy does not wrap.
    assert re.search(r"Sign-in with github failed: Postgres failed \(\w+Error\)", caplog.text)
    assert "ada@example.com" not in caplog.text.lower()
    assert not [record for record in caplog.records if record.exc_info]


def test_dev_sign_in(browser, sql) -> None:
    first = browser.post("/api/v1/auth/dev", headers=ORIGIN)

    assert first.status_code == 204
    assert "Max-Age=2592000" in cookies_set(first)["session"]
    me = browser.me().json()
    assert (me["display_name"], me["email"], me["providers"]) == (
        "Dev user",
        "dev@candlestack.tech",
        [],
    )
    assert browser.post("/api/v1/auth/dev", headers=ORIGIN).status_code == 204
    assert browser.me().json()["id"] == me["id"]
    assert len(sql("SELECT id FROM users")) == 1


def test_dev_sign_in_from_another_origin(browser) -> None:
    response = browser.post("/api/v1/auth/dev", headers={"Origin": "https://evil.example"})

    assert response.status_code == 403


def _move_clock(monkeypatch, by: timedelta) -> None:
    moved = datetime.now(UTC) + by
    monkeypatch.setattr(service, "_now", lambda: moved)


def test_an_expired_session(browser, monkeypatch, sql) -> None:
    browser.sign_in("github")
    _move_clock(monkeypatch, timedelta(days=30, minutes=1))

    me = browser.me()

    assert me.status_code == 401
    assert sql("SELECT token_hash FROM sessions") == []


def test_a_session_in_use_slides_on_once_a_day(browser, monkeypatch, sql) -> None:
    browser.sign_in("github")
    [(expires,)] = sql("SELECT expires_at FROM sessions")

    same_day = browser.me()

    assert "set-cookie" not in same_day.headers
    _move_clock(monkeypatch, timedelta(days=20))
    later = browser.me()
    assert later.status_code == 200
    assert "Max-Age=2592000" in cookies_set(later)["session"]
    [(extended,)] = sql("SELECT expires_at FROM sessions")
    assert extended - expires > timedelta(days=19)
    # Still signed in 40 days after the sign-in, 20 after the last use.
    _move_clock(monkeypatch, timedelta(days=40))
    assert browser.me().status_code == 200


def test_a_new_sign_in_removes_the_users_expired_sessions(
    browser, other_browser, monkeypatch, sql
) -> None:
    other_browser.sign_in("github")
    _move_clock(monkeypatch, timedelta(days=31))

    browser.sign_in("github")

    assert len(sql("SELECT token_hash FROM sessions")) == 1


def test_a_session_cookie_of_no_session(browser) -> None:
    browser.set_cookie("session", "made-up-token")

    assert browser.me().status_code == 401


def test_sign_out_needs_the_apps_origin(browser) -> None:
    browser.sign_in("github")

    response = browser.post(
        "/api/v1/auth/sign-out", headers={"Origin": "https://ops.candlestack.tech"}
    )

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"
    assert browser.me().status_code == 200


def test_sign_out_ends_only_this_browsers_session(browser, other_browser) -> None:
    browser.sign_in("github")
    other_browser.sign_in("github")

    browser.sign_out()

    assert browser.me().status_code == 401
    assert other_browser.me().status_code == 200
    assert browser.sign_out().status_code == 204


def test_sign_in_rate_limit(browser, monkeypatch) -> None:
    monkeypatch.setattr(dependencies, "SIGN_IN_RATE_LIMIT", 2)

    starts = [browser.start("github").headers["location"] for _ in range(3)]

    assert [urlsplit(start).netloc for start in starts[:2]] == ["github.com", "github.com"]
    assert starts[2] == "/signin?error=failed"
    callback = browser.callback("github", code="the-code", state="s")
    assert callback.headers["location"] == "/signin?error=failed"
