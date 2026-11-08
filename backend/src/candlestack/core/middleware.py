"""Request id, access log and last-resort error handling for every HTTP request."""

import logging
import secrets
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from candlestack.core.errors import internal_error_response
from candlestack.core.logging import request_id_var
from candlestack.core.observability import tag_request, without_secrets

access_logger = logging.getLogger("candlestack.access")
logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    """Tags each request with an id, logs one access line and turns a crash into a 500 problem.

    The id is Cloudflare's ``CF-Ray`` when present (so our logs match Cloudflare's), else random.
    It is sent back as ``X-Request-ID`` and added to every log record and Sentry event of the
    request. Every response also carries ``Server-Timing: app;dur=<ms>``, the time the app took
    until the response started, so it can be measured from outside. The access line leaves out
    the secret a path may hold (``without_secrets``: an upload link's token).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = Headers(scope=scope).get("cf-ray") or secrets.token_hex(8)
        token = request_id_var.set(request_id)
        tag_request(request_id)
        start = time.perf_counter()
        status = 500
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                status = message["status"]
                started = True
                headers = MutableHeaders(scope=message)
                headers["X-Request-ID"] = request_id
                headers["Server-Timing"] = f"app;dur={_elapsed_ms(start)}"
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            logger.exception("Unhandled error")
            if started:
                raise
            await internal_error_response()(scope, receive, send_with_id)
        finally:
            path = without_secrets(scope["path"])
            access_logger.info(
                "%s %s %s",
                scope["method"],
                path,
                status,
                extra={
                    "method": scope["method"],
                    "path": path,
                    "status": status,
                    "duration_ms": _elapsed_ms(start),
                    "request_id": request_id,
                },
            )
            request_id_var.reset(token)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)
