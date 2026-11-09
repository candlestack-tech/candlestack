"""Shared foundations of every module: settings, logging, errors, Redis, Postgres, outgoing
HTTP, rate limits, CSRF protection, health, Sentry.

Other modules import only from here (``from candlestack.core import ProblemError``), never
from the submodules.
"""

from candlestack.core.config import Settings, SettingsDep, get_settings
from candlestack.core.csrf import bearer_token, is_same_origin, require_same_origin
from candlestack.core.db import DATABASE_ERRORS, Base, Database, DbSessionDep
from candlestack.core.errors import (
    NO_STORE,
    PROBLEM_TYPE_BASE,
    Problem,
    ProblemError,
    document_problems,
    install_error_handlers,
    problem_doc,
    problem_responses,
)
from candlestack.core.health import keep_runner_alive
from candlestack.core.health import router as health_router
from candlestack.core.http import (
    HttpClientDep,
    create_http_client,
    request_with_retry,
    retry_after,
)
from candlestack.core.logging import setup_logging
from candlestack.core.middleware import RequestContextMiddleware
from candlestack.core.observability import check_router as sentry_check_router
from candlestack.core.observability import setup_sentry
from candlestack.core.ratelimit import RateLimiter, RateLimiterDep, client_address
from candlestack.core.redis import RedisDep, create_redis

__all__ = [
    "DATABASE_ERRORS",
    "NO_STORE",
    "PROBLEM_TYPE_BASE",
    "Base",
    "Database",
    "DbSessionDep",
    "HttpClientDep",
    "Problem",
    "ProblemError",
    "RateLimiter",
    "RateLimiterDep",
    "RedisDep",
    "RequestContextMiddleware",
    "Settings",
    "SettingsDep",
    "bearer_token",
    "client_address",
    "create_http_client",
    "create_redis",
    "document_problems",
    "get_settings",
    "health_router",
    "install_error_handlers",
    "is_same_origin",
    "keep_runner_alive",
    "problem_doc",
    "problem_responses",
    "request_with_retry",
    "require_same_origin",
    "retry_after",
    "sentry_check_router",
    "setup_logging",
    "setup_sentry",
]
