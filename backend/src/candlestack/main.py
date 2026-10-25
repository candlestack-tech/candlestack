"""The CandleStack API app, served by granian: ``candlestack.main:app``."""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager, suppress
from importlib.resources import files
from typing import Any, override

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from scalar_fastapi import AgentScalarConfig, get_scalar_api_reference

from candlestack.accounts import (
    AccountDeletionHook,
    accounts_router,
    dev_sign_in_allowed,
    dev_sign_in_router,
)
from candlestack.core import (
    Database,
    RateLimiter,
    RequestContextMiddleware,
    Settings,
    create_http_client,
    create_redis,
    document_problems,
    get_settings,
    health_router,
    install_error_handlers,
    sentry_check_router,
    setup_logging,
    setup_sentry,
)
from candlestack.data import DataService, build_data_service, data_router
from candlestack.experiments import BacktestRunner, backtests_router, model_usage
from candlestack.jobs import JobClient, JobFiles, JobQueue
from candlestack.ml import (
    LocalModelStorage,
    ModelsAccountDeletion,
    ModelStorage,
    keep_incoming_swept,
    models_router,
    preprocessing_router,
)

logger = logging.getLogger(__name__)

TITLE = "CandleStack API"
# The icon is served by the frontend on the same host, like the rest of the site.
ICON_URL = "/favicon.svg"
DESCRIPTION = (
    f'<img src="{ICON_URL}" alt="CandleStack" width="40" height="40">\n\n'
    "HTTP API of CandleStack, a lab for experiments on financial time series. "
    "Market data covers crypto from Binance and US stocks from Alpaca. "
    "Times in responses are UTC epoch seconds; requests also accept ISO 8601. "
    "Errors are RFC 9457 problem details (`application/problem+json`).\n\n"
    "[Website](https://candlestack.tech) · "
    "[Source code](https://github.com/candlestack-tech/candlestack) · "
    "[Status](https://ops.candlestack.tech)"
)
OPENAPI_URL = "/api/v1/openapi.json"
DOCS_URL = "/api/v1/docs"
# A relative server makes the code examples use the host the docs are opened on.
SERVERS = [{"url": "/", "description": "This environment"}]
# Pinned: the theme styles Scalar's CSS variables, so upgrade it on purpose (not via Dependabot).
SCALAR_JS_URL = "https://cdn.jsdelivr.net/npm/@scalar/api-reference@1.72.1"
# The CandleStack theme: the colours and fonts of the website and the ops page.
DOCS_CSS = (files("candlestack") / "docs_theme.css").read_text(encoding="utf-8")
# The only environment with /api/debug/sentry-error, which fails on purpose to check Sentry:
# stage, which is team-only (Cloudflare Access).
SENTRY_CHECK_ENV = "stage"


class CandleStackAPI(FastAPI):
    @override
    def openapi(self) -> dict[str, Any]:
        if self.openapi_schema is None:
            self.openapi_schema = document_problems(super().openapi())
        return self.openapi_schema


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Opens the shared clients on ``app.state`` (``redis``, ``db``, ``http``) and builds what
    uses them: the client ``rate_limiter`` and the market ``data_service``. With
    ``STATS_REFRESH`` (prod and stage) the instrument stats are refreshed in a background task
    while the app runs. The model storage is made ready, and what crashed uploads left there
    is removed now and every 10 minutes. The ``backtest_runner`` runs the backtests users start;
    those a stopped process left running fail now. On shutdown the runs stop (failed), then the
    data service's lookups in the background, before the clients close."""
    settings: Settings = app.state.settings
    model_storage: ModelStorage = app.state.model_storage
    await model_storage.prepare()
    app.state.redis = create_redis(settings.redis_url)
    # The queue of the worker that runs the models, for the backtests.
    jobs_redis = create_redis(settings.jobs_redis_url)
    app.state.db = Database(settings.database_url.get_secret_value())
    app.state.rate_limiter = RateLimiter(app.state.redis)
    try:
        async with create_http_client(settings) as http:
            app.state.http = http
            data_service = build_data_service(settings, app.state.redis, http)
            app.state.data_service = data_service
            runner = BacktestRunner(
                app.state.db,
                data_service,
                JobClient(JobQueue(jobs_redis), JobFiles(settings.job_files_dir)),
            )
            app.state.backtest_runner = runner
            await runner.recover()
            async with (
                _background(data_service, stats=settings.stats_refresh),
                _running(keep_incoming_swept(model_storage), "The sweep of incoming model files"),
            ):
                try:
                    yield
                finally:
                    # The runs first: they use the data service and Postgres to the end.
                    await runner.close()
                    # Before the HTTP client closes under the lookups it may still run.
                    await data_service.close()
    finally:
        await app.state.db.dispose()
        await jobs_redis.aclose()
        await app.state.redis.aclose()


def _death_report(what: str) -> Callable[[asyncio.Task[None]], None]:
    """What makes a background task that ended other than by its cancellation a developer's
    issue (Sentry), so `what` it does cannot stop silently."""

    def report(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        error = task.exception()
        logger.error("%s stopped: %r", what, error, exc_info=error)

    return report


_report_death = _death_report("The stats refresh")


@asynccontextmanager
async def _running(work: Coroutine[Any, Any, None], what: str) -> AsyncIterator[None]:
    """Runs `work` in a background task while the block runs; cancels it on shutdown."""
    task = asyncio.create_task(work)
    task.add_done_callback(_death_report(what))
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


@asynccontextmanager
async def _background(service: DataService, *, stats: bool) -> AsyncIterator[None]:
    """Runs the stats refresh while the app runs, when ``stats``; cancels it on shutdown."""
    if not stats:
        yield
        return
    async with _running(service.keep_stats_fresh(), "The stats refresh"):
        yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Builds the app; `settings` default to the environment (``get_settings()``)."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    setup_sentry(settings)

    app = CandleStackAPI(
        title=TITLE,
        version=settings.app_version,
        description=DESCRIPTION,
        servers=SERVERS,
        lifespan=lifespan,
        openapi_url=OPENAPI_URL,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    model_storage = LocalModelStorage(settings.model_storage_dir)
    app.state.model_storage = model_storage
    # What other modules delete with an account (their hooks run before the user's row goes,
    # and what they return after the commit).
    deletion_hooks: tuple[AccountDeletionHook, ...] = (ModelsAccountDeletion(model_storage),)
    app.state.account_deletion_hooks = deletion_hooks
    # The models list counts their backtests through experiments, which ml cannot import.
    app.state.model_usage = model_usage
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(data_router)
    app.include_router(accounts_router)
    app.include_router(models_router)
    app.include_router(preprocessing_router)
    app.include_router(backtests_router)
    if dev_sign_in_allowed(settings.app_env):
        app.include_router(dev_sign_in_router)
    if settings.app_env == SENTRY_CHECK_ENV:
        app.include_router(sentry_check_router)

    @app.get(DOCS_URL, include_in_schema=False)
    async def docs() -> HTMLResponse:
        return get_scalar_api_reference(
            openapi_url=OPENAPI_URL,
            title=TITLE,
            scalar_js_url=SCALAR_JS_URL,
            scalar_favicon_url=ICON_URL,
            theme="none",
            layout="modern",
            custom_css=DOCS_CSS,
            with_default_fonts=False,
            document_download_type="none",
            show_developer_tools="never",
            telemetry=False,
            agent=AgentScalarConfig(disabled=True),
            overrides={"mcp": {"disabled": True}},
        )

    return app


app = create_app()
