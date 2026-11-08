"""Accounts: users, GitHub and Google sign-in, sessions and the beta list.

Other modules import only from here (``from candlestack.accounts import CurrentUserDep``),
never from the submodules. A route of the signed-in user takes ``user: CurrentUserDep``, and
one that changes something also depends on ``core.require_same_origin``.
``CurrentUserDep`` lets an API key read (GET) but change nothing, ``SessionUserDep`` lets no
key in at all; a route that a key with a scope may change takes ``BacktestsUserDep`` or
``ModelsUserDep`` instead (a POST that changes nothing ``ReaderDep``), or ``CallerDep`` and
checks the scopes itself (``Caller.require``). Such a route returns its data, not a
``Response`` of its own: FastAPI drops the renewed session cookie that ``CurrentUserDep`` sets
from a response the route builds itself.

A module that keeps data of a user deletes it with the account through an
``AccountDeletionHook``, which ``candlestack.main`` puts on ``app.state.account_deletion_hooks``:
its rows in the deletion's transaction, and what lies outside Postgres in the ``AfterDeletion``
it returns, once the deletion is committed.

What signs requests in outside FastAPI's routes (the MCP server) uses ``key_caller`` for the
API key, ``token_caller`` for an OAuth access token (``is_access_token``) and
``get_accounts_service`` for the service the routes use. The OAuth authorization server
(``oauth_router``) signs MCP clients in; its issuer is ``oauth_issuer``.
"""

from candlestack.accounts.api import dev_router as dev_sign_in_router
from candlestack.accounts.api import router as accounts_router
from candlestack.accounts.authorization import ACCESS_PREFIX
from candlestack.accounts.authorization import issuer as oauth_issuer
from candlestack.accounts.dependencies import (
    API_KEY_SCHEME,
    BacktestsUserDep,
    Caller,
    CallerDep,
    CurrentUserDep,
    ModelsUserDep,
    ReaderDep,
    SessionUserDep,
    count_request,
    get_accounts_service,
    key_caller,
    token_caller,
)
from candlestack.accounts.models import User
from candlestack.accounts.oauth_api import router as oauth_router
from candlestack.accounts.reference import document_api_keys
from candlestack.accounts.schemas import (
    BacktestDefaults,
    BacktestDefaultsIn,
    DateFormat,
    DeniedSignInOut,
    MeOut,
    Provider,
    Scope,
    TimeFormat,
    TimeZone,
)
from candlestack.accounts.service import (
    AccountDeletionHook,
    AccountsService,
    AfterDeletion,
    dev_sign_in_allowed,
)


def is_access_token(token: str) -> bool:
    """Whether `token` is an OAuth access token (``csa_...``), not an API key."""
    return token.startswith(ACCESS_PREFIX)


__all__ = [
    "API_KEY_SCHEME",
    "AccountDeletionHook",
    "AccountsService",
    "AfterDeletion",
    "BacktestDefaults",
    "BacktestDefaultsIn",
    "BacktestsUserDep",
    "Caller",
    "CallerDep",
    "CurrentUserDep",
    "DateFormat",
    "DeniedSignInOut",
    "MeOut",
    "ModelsUserDep",
    "Provider",
    "ReaderDep",
    "Scope",
    "SessionUserDep",
    "TimeFormat",
    "TimeZone",
    "User",
    "accounts_router",
    "count_request",
    "dev_sign_in_allowed",
    "dev_sign_in_router",
    "document_api_keys",
    "get_accounts_service",
    "is_access_token",
    "key_caller",
    "oauth_issuer",
    "oauth_router",
    "token_caller",
]
