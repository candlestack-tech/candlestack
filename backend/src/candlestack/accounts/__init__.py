"""Accounts: users, GitHub and Google sign-in, sessions and the beta list.

Other modules import only from here (``from candlestack.accounts import CurrentUserDep``),
never from the submodules. A route of the signed-in user takes ``user: CurrentUserDep``, and
one that changes something also depends on ``core.require_same_origin``.
``CurrentUserDep`` lets an API key read (GET) but change nothing; a route that a key with a
scope may change takes ``BacktestsUserDep`` or ``ModelsUserDep`` instead, or ``CallerDep``
and checks the scopes itself (``Caller.require``). Such a route returns
its data, not a ``Response`` of its own: FastAPI drops the renewed session cookie that
``CurrentUserDep`` sets from a response the route builds itself.

A module that keeps data of a user deletes it with the account through an
``AccountDeletionHook``, which ``candlestack.main`` puts on ``app.state.account_deletion_hooks``:
its rows in the deletion's transaction, and what lies outside Postgres in the ``AfterDeletion``
it returns, once the deletion is committed.
"""

from candlestack.accounts.api import dev_router as dev_sign_in_router
from candlestack.accounts.api import router as accounts_router
from candlestack.accounts.dependencies import (
    BacktestsUserDep,
    Caller,
    CallerDep,
    CurrentUserDep,
    ModelsUserDep,
)
from candlestack.accounts.models import User
from candlestack.accounts.reference import document_api_keys
from candlestack.accounts.schemas import DeniedSignInOut, MeOut, Provider, Scope
from candlestack.accounts.service import AccountDeletionHook, AfterDeletion, dev_sign_in_allowed

__all__ = [
    "AccountDeletionHook",
    "AfterDeletion",
    "BacktestsUserDep",
    "Caller",
    "CallerDep",
    "CurrentUserDep",
    "DeniedSignInOut",
    "MeOut",
    "ModelsUserDep",
    "Provider",
    "Scope",
    "User",
    "accounts_router",
    "dev_sign_in_allowed",
    "dev_sign_in_router",
    "document_api_keys",
]
