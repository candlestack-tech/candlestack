"""Fixtures of the integration tests: a real Redis at REDIS_URL and a real Postgres at
DATABASE_URL (CI starts both, locally the ones of compose.yaml).

Without REDIS_URL the tests use DB 15 of the local Redis, not the dev backend's DB 0: the data
tests flush their database before each test. Without DATABASE_URL they use the database
``candlestack_test`` of the local Postgres, which they create when it is missing; the first test
that needs it migrates it down to nothing and up again (migrations/), and the tests empty its
tables before each test. Never point them at a database whose data you need.

The modules that keep users' data (accounts, ml, experiments) share the app on that Postgres
(``settings``, ``app``, ``client``), ``sql``, ``empty_tables`` and the signed-in ``Account``
made by ``account``; their own conftests add what their API needs.
"""

import asyncio
import hashlib
import os
import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, make_url, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from candlestack.core import Settings
from candlestack.main import create_app

BACKEND = Path(__file__).parents[2]
LOCAL_DATABASE_URL = "postgresql+asyncpg://candlestack:candlestack@127.0.0.1:5432/candlestack_test"


@pytest.fixture
def redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/15")


def alembic_config(connection: Connection) -> Config:
    """The project's Alembic configuration, running on `connection`."""
    config = Config(toml_file=BACKEND / "pyproject.toml")
    config.attributes["connection"] = connection
    return config


def _migrate_from_scratch(connection: Connection) -> None:
    config = alembic_config(connection)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


async def _prepare(url: str) -> None:
    """Creates the database when missing, then migrates it down to nothing and up again."""
    name = make_url(url).database
    server = create_async_engine(
        make_url(url).set(database="postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with server.connect() as connection:
            exists = await connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if not exists:
                await connection.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await server.dispose()
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_migrate_from_scratch)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL") or LOCAL_DATABASE_URL
    asyncio.run(_prepare(url))
    return url


# --- The app on the real Postgres, for the modules that keep users' data ------------------------

ORIGIN = {"Origin": "http://testserver"}
# Every table of what users create; TRUNCATE needs the ones that refer to users with them.
TABLES = (
    "users, identities, sessions, user_preferences, api_keys, models, model_uploads, sweeps, "
    "backtests"
)


async def _run_sql(url: str, statement: str, params: dict[str, Any]) -> list[Any]:
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            result = await connection.execute(text(statement), params)
            return list(result.all()) if result.returns_rows else []
    finally:
        await engine.dispose()


@pytest.fixture
def sql(database_url: str) -> Callable[..., list[Any]]:
    """Runs one statement in the test database: ``sql("SELECT id FROM models")``, the rows."""
    return lambda statement, **params: asyncio.run(_run_sql(database_url, statement, params))


@pytest.fixture
def empty_tables(sql: Callable[..., list[Any]]) -> None:
    """Empties the tables of what users create. Not used by itself: the conftest of a module
    whose tests need Postgres uses it in an autouse fixture (the data tests need none)."""
    sql(f"TRUNCATE {TABLES}")


@pytest.fixture
def settings(database_url: str, redis_url: str, tmp_path: Path) -> Settings:
    return Settings(
        app_env="test",
        database_url=database_url,
        redis_url=redis_url,
        model_storage_dir=tmp_path / "models",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class Account:
    """A signed-in user: a row in ``users`` and a session whose cookie each request carries.
    The modules' conftests wrap it with the requests of their API."""

    def __init__(self, client: TestClient, sql: Callable[..., list[Any]], user_id: str) -> None:
        self.id = user_id
        self.email = f"{user_id}@example.com"
        self.sql = sql
        self._client = client
        self._token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        sql(
            "INSERT INTO users VALUES (:id, :name, :email, :now, :now)",
            id=user_id,
            name=user_id,
            email=self.email,
            now=now,
        )
        sql(
            "INSERT INTO sessions VALUES (:hash, :id, :now, :now, :expires)",
            hash=hashlib.sha256(self._token.encode()).digest(),
            id=user_id,
            now=now,
            expires=now + timedelta(days=30),
        )

    def request(self, method: str, url: str, **kwargs: Any) -> httpx2.Response:
        headers = {**ORIGIN, "Cookie": f"session={self._token}", **kwargs.pop("headers", {})}
        self._client.cookies.clear()
        return self._client.request(method, url, headers=headers, **kwargs)


@pytest.fixture
def account(client: TestClient, sql: Callable[..., list[Any]]) -> Callable[[str], Account]:
    """Makes a signed-in user with the ID: ``account("usr_adaaaaaaaa")``."""
    return lambda user_id: Account(client, sql, user_id)
