"""Sentry: the backend's errors, traces, profiles and logs, sent only when ``SENTRY_DSN`` is set.

An error in Sentry is an unhandled exception (answered with a 500 by RequestContextMiddleware)
or a record logged at ERROR or above (``logger.error``, ``logger.exception``). Everything else a
request can end in, a problem response (4xx, or the 502/503 of a failing source) or a warning,
is not: records from INFO up go to Sentry's logs instead. Every event carries the environment
(``APP_ENV``), the release (``APP_VERSION``) and, inside a request, its ``request_id``.
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote_plus

import sentry_sdk
from fastapi import APIRouter
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.scrubber import DEFAULT_DENYLIST, DEFAULT_PII_DENYLIST, EventScrubber
from sentry_sdk.types import Breadcrumb, BreadcrumbHint, Event, Hint, Log

from candlestack.core.config import Settings
from candlestack.core.logging import request_id_var

# At most this many error events per hour from one process: an error repeated in a loop must
# not use up the month's quota (50,000 errors) within hours. Sentry's spike protection and the
# spike alert (50 events of one issue in an hour) come on top.
ERRORS_PER_HOUR = 100

# Values never sent, on top of Sentry's defaults (passwords, tokens, cookies, authorization):
# the Alpaca and logo.dev keys as settings, variables and request headers, the DSN, the database
# URL (it holds the password), the OAuth client secrets, codes, verifiers, states and tokens of
# a sign-in, and the page a sign-in returns to (``next``), which may be an upload page whose path
# holds an upload link's token (``/upload/<token>``).
_DENYLIST = [
    *DEFAULT_DENYLIST,
    "alpaca_key_id",
    "alpaca_secret_key",
    "apca-api-key-id",
    "apca-api-secret-key",
    "key_id",
    "secret_key",
    "logo_dev_key",
    "dsn",
    "sentry_dsn",
    "database_url",
    "postgres_password",
    "client_secret",
    "oauth_github_client_secret",
    "oauth_google_client_secret",
    "code",
    "code_verifier",
    "verifier",
    "state",
    "access_token",
    "refresh_token",
    "id_token",
    "next",
]
# The query of a request is recorded as one string, which the scrubber cannot see into: the
# httpx integration's ``http.query`` of every outgoing request in breadcrumbs and spans
# (logo.dev takes its key as ``token`` there), and the ASGI integration's ``query_string`` of
# the incoming request in events and transactions (a sign-in's ``code`` and ``state`` there).
_QUERY = "http.query"
_REQUEST_QUERY = "query_string"
_QUERY_DENYLIST = frozenset(name.lower() for name in _DENYLIST)
_FILTERED = "[Filtered]"
# Personal data never sent: the client's IP, the Cloudflare Access login of stage, and the
# users' emails and the beta list.
_PII_DENYLIST = [
    *DEFAULT_PII_DENYLIST,
    "email",
    "beta_allowlist",
    "cf-connecting-ip",
    "true-client-ip",
    "x-forwarded-for",
    "x-real-ip",
    "cf-access-authenticated-user-email",
    "cf-access-jwt-assertion",
]

_ACCESS_LOGGER = "candlestack.access"
_HEALTH_PATH = "/api/health"
# Paths whose last part is a secret: the token of a one-time upload link (ml), which is the
# request's only sign-in.
_REDACTED_PATHS = ("/api/v1/uploads/",)


def redacted_path(path: str) -> str:
    """`path` with the secret it ends in, if any, filtered: for the access log and Sentry."""
    for prefix in _REDACTED_PATHS:
        if path.startswith(prefix) and len(path) > len(prefix):
            return prefix + _FILTERED
    return path


def _redact_url(request: Any) -> None:
    """Filters the secret of the recorded ``url`` of the incoming request (``redacted_path``)."""
    if not isinstance(request, dict) or not isinstance(url := request.get("url"), str):
        return
    scheme, separator, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    if separator and slash:
        request["url"] = f"{scheme}://{host}{redacted_path('/' + path)}"


def _redact_paths(event: Event) -> None:
    """Filters the secret of the incoming request's recorded URL, and of the transaction's
    name when it is the path itself (no route matched; a route's template ends in ``}``)."""
    _redact_url(event.get("request"))
    name = event.get("transaction")
    if isinstance(name, str) and not name.endswith("}"):
        event["transaction"] = redacted_path(name)


class ErrorBudget:
    """Allows at most `limit` events per `window` seconds; the ones over it are dropped."""

    def __init__(
        self, limit: int, window: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window
        self._clock = clock
        self._lock = threading.Lock()
        self._start = clock()
        self._used = 0

    def take(self) -> bool:
        with self._lock:
            now = self._clock()
            if now - self._start >= self._window:
                self._start, self._used = now, 0
            if self._used >= self._limit:
                return False
            self._used += 1
            return True


def _traces_sampler(rate: float) -> Callable[[dict[str, Any]], float]:
    """Traces `rate` of the requests, and none of the health checks: ops and Sentry's uptime
    monitor call them every minute."""

    def sample(context: dict[str, Any]) -> float:
        if context.get("parent_sampled") is not None:
            return float(context["parent_sampled"])
        path = (context.get("asgi_scope") or {}).get("path", "")
        return 0.0 if path.startswith(_HEALTH_PATH) else rate

    return sample


def _drop_request_variables(event: Event) -> None:
    """Keeps the local variables of our own frames only, without the ASGI ``scope``: the
    frames of the framework and of RequestContextMiddleware hold the raw request headers
    (cookies, the client's IP, the Access login) as lists that the scrubber cannot read."""
    for exception in event.get("exception", {}).get("values", []):
        for frame in (exception.get("stacktrace") or {}).get("frames", []):
            if not frame.get("in_app"):
                frame.pop("vars", None)
            else:
                frame.get("vars", {}).pop("scope", None)


def _before_send(budget: ErrorBudget) -> Callable[[Event, Hint], Event | None]:
    def before_send(event: Event, _hint: Hint) -> Event | None:
        if not budget.take():
            return None
        _drop_request_variables(event)
        _hide_query_secrets(event.get("request"), _REQUEST_QUERY)
        _redact_paths(event)
        return event

    return before_send


def _hide_query_secrets(data: Any, key: str = _QUERY) -> None:
    """Replaces the values of the denylisted parameters (any case) in the recorded query
    ``data[key]``."""
    if not isinstance(data, dict) or not isinstance(query := data.get(key), str):
        return
    parts = []
    for part in query.split("&"):
        name, equals, _ = part.partition("=")
        secret = equals and unquote_plus(name).lower() in _QUERY_DENYLIST
        parts.append(f"{name}={_FILTERED}" if secret else part)
    data[key] = "&".join(parts)


def _before_breadcrumb(crumb: Breadcrumb, _hint: BreadcrumbHint) -> Breadcrumb | None:
    _hide_query_secrets(crumb.get("data"))
    return crumb


def _before_send_transaction(event: Event, _hint: Hint) -> Event | None:
    _hide_query_secrets(event.get("request"), _REQUEST_QUERY)
    _redact_paths(event)
    spans = event.get("spans")
    if isinstance(spans, list):
        for span in spans:
            _hide_query_secrets(span.get("data"))
    return event


def _before_send_log(log: Log, _hint: Hint) -> Log | None:
    """Drops the access lines of health checks and adds the request id to the other logs."""
    attributes = log["attributes"]
    if attributes.get("logger.name") == _ACCESS_LOGGER and str(
        attributes.get("path", "")
    ).startswith(_HEALTH_PATH):
        return None
    if "request_id" not in attributes and (request_id := request_id_var.get()) is not None:
        attributes["request_id"] = request_id
    return log


def setup_sentry(settings: Settings) -> bool:
    """Starts the Sentry SDK when ``SENTRY_DSN`` is set; returns whether it did.

    Call it before the app is built: the SDK hooks into FastAPI, httpx, Redis and logging.
    """
    if not settings.sentry_dsn:
        return False
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release=settings.app_version,
        send_default_pii=False,
        in_app_include=["candlestack"],
        event_scrubber=EventScrubber(
            denylist=_DENYLIST, pii_denylist=_PII_DENYLIST, recursive=True
        ),
        max_request_body_size="never",
        traces_sampler=_traces_sampler(settings.sentry_traces_sample_rate),
        # Where the CPU time of the traced requests goes, by function: the profiler samples the
        # stacks only while a traced request runs. The plan includes 750 profile hours a month.
        profile_session_sample_rate=1.0,
        profile_lifecycle="trace",
        enable_logs=True,
        before_send=_before_send(ErrorBudget(ERRORS_PER_HOUR, 3600)),
        before_send_transaction=_before_send_transaction,
        before_breadcrumb=_before_breadcrumb,
        before_send_log=_before_send_log,
        integrations=[
            LoggingIntegration(
                level=logging.INFO, event_level=logging.ERROR, sentry_logs_level=logging.INFO
            )
        ],
    )
    return True


def tag_request(request_id: str) -> None:
    """Tags the events of the current request with its id, the ``X-Request-ID`` a client sees."""
    sentry_sdk.get_isolation_scope().set_tag("request_id", request_id)


# Stage only, hidden from the API reference: lets the team check that an unhandled error
# reaches Sentry after a deploy.
check_router = APIRouter(include_in_schema=False)


@check_router.get("/api/debug/sentry-error")
async def sentry_error() -> None:
    raise RuntimeError("Deliberate error from /api/debug/sentry-error to check Sentry")
