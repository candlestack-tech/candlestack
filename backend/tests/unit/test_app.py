import asyncio
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from candlestack.core import Database, RateLimiter, Settings
from candlestack.core.redis import get_redis
from candlestack.data import DataService
from candlestack.experiments import BacktestRunner
from candlestack.main import _report_death, create_app
from candlestack.main import app as module_app


def test_lifespan_opens_and_closes_the_shared_clients(app: FastAPI) -> None:
    with TestClient(app):
        assert isinstance(app.state.redis, Redis)
        assert isinstance(app.state.db, Database)
        assert isinstance(app.state.http, httpx.AsyncClient)
        assert isinstance(app.state.rate_limiter, RateLimiter)
        assert isinstance(app.state.data_service, DataService)
        assert isinstance(app.state.backtest_runner, BacktestRunner)
        assert app.state.http.headers["User-Agent"] == "candlestack/1.2.3"

    assert app.state.http.is_closed


def test_lifespan_refreshes_the_stats_while_the_app_runs(
    settings: Settings, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = []

    async def keep_stats_fresh(_service: DataService) -> None:
        events.append("started")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    monkeypatch.setattr(DataService, "keep_stats_fresh", keep_stats_fresh)
    app = create_app(settings.model_copy(update={"stats_refresh": True}))
    app.dependency_overrides[get_redis] = lambda: fake_redis

    with TestClient(app):
        assert events == ["started"]

    assert events == ["started", "cancelled"]


def test_lifespan_without_the_stats_refresh(app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DataService, "keep_stats_fresh", None)  # never called

    with TestClient(app):
        pass


def test_module_level_app_for_granian() -> None:
    assert isinstance(module_app, FastAPI)
    assert module_app.openapi_url == "/api/v1/openapi.json"


async def _fails() -> None:
    raise RuntimeError("boom")


async def _waits() -> None:
    await asyncio.Event().wait()


@pytest.mark.anyio
async def test_a_stats_task_that_dies_is_an_error_for_a_developer(caplog: pytest.LogCaptureFixture):
    task = asyncio.create_task(_fails())
    await asyncio.wait([task])

    with caplog.at_level(logging.ERROR):
        _report_death(task)

    assert "The stats refresh stopped: RuntimeError('boom')" in caplog.text
    assert caplog.records[-1].exc_info is not None


@pytest.mark.anyio
async def test_a_cancelled_stats_task_is_no_error(caplog: pytest.LogCaptureFixture):
    task = asyncio.create_task(_waits())
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.wait([task])

    _report_death(task)

    assert caplog.text == ""
