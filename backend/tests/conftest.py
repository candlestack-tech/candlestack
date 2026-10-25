"""Fixtures shared by the unit and integration tests.

``app`` is ``create_app(settings)`` with Redis replaced by ``fake_redis`` and Postgres by
``fake_database``; ``client`` runs it (lifespan included) in a TestClient. Tests override
``settings``, ``fake_redis`` or ``fake_database`` to vary them.
"""

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from candlestack.core import Settings
from candlestack.core.db import get_database
from candlestack.core.redis import get_redis
from candlestack.main import create_app

# The worker's tests load models with Keras on the backend the worker runs them on (its image
# and its loop set the same), before any test imports Keras.
os.environ.setdefault("KERAS_BACKEND", "jax")


class FakeRedis:
    """Stands in for the Redis client where a test only needs it up or down."""

    def __init__(self, *, up: bool = True) -> None:
        self.up = up

    async def ping(self) -> bool:
        if not self.up:
            raise RedisConnectionError("Connection refused")
        return True


class FakeDatabase:
    """Stands in for the database where a test only needs it up or down. Its sessions are
    bound to no database: a route may open one, but a query in it fails."""

    def __init__(self, *, up: bool = True) -> None:
        self.up = up
        self.sessions = async_sessionmaker()

    async def ping(self) -> None:
        if not self.up:
            raise OperationalError("SELECT 1", None, ConnectionRefusedError("Connection refused"))


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Sockets are off (``--disable-socket`` in the pytest options) except for the suites that
    need them: integration tests talk to Redis, e2e tests to the stack. On Windows the event
    loop wakes itself through a loopback TCP pair, so unit tests there may reach loopback only.
    """
    for item in items:
        if item.get_closest_marker("integration") or item.get_closest_marker("e2e"):
            item.add_marker(pytest.mark.enable_socket)
        elif sys.platform == "win32":
            item.add_marker(pytest.mark.allow_hosts(["127.0.0.1", "::1"]))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        app_version="1.2.3",
        app_commit="abc1234",
        model_storage_dir=tmp_path / "models",
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def fake_database() -> FakeDatabase:
    return FakeDatabase()


@pytest.fixture
def app(settings: Settings, fake_redis: FakeRedis, fake_database: FakeDatabase) -> FastAPI:
    app = create_app(settings)
    app.dependency_overrides[get_redis] = lambda: fake_redis
    app.dependency_overrides[get_database] = lambda: fake_database
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
