import asyncio
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

from candlestack.core import Settings, health
from candlestack.core.db import get_database
from candlestack.core.redis import get_redis
from candlestack.experiments import RunnerChannel
from candlestack.main import create_app


def test_ok_when_redis_and_postgres_answer(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    # Compact JSON: the deploy checks match these substrings.
    assert response.content == (
        b'{"status":"ok","env":"test","version":"1.2.3","commit":"abc1234","redis":"ok",'
        b'"postgres":"ok","runner":"ok"}'
    )


def test_503_when_redis_is_down(client: TestClient, fake_redis) -> None:
    fake_redis.up = False

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "env": "test",
        "version": "1.2.3",
        "commit": "abc1234",
        "redis": "error",
        "postgres": "ok",
        "runner": "ok",
    }


def test_503_when_postgres_is_down(client: TestClient, fake_database) -> None:
    fake_database.up = False

    response = client.get("/api/health")

    assert response.status_code == 503
    body = response.json()
    assert (body["status"], body["redis"], body["postgres"]) == ("error", "ok", "error")


def test_503_when_redis_hangs(
    client: TestClient, fake_redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hang() -> bool:
        await asyncio.sleep(10)
        return True

    monkeypatch.setattr(fake_redis, "ping", hang)
    monkeypatch.setattr(health, "REDIS_TIMEOUT", 0.05)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["redis"] == "error"


def test_503_when_postgres_hangs(
    client: TestClient, fake_database, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def hang() -> None:
        await asyncio.sleep(10)

    monkeypatch.setattr(fake_database, "ping", hang)
    monkeypatch.setattr(health, "POSTGRES_TIMEOUT", 0.05)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["postgres"] == "error"


class KeyRedis:
    """A Redis up with the keys of ``keys``; ``down`` makes every command fail."""

    def __init__(self, *keys: str) -> None:
        self.keys = set(keys)
        self.down = False
        self.written: list[tuple[str, bytes, int]] = []

    async def ping(self) -> bool:
        return True

    async def exists(self, key: str) -> int:
        if self.down:
            raise RedisConnectionError("Connection refused")
        return int(key in self.keys)

    async def set(self, key: str, value: bytes, ex: int) -> None:
        if self.down:
            raise RedisConnectionError("Connection refused")
        self.written.append((key, value, ex))


async def _listens(*_args: Any, **_kwargs: Any) -> None:
    await asyncio.Event().wait()


@pytest.mark.parametrize(
    ("keys", "down", "runner"),
    [(("candlestack:test:runner:alive",), False, "ok"), ((), False, "error"), ((), True, "error")],
)
def test_an_api_process_says_whether_the_runner_is_alive(
    settings: Settings,
    fake_database: Any,
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str, ...],
    down: bool,
    runner: str,
) -> None:
    monkeypatch.setattr(RunnerChannel, "listen", _listens)
    redis = KeyRedis(*keys)
    redis.down = down
    app = create_app(settings.model_copy(update={"app_role": "api"}))
    app.dependency_overrides[get_redis] = lambda: redis
    app.dependency_overrides[get_database] = lambda: fake_database

    with TestClient(app) as client:
        response = client.get("/api/health")

    # The API answers without the runner: only the field says it.
    assert response.status_code == 200
    assert response.json()["runner"] == runner


@pytest.mark.anyio
async def test_the_runner_keeps_its_key_and_says_once_when_it_cannot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(health, "RUNNER_ALIVE_EVERY", 0.01)
    redis = KeyRedis()

    with caplog.at_level(logging.WARNING):
        task = asyncio.create_task(health.keep_runner_alive(redis, "test"))  # ty: ignore[invalid-argument-type]
        await asyncio.sleep(0.05)
        redis.down = True
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert redis.written[0] == ("candlestack:test:runner:alive", b"1", health.RUNNER_ALIVE_TTL)
    assert caplog.text.count("The runner could not say it is alive (ConnectionError)") == 1
