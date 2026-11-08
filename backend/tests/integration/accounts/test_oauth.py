"""The OAuth sign-in of MCP clients over HTTP, against a real Postgres and Redis (conftest.py):
registration, the authorize step, the consent page's requests, the token endpoint, refresh,
revocation, connected apps, and the access token at ``/mcp``, with every refusal."""

import base64
import hashlib
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import httpx2
import pytest
import respx
from fastapi.testclient import TestClient

from candlestack.accounts import authorization, client_metadata

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = "http://testserver/mcp"
ISSUER = "http://testserver/api/v1/oauth"
VERIFIER = "the-verifier-of-this-sign-in-" + "x" * 30
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).decode().rstrip("=")
)
DOCUMENT_URL = "https://app.example.com/oauth/client.json"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}


class McpClient:
    """The back end of an MCP client: no cookies, only its own requests."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def request(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
        self._client.cookies = httpx2.Cookies()
        return self._client.request(method, url, **kwargs)

    def register(self, **metadata: Any) -> dict[str, Any]:
        body = {
            "redirect_uris": [REDIRECT],
            "client_name": "Claude",
            "token_endpoint_auth_method": "none",
            **metadata,
        }
        response = self.request("POST", "/api/v1/oauth/register", json=body)
        assert response.status_code == 201, response.text
        return response.json()

    def token(self, **form: str) -> httpx2.Response:
        return self.request("POST", "/api/v1/oauth/token", data=form)

    def exchange(self, client_id: str, code: str, **form: str) -> httpx2.Response:
        return self.token(
            **{
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT,
                "code_verifier": VERIFIER,
                "client_id": client_id,
                "resource": RESOURCE,
                **form,
            }
        )

    def refresh(self, client_id: str, refresh_token: str, **form: str) -> httpx2.Response:
        return self.token(
            grant_type="refresh_token", refresh_token=refresh_token, client_id=client_id, **form
        )

    def mcp(self, access_token: str) -> httpx2.Response:
        return self.request(
            "POST",
            "/mcp",
            json=INITIALIZE,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
            },
        )


@pytest.fixture
def app_client(client: TestClient) -> McpClient:
    return McpClient(client)


def authorize(browser, client_id: str, **params: str | None) -> httpx2.Response:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT,
        "state": "the-state",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "scope": "read backtests models",
        "resource": RESOURCE,
        **params,
    }
    return browser.get(
        "/api/v1/oauth/authorize", params={k: v for k, v in query.items() if v is not None}
    )


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


def request_id(authorized: httpx2.Response) -> str:
    assert authorized.status_code == 302, authorized.text
    location = authorized.headers["location"]
    if location.startswith("/signin?"):  # signed out: the consent page after the sign-in
        location = query_of(location)["next"]
    assert location.startswith("/oauth/consent?")
    return query_of(location)["request"]


def decide(browser, rid: str, decision: str = "allow", scopes: list[str] | None = None):
    body = {"decision": decision, "scopes": ["read", "backtests"] if scopes is None else scopes}
    return browser.post(f"/api/v1/oauth/requests/{rid}", json=body, headers=ORIGIN)


def code_of(consent: httpx2.Response) -> str:
    assert consent.status_code == 200, consent.text
    redirect = consent.json()["redirect_to"]
    assert redirect.startswith(REDIRECT + "?")
    query = query_of(redirect)
    assert query["state"] == "the-state"
    assert query["iss"] == ISSUER
    return query["code"]


def connect(browser, app_client: McpClient, **register: Any) -> tuple[str, dict[str, Any]]:
    """Signs in, registers, authorizes and allows read and backtests: the client ID and the
    token answer."""
    if browser.cookie("session") is None:
        browser.sign_in("github")
    client_id = app_client.register(**register)["client_id"]
    code = code_of(decide(browser, request_id(authorize(browser, client_id))))
    tokens = app_client.exchange(client_id, code)
    assert tokens.status_code == 200, tokens.text
    return client_id, tokens.json()


def oauth_error(response: httpx2.Response, status: int, error: str) -> None:
    assert response.status_code == status, response.text
    assert response.json()["error"] == error
    assert response.headers["cache-control"] == "no-store"


def problem(response: httpx2.Response, status: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["type"] == PROBLEMS + slug
    return body


# --- The whole flow -----------------------------------------------------------------------------


def test_the_whole_flow_signs_an_mcp_client_in(browser, app_client, sql) -> None:
    client_id, tokens = connect(browser, app_client)

    assert tokens["token_type"] == "Bearer"
    assert tokens["expires_in"] == 3600
    assert tokens["scope"] == "read backtests"
    assert tokens["access_token"].startswith("csa_")
    assert tokens["refresh_token"].startswith("csr_")
    rows = sql("SELECT kind, scopes, resource, token_hash FROM oauth_tokens ORDER BY kind")
    assert [(kind, scopes, resource) for kind, scopes, resource, _ in rows] == [
        ("access", ["read", "backtests"], RESOURCE),
        ("refresh", ["read", "backtests"], RESOURCE),
    ]
    assert bytes(rows[0][3]) == hashlib.sha256(tokens["access_token"].encode()).digest()
    assert app_client.mcp(tokens["access_token"]).status_code == 200
    [(last_used,)] = sql("SELECT last_used_at FROM connected_apps")
    assert last_used is not None
    listed = browser.get("/api/v1/me/connected-apps")
    assert listed.headers["cache-control"] == "no-store"
    [app] = listed.json()["items"]
    assert app["client"] == {
        "name": "Claude",
        "logo_uri": None,
        "client_uri": None,
        "redirect_host": "claude.ai",
    }
    assert app["scopes"] == ["read", "backtests"]
    assert app["last_used_at"] is not None
    assert app["id"].startswith("app_")
    assert client_id.startswith("ocl_")


def test_the_mcp_metadata_names_the_authorization_server(app_client) -> None:
    resource = app_client.request("GET", "/.well-known/oauth-protected-resource/mcp").json()
    assert resource["authorization_servers"] == [ISSUER]

    for path in (
        "/.well-known/oauth-authorization-server/api/v1/oauth",
        "/api/v1/oauth/.well-known/oauth-authorization-server",
    ):
        metadata = app_client.request("GET", path).json()
        assert metadata["issuer"] == ISSUER
        assert metadata["authorization_endpoint"] == f"{ISSUER}/authorize"
        assert metadata["token_endpoint"] == f"{ISSUER}/token"
        assert metadata["registration_endpoint"] == f"{ISSUER}/register"
        assert metadata["revocation_endpoint"] == f"{ISSUER}/revoke"
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        assert metadata["client_id_metadata_document_supported"] is True


# --- Registration -------------------------------------------------------------------------------


def test_a_confidential_client_gets_a_secret_and_must_use_it(browser, app_client, sql) -> None:
    browser.sign_in("github")
    registered = app_client.register(token_endpoint_auth_method="client_secret_basic")
    secret = registered["client_secret"]
    assert registered["client_secret_expires_at"] == 0
    [(stored,)] = sql("SELECT secret_hash FROM oauth_clients")
    assert bytes(stored) == hashlib.sha256(secret.encode()).digest()
    client_id = registered["client_id"]
    code = code_of(decide(browser, request_id(authorize(browser, client_id))))

    without = app_client.exchange(client_id, code)
    oauth_error(without, 401, "invalid_client")

    code = code_of(decide(browser, request_id(authorize(browser, client_id))))
    basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    tokens = app_client.request(
        "POST",
        "/api/v1/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
        },
        headers={"Authorization": f"Basic {basic}"},
    )
    assert tokens.status_code == 200, tokens.text


def test_client_secret_post_works_and_a_wrong_secret_does_not(browser, app_client) -> None:
    browser.sign_in("github")
    registered = app_client.register(token_endpoint_auth_method="client_secret_post")
    client_id = registered["client_id"]
    code = code_of(decide(browser, request_id(authorize(browser, client_id))))

    wrong = app_client.exchange(client_id, code, client_secret="nope")
    oauth_error(wrong, 401, "invalid_client")

    code = code_of(decide(browser, request_id(authorize(browser, client_id))))
    right = app_client.exchange(client_id, code, client_secret=registered["client_secret"])
    assert right.status_code == 200


@pytest.mark.parametrize(
    ("metadata", "error"),
    [
        ({"redirect_uris": ["http://claude.ai/callback"]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["https://claude.ai/cb#frag"]}, "invalid_redirect_uri"),
        ({"redirect_uris": []}, "invalid_client_metadata"),
        ({"grant_types": ["client_credentials"]}, "invalid_client_metadata"),
        ({"response_types": ["token"]}, "invalid_client_metadata"),
        ({"token_endpoint_auth_method": "private_key_jwt"}, "invalid_client_metadata"),
        ({"logo_uri": "http://claude.ai/logo.png"}, "invalid_client_metadata"),
    ],
)
def test_registration_refuses_bad_metadata(app_client, metadata, error) -> None:
    body = {"redirect_uris": [REDIRECT], **metadata}

    response = app_client.request("POST", "/api/v1/oauth/register", json=body)

    oauth_error(response, 400, error)


def test_registration_needs_a_json_body(app_client) -> None:
    response = app_client.request(
        "POST", "/api/v1/oauth/register", content=b"nope", headers={"Content-Type": "text/plain"}
    )

    oauth_error(response, 400, "invalid_client_metadata")


def test_a_native_client_may_return_to_any_loopback_port(browser, app_client) -> None:
    browser.sign_in("github")
    client_id = app_client.register(redirect_uris=["http://127.0.0.1:3118/callback"])["client_id"]

    other_port = authorize(browser, client_id, redirect_uri="http://127.0.0.1:49152/callback")
    other_path = authorize(browser, client_id, redirect_uri="http://127.0.0.1:49152/other")

    assert other_port.headers["location"].startswith("/oauth/consent?")
    problem(other_path, 400, "invalid-authorization-request")


# --- Authorize ----------------------------------------------------------------------------------


def test_signed_out_the_browser_signs_in_first_and_comes_back(browser, app_client) -> None:
    client_id = app_client.register()["client_id"]

    authorized = authorize(browser, client_id)

    assert authorized.status_code == 302
    location = authorized.headers["location"]
    assert location.startswith("/signin?")
    back = query_of(location)["next"]
    assert back.startswith("/oauth/consent?request=")
    # The normal sign-in, and back to the consent page.
    signed_in = browser.sign_in("github", next_path=back)
    assert signed_in.headers["location"] == back
    rid = query_of(back)["request"]
    assert decide(browser, rid).status_code == 200


@pytest.mark.parametrize(
    "params",
    [
        {"client_id": "ocl_unknownunknown00"},
        {"client_id": None},
        {"redirect_uri": "https://evil.example/callback"},
        {"redirect_uri": None},
    ],
)
def test_an_unknown_client_or_redirect_uri_is_not_followed(browser, app_client, params) -> None:
    client_id = app_client.register()["client_id"]

    response = authorize(browser, params.pop("client_id", client_id), **params)

    problem(response, 400, "invalid-authorization-request")
    assert "location" not in response.headers


@pytest.mark.parametrize(
    ("params", "error"),
    [
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge": "too-short"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"state": "s" * 2000}, "invalid_request"),
    ],
)
def test_a_bad_request_goes_back_to_the_client_with_its_error(
    browser, app_client, params, error
) -> None:
    client_id = app_client.register()["client_id"]

    response = authorize(browser, client_id, **params)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith(REDIRECT + "?")
    query = query_of(location)
    assert query["error"] == error
    assert query["iss"] == ISSUER
    assert query.get("state") == (None if "state" in params else "the-state")


def test_the_consent_page_reads_the_request_without_a_session(
    browser, app_client, other_browser
) -> None:
    client_id = app_client.register(
        client_uri="https://claude.ai", logo_uri="https://claude.ai/logo.png"
    )["client_id"]
    rid = request_id(authorize(browser, client_id, scope="publish offline_access", state=None))

    described = other_browser.get(f"/api/v1/oauth/requests/{rid}")

    assert described.status_code == 200
    assert described.headers["cache-control"] == "no-store"
    body = described.json()
    assert body["client"] == {
        "name": "Claude",
        "logo_uri": "https://claude.ai/logo.png",
        "client_uri": "https://claude.ai",
        "redirect_host": "claude.ai",
    }
    assert body["scopes"] == [
        {"scope": "publish", "description": "Publish and unpublish backtests in Experiments"}
    ]


def test_no_scope_asks_for_all_of_them(browser, app_client) -> None:
    client_id = app_client.register()["client_id"]
    rid = request_id(authorize(browser, client_id, scope=None, resource=None))

    body = browser.get(f"/api/v1/oauth/requests/{rid}").json()

    assert [item["scope"] for item in body["scopes"]] == ["read", "backtests", "models", "publish"]


def test_an_unknown_request_is_not_found(browser) -> None:
    browser.sign_in("github")

    problem(browser.get("/api/v1/oauth/requests/nope"), 404, "authorization-request-not-found")
    problem(decide(browser, "nope"), 404, "authorization-request-not-found")


# --- The decision -------------------------------------------------------------------------------


def test_deny_sends_access_denied_back(browser, app_client, sql) -> None:
    browser.sign_in("github")
    client_id = app_client.register()["client_id"]
    rid = request_id(authorize(browser, client_id))

    denied = decide(browser, rid, "deny")

    query = query_of(denied.json()["redirect_to"])
    assert query["error"] == "access_denied"
    assert query["state"] == "the-state"
    assert "code" not in query
    assert sql("SELECT id FROM connected_apps") == []
    problem(decide(browser, rid), 404, "authorization-request-not-found")


def test_a_request_is_decided_once(browser, app_client) -> None:
    browser.sign_in("github")
    client_id = app_client.register()["client_id"]
    rid = request_id(authorize(browser, client_id))

    assert decide(browser, rid).status_code == 200

    problem(decide(browser, rid), 404, "authorization-request-not-found")


def test_the_user_narrows_the_scopes_but_cannot_widen_them(browser, app_client) -> None:
    browser.sign_in("github")
    client_id = app_client.register()["client_id"]
    rid = request_id(authorize(browser, client_id, scope="read backtests"))

    widened = decide(browser, rid, scopes=["read", "publish"])
    narrowed = decide(browser, rid, scopes=[])

    assert problem(widened, 422, "scope-not-requested")["scope"] == "publish"
    tokens = app_client.exchange(client_id, code_of(narrowed)).json()
    assert tokens["scope"] == "read"


def test_the_decision_needs_the_session_and_the_app_s_own_origin(
    browser, app_client, other_browser
) -> None:
    browser.sign_in("github")
    client_id = app_client.register()["client_id"]
    rid = request_id(authorize(browser, client_id))

    signed_out = decide(other_browser, rid)
    cross_site = browser.post(
        f"/api/v1/oauth/requests/{rid}",
        json={"decision": "allow"},
        headers={"Origin": "https://evil.example"},
    )

    problem(signed_out, 401, "not-signed-in")
    problem(cross_site, 403, "cross-origin-request")
    assert decide(browser, rid).status_code == 200


def test_someone_off_the_beta_list_cannot_allow(account, app_client, browser) -> None:
    bob = account("usr_bobbbbbbbb")
    client_id = app_client.register()["client_id"]
    rid = request_id(authorize(browser, client_id))

    response = bob.request("POST", f"/api/v1/oauth/requests/{rid}", json={"decision": "allow"})

    problem(response, 403, "not-on-beta-list")


# --- The code -----------------------------------------------------------------------------------


def _code(browser, app_client) -> tuple[str, str]:
    browser.sign_in("github")
    client_id = app_client.register()["client_id"]
    return client_id, code_of(decide(browser, request_id(authorize(browser, client_id))))


@pytest.mark.parametrize(
    ("form", "error"),
    [
        ({"code_verifier": "wrong-verifier-" + "y" * 40}, "invalid_grant"),
        ({"code_verifier": ""}, "invalid_grant"),
        ({"redirect_uri": "https://claude.ai/other"}, "invalid_grant"),
        ({"resource": "https://other.example/mcp"}, "invalid_target"),
        ({"code": "unknown"}, "invalid_grant"),
        ({"code": ""}, "invalid_request"),
        ({"grant_type": "password"}, "unsupported_grant_type"),
        ({"client_id": "ocl_unknownunknown00"}, "invalid_client"),
    ],
)
def test_the_token_endpoint_refuses_a_bad_exchange(browser, app_client, form, error) -> None:
    client_id, code = _code(browser, app_client)
    form = dict(form)

    response = app_client.exchange(form.pop("client_id", client_id), form.pop("code", code), **form)

    oauth_error(response, 401 if error == "invalid_client" else 400, error)


def test_a_code_of_another_client_is_refused(browser, app_client) -> None:
    _client_id, code = _code(browser, app_client)
    other = app_client.register()["client_id"]

    oauth_error(app_client.exchange(other, code), 400, "invalid_grant")


def test_a_code_reused_revokes_what_it_gave(browser, app_client) -> None:
    client_id, code = _code(browser, app_client)
    tokens = app_client.exchange(client_id, code).json()

    again = app_client.exchange(client_id, code)

    oauth_error(again, 400, "invalid_grant")
    problem(app_client.mcp(tokens["access_token"]), 401, "invalid-access-token")
    oauth_error(app_client.refresh(client_id, tokens["refresh_token"]), 400, "invalid_grant")


def test_an_expired_code_is_refused(browser, app_client, monkeypatch) -> None:
    client_id, code = _code(browser, app_client)
    later = authorization._now() + authorization.CODE_LIFETIME + timedelta(seconds=1)
    monkeypatch.setattr(authorization, "_now", lambda: later)

    oauth_error(app_client.exchange(client_id, code), 400, "invalid_grant")


def test_a_code_of_someone_who_left_the_beta_list_is_refused(browser, app_client, sql) -> None:
    client_id, code = _code(browser, app_client)
    sql("UPDATE users SET email = 'gone@example.com'")

    oauth_error(app_client.exchange(client_id, code), 400, "invalid_grant")


def test_the_token_endpoint_wants_a_form(app_client) -> None:
    as_json = app_client.request("POST", "/api/v1/oauth/token", json={"grant_type": "x"})
    repeated = app_client.request(
        "POST",
        "/api/v1/oauth/token",
        content=b"grant_type=a&grant_type=b",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    too_large = app_client.request(
        "POST",
        "/api/v1/oauth/token",
        content=b"grant_type=" + b"a" * 20000,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    for response in (as_json, repeated, too_large):
        oauth_error(response, 400, "invalid_request")


def test_malformed_basic_credentials_are_an_invalid_client(app_client) -> None:
    for header in ("Basic !!!", "Basic " + base64.b64encode(b"no-colon").decode()):
        response = app_client.request(
            "POST",
            "/api/v1/oauth/token",
            data={"grant_type": "authorization_code"},
            headers={"Authorization": header},
        )
        oauth_error(response, 401, "invalid_client")
        assert response.headers["www-authenticate"] == 'Basic realm="CandleStack"'


# --- Refresh ------------------------------------------------------------------------------------


def test_a_refresh_rotates_and_a_reused_one_ends_the_family(browser, app_client) -> None:
    client_id, first = connect(browser, app_client)

    second = app_client.refresh(client_id, first["refresh_token"], resource=RESOURCE)

    assert second.status_code == 200, second.text
    second = second.json()
    assert second["refresh_token"] != first["refresh_token"]
    assert second["scope"] == "read backtests"
    assert app_client.mcp(second["access_token"]).status_code == 200
    # The used token again: someone has a copy, so the whole family ends.
    reused = app_client.refresh(client_id, first["refresh_token"])
    oauth_error(reused, 400, "invalid_grant")
    problem(app_client.mcp(second["access_token"]), 401, "invalid-access-token")
    oauth_error(app_client.refresh(client_id, second["refresh_token"]), 400, "invalid_grant")


def test_a_refresh_may_narrow_the_scopes_but_not_widen_them(browser, app_client) -> None:
    client_id, tokens = connect(browser, app_client)

    widened = app_client.refresh(client_id, tokens["refresh_token"], scope="read publish")
    narrowed = app_client.refresh(client_id, tokens["refresh_token"], scope="read offline_access")

    oauth_error(widened, 400, "invalid_scope")
    assert narrowed.json()["scope"] == "read"


@pytest.mark.parametrize(
    "form",
    [
        {"refresh_token": "csr_unknown"},
        {"refresh_token": ""},
        {"resource": "https://other.example/mcp"},
    ],
)
def test_a_bad_refresh_is_refused(browser, app_client, form) -> None:
    client_id, tokens = connect(browser, app_client)

    response = app_client.refresh(
        client_id, form.pop("refresh_token", tokens["refresh_token"]), **form
    )

    assert response.status_code == 400
    assert response.json()["error"] in {"invalid_grant", "invalid_request", "invalid_target"}


def test_a_refresh_of_another_client_is_refused(browser, app_client) -> None:
    _client_id, tokens = connect(browser, app_client)
    other = app_client.register()["client_id"]

    oauth_error(app_client.refresh(other, tokens["refresh_token"]), 400, "invalid_grant")


def test_an_expired_refresh_token_is_refused(browser, app_client, monkeypatch) -> None:
    client_id, tokens = connect(browser, app_client)
    later = authorization._now() + authorization.REFRESH_LIFETIME
    monkeypatch.setattr(authorization, "_now", lambda: later)

    oauth_error(app_client.refresh(client_id, tokens["refresh_token"]), 400, "invalid_grant")


# --- The access token at /mcp -------------------------------------------------------------------


def test_an_expired_access_token_is_refused(browser, app_client, monkeypatch) -> None:
    _client_id, tokens = connect(browser, app_client)
    later = authorization._now() + authorization.ACCESS_LIFETIME
    monkeypatch.setattr(authorization, "_now", lambda: later)

    response = app_client.mcp(tokens["access_token"])

    problem(response, 401, "invalid-access-token")
    assert 'error="invalid_token"' in response.headers["www-authenticate"]
    assert "resource_metadata=" in response.headers["www-authenticate"]


def test_an_access_token_of_someone_off_the_beta_list_is_refused(browser, app_client, sql) -> None:
    client_id, tokens = connect(browser, app_client)
    sql("UPDATE users SET email = 'gone@example.com'")

    problem(app_client.mcp(tokens["access_token"]), 401, "invalid-access-token")
    oauth_error(app_client.refresh(client_id, tokens["refresh_token"]), 400, "invalid_grant")


def test_an_access_token_for_another_resource_is_refused(browser, app_client, sql) -> None:
    _client_id, tokens = connect(browser, app_client)
    sql("UPDATE oauth_tokens SET resource = 'https://other.example/mcp'")

    problem(app_client.mcp(tokens["access_token"]), 401, "invalid-access-token")


def test_an_access_token_does_not_sign_in_to_the_api(browser, app_client) -> None:
    _client_id, tokens = connect(browser, app_client)

    response = app_client.request(
        "GET", "/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )

    problem(response, 401, "invalid-api-key")


# --- Revocation and connected apps --------------------------------------------------------------


def test_revoking_a_refresh_token_ends_its_family(browser, app_client) -> None:
    client_id, tokens = connect(browser, app_client)

    revoked = app_client.request(
        "POST",
        "/api/v1/oauth/revoke",
        data={"token": tokens["refresh_token"], "client_id": client_id},
    )

    assert revoked.status_code == 200
    problem(app_client.mcp(tokens["access_token"]), 401, "invalid-access-token")


def test_revoking_an_access_token_ends_it_alone(browser, app_client) -> None:
    client_id, tokens = connect(browser, app_client)

    revoked = app_client.request(
        "POST",
        "/api/v1/oauth/revoke",
        data={"token": tokens["access_token"], "client_id": client_id},
    )

    assert revoked.status_code == 200
    problem(app_client.mcp(tokens["access_token"]), 401, "invalid-access-token")
    assert app_client.refresh(client_id, tokens["refresh_token"]).status_code == 200


def test_revoking_another_client_s_or_an_unknown_token_changes_nothing(browser, app_client) -> None:
    _client_id, tokens = connect(browser, app_client)
    other = app_client.register()["client_id"]

    for token in (tokens["access_token"], "csr_unknown"):
        response = app_client.request(
            "POST", "/api/v1/oauth/revoke", data={"token": token, "client_id": other}
        )
        assert response.status_code == 200
    assert app_client.mcp(tokens["access_token"]).status_code == 200
    missing = app_client.request("POST", "/api/v1/oauth/revoke", data={"client_id": other})
    oauth_error(missing, 400, "invalid_request")
    unknown = app_client.request("POST", "/api/v1/oauth/revoke", data={"token": "x"})
    oauth_error(unknown, 401, "invalid_client")


def test_disconnecting_an_app_revokes_its_tokens(browser, app_client, sql) -> None:
    client_id, tokens = connect(browser, app_client)
    [app] = browser.get("/api/v1/me/connected-apps").json()["items"]

    disconnected = browser.delete(f"/api/v1/me/connected-apps/{app['id']}", headers=ORIGIN)

    assert disconnected.status_code == 204
    assert browser.get("/api/v1/me/connected-apps").json() == {"items": []}
    assert sql("SELECT kind FROM oauth_tokens") == []
    problem(app_client.mcp(tokens["access_token"]), 401, "invalid-access-token")
    oauth_error(app_client.refresh(client_id, tokens["refresh_token"]), 400, "invalid_grant")
    again = browser.delete(f"/api/v1/me/connected-apps/{app['id']}", headers=ORIGIN)
    problem(again, 404, "not-found")


def test_connected_apps_need_the_session(browser, app_client) -> None:
    _client_id, tokens = connect(browser, app_client)
    key = browser.post(
        "/api/v1/me/api-keys", json={"name": "k", "scopes": ["read"]}, headers=ORIGIN
    ).json()["secret"]

    with_key = app_client.request(
        "GET", "/api/v1/me/connected-apps", headers={"Authorization": f"Bearer {key}"}
    )
    with_token = app_client.request(
        "GET",
        "/api/v1/me/connected-apps",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    problem(with_key, 403, "session-required")
    problem(with_token, 403, "session-required")


def test_authorizing_again_replaces_the_earlier_tokens(browser, app_client, sql) -> None:
    client_id, first = connect(browser, app_client)
    code = code_of(decide(browser, request_id(authorize(browser, client_id)), scopes=["models"]))

    second = app_client.exchange(client_id, code).json()

    assert second["scope"] == "read models"
    problem(app_client.mcp(first["access_token"]), 401, "invalid-access-token")
    assert app_client.mcp(second["access_token"]).status_code == 200
    assert sql("SELECT scopes FROM connected_apps") == [(["read", "models"],)]


def test_deleting_the_account_deletes_its_apps(browser, app_client, sql) -> None:
    connect(browser, app_client)

    assert browser.delete("/api/v1/me", headers=ORIGIN).status_code == 204

    assert sql("SELECT id FROM connected_apps") == []
    assert sql("SELECT kind FROM oauth_tokens") == []


# --- Client ID metadata documents ---------------------------------------------------------------


@pytest.fixture
def documents(monkeypatch):
    """The metadata documents' host resolves to a public address, served by respx."""

    async def resolve(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(client_metadata, "_resolve", resolve)
    with respx.mock(assert_all_called=False) as router:
        yield router


def document(**overrides: Any) -> dict[str, Any]:
    return {
        "client_id": DOCUMENT_URL,
        "client_name": "Example app",
        "redirect_uris": [REDIRECT],
        "logo_uri": "https://app.example.com/logo.png",
        **overrides,
    }


def test_a_client_named_by_its_metadata_document_signs_in(
    browser, app_client, documents, sql
) -> None:
    route = documents.get("https://93.184.216.34/oauth/client.json").respond(200, json=document())
    browser.sign_in("github")

    rid = request_id(authorize(browser, DOCUMENT_URL))
    again = request_id(authorize(browser, DOCUMENT_URL))

    assert route.call_count == 1  # kept for an hour
    request = route.calls[0].request
    assert request.headers["host"] == "app.example.com"
    assert request.extensions["sni_hostname"] == "app.example.com"
    body = browser.get(f"/api/v1/oauth/requests/{rid}").json()
    assert body["client"]["name"] == "Example app"
    assert again != rid
    tokens = app_client.exchange(DOCUMENT_URL, code_of(decide(browser, rid)))
    assert tokens.status_code == 200, tokens.text
    assert sql("SELECT metadata_document, secret_hash FROM oauth_clients") == [(True, None)]


def test_a_metadata_document_is_fetched_again_after_an_hour(
    browser, app_client, documents, monkeypatch
) -> None:
    route = documents.get("https://93.184.216.34/oauth/client.json")
    route.respond(200, json=document())
    request_id(authorize(browser, DOCUMENT_URL))
    route.respond(200, json=document(redirect_uris=["https://app.example.com/new"]))
    later = authorization._now() + authorization.DOCUMENT_TTL
    monkeypatch.setattr(authorization, "_now", lambda: later)

    stale = authorize(browser, DOCUMENT_URL)

    assert route.call_count == 2
    problem(stale, 400, "invalid-authorization-request")


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(404),
        httpx.Response(302, headers={"Location": "https://evil.example/doc.json"}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=document(client_id="https://app.example.com/other.json")),
        httpx.Response(200, json=document(client_secret="s")),
        httpx.Response(200, json=document(token_endpoint_auth_method="client_secret_basic")),
        httpx.Response(200, json=document(redirect_uris=["http://app.example.com/cb"])),
        httpx.Response(200, content=b"{" + b" " * 20000 + b"}"),
    ],
)
def test_a_bad_metadata_document_is_refused(browser, documents, answer) -> None:
    documents.get("https://93.184.216.34/oauth/client.json").mock(return_value=answer)

    response = authorize(browser, DOCUMENT_URL)

    problem(response, 400, "invalid-authorization-request")


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.8", "169.254.169.254", "::1", "::ffff:127.0.0.1", "fd00::1"]
)
def test_a_metadata_document_on_a_private_address_is_never_fetched(
    browser, documents, monkeypatch, address
) -> None:
    async def resolve(_host: str, _port: int) -> list[str]:
        return ["93.184.216.34", address]

    monkeypatch.setattr(client_metadata, "_resolve", resolve)
    route = documents.route()

    response = authorize(browser, DOCUMENT_URL)

    assert "public internet" in problem(response, 400, "invalid-authorization-request")["detail"]
    assert route.call_count == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://app.example.com/",
        "https://app.example.com/a/../b.json",
        "https://user@app.example.com/client.json",
        "https://app.example.com:99999/client.json",
    ],
)
def test_a_metadata_document_url_must_be_plain_https_with_a_path(browser, documents, url) -> None:
    route = documents.route()

    response = authorize(browser, url)

    assert "https URL" in problem(response, 400, "invalid-authorization-request")["detail"]
    assert route.call_count == 0


def test_an_unreachable_metadata_document_is_refused(browser, documents) -> None:
    documents.get("https://93.184.216.34/oauth/client.json").mock(
        side_effect=httpx.ConnectError("refused")
    )

    problem(authorize(browser, DOCUMENT_URL), 400, "invalid-authorization-request")
