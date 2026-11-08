"""The pure parts of the OAuth authorization server (``accounts.authorization``): which scopes
a request asks for, a consent grants and a refresh keeps, Basic credentials and the redirect
back to a client. The flow over HTTP is an integration test
(tests/integration/accounts/test_oauth.py)."""

import base64

import pytest

from candlestack.accounts import Scope
from candlestack.accounts.authorization import (
    RESOURCE_PATH,
    _basic_credentials,
    _granted,
    _narrowed,
    _with_query,
    requested_scopes,
)
from candlestack.accounts.errors import OAuthError, ScopeNotRequested
from candlestack.mcp import MCP_PATH


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        (None, list(Scope)),
        ("", list(Scope)),
        ("openid offline_access", list(Scope)),
        ("publish read publish", [Scope.read, Scope.publish]),
    ],
)
def test_requested_scopes(scope: str | None, expected: list[Scope]) -> None:
    assert requested_scopes(scope) == expected


def test_a_consent_grants_read_always_and_nothing_not_asked_for() -> None:
    assert _granted([Scope.backtests], []) == [Scope.read]
    assert _granted([Scope.backtests], [Scope.backtests, Scope.read]) == [
        Scope.read,
        Scope.backtests,
    ]
    with pytest.raises(ScopeNotRequested):
        _granted([Scope.read], [Scope.models])


def test_a_refresh_narrows_but_never_widens() -> None:
    held = ["read", "backtests"]

    assert _narrowed(held, None) == [Scope.read, Scope.backtests]
    assert _narrowed(held, "backtests") == [Scope.read, Scope.backtests]
    assert _narrowed(held, "offline_access") == [Scope.read, Scope.backtests]
    assert _narrowed(held, "read") == [Scope.read]
    with pytest.raises(OAuthError, match="cannot add"):
        _narrowed(held, "publish")


def test_basic_credentials_are_form_decoded() -> None:
    value = base64.b64encode(b"ocl_abc:s%3Acret+x").decode()

    assert _basic_credentials(f"Basic {value}") == ("ocl_abc", "s:cret x")
    assert _basic_credentials(None) is None
    assert _basic_credentials("Bearer csa_x") is None


def test_the_redirect_back_keeps_the_client_s_own_query() -> None:
    assert _with_query("https://a.example/cb?x=1", {"code": "c", "state": None}) == (
        "https://a.example/cb?x=1&code=c"
    )
    assert _with_query("https://a.example/cb", {"error": "access_denied"}) == (
        "https://a.example/cb?error=access_denied"
    )


def test_tokens_are_bound_to_the_mcp_server_s_path() -> None:
    assert RESOURCE_PATH == MCP_PATH
