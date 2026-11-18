import asyncio
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from candlestack import main
from candlestack.core import Database, RateLimiter, Settings
from candlestack.core.redis import get_redis
from candlestack.data import DataService
from candlestack.experiments import BacktestRunner, RemoteRunner, RunnerChannel
from candlestack.main import _report_death, create_app
from candlestack.main import app as module_app
from candlestack.ml import ModelChecks


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


async def _listens(*_args: Any, **_kwargs: Any) -> None:
    await asyncio.Event().wait()


def test_an_api_process_leaves_the_background_work_to_the_runner(
    settings: Settings, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []
    monkeypatch.setattr(RunnerChannel, "listen", _listens)
    monkeypatch.setattr(BacktestRunner, "recover", lambda _runner: started.append("runner"))
    monkeypatch.setattr(ModelChecks, "recover", lambda _checks: started.append("checks"))
    monkeypatch.setattr(DataService, "keep_stats_fresh", lambda _service: started.append("stats"))
    monkeypatch.setattr(main, "keep_incoming_swept", lambda _storage: started.append("sweep"))
    monkeypatch.setattr(main, "keep_runner_alive", lambda *_args: started.append("alive"))
    app = create_app(settings.model_copy(update={"app_role": "api", "stats_refresh": True}))
    app.dependency_overrides[get_redis] = lambda: fake_redis

    with TestClient(app):
        runner = app.state.backtest_runner
        assert isinstance(runner, RemoteRunner)
        assert runner._listening is not None
        assert not runner._listening.done()
        assert app.state.mcp_sessions is not None

    assert started == []
    assert runner._listening.cancelled()


def test_a_runner_process_runs_the_backtests_and_the_background_work_but_no_mcp(
    settings: Settings, fake_redis: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[str] = []

    async def recover(_runner: BacktestRunner) -> None:
        started.append("runner")

    async def keep_stats_fresh(_service: DataService) -> None:
        started.append("stats")

    async def keep_incoming_swept(_storage: Any) -> None:
        started.append("sweep")

    async def keep_runner_alive(_redis: Any, app_env: str) -> None:
        started.append(f"alive in {app_env}")

    monkeypatch.setattr(BacktestRunner, "recover", recover)
    monkeypatch.setattr(ModelChecks, "recover", lambda _checks: started.append("checks"))
    monkeypatch.setattr(DataService, "keep_stats_fresh", keep_stats_fresh)
    monkeypatch.setattr(main, "keep_incoming_swept", keep_incoming_swept)
    monkeypatch.setattr(main, "keep_runner_alive", keep_runner_alive)
    app = create_app(settings.model_copy(update={"app_role": "runner", "stats_refresh": True}))
    app.dependency_overrides[get_redis] = lambda: fake_redis

    with TestClient(app):
        runner = app.state.backtest_runner
        assert isinstance(runner, BacktestRunner)
        assert runner._channel is not None
        assert not hasattr(app.state, "mcp_sessions")

    assert sorted(started) == ["alive in test", "checks", "runner", "stats", "sweep"]


def test_one_process_does_everything_by_default(app: FastAPI) -> None:
    with TestClient(app):
        runner = app.state.backtest_runner
        assert isinstance(runner, BacktestRunner)
        assert runner._channel is None
