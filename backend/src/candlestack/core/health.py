"""``GET /api/health``: liveness of the app, its Redis and its Postgres, read by deploy checks
and ops; in an API process (``APP_ROLE`` ``api``) also whether the backtests' runner, a process
of its own, is alive: it keeps a key in Redis that expires unless it is written again
(``keep_runner_alive``)."""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from redis.exceptions import RedisError

from candlestack.core.config import Settings, SettingsDep
from candlestack.core.db import DATABASE_ERRORS, Database, DatabaseDep
from candlestack.core.redis import RedisDep

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

REDIS_TIMEOUT = 2.0
POSTGRES_TIMEOUT = 2.0
# The runner writes its key every RUNNER_ALIVE_EVERY seconds, and it expires after
# RUNNER_ALIVE_TTL: a runner that stopped is seen down within that.
RUNNER_ALIVE_EVERY = 10.0
RUNNER_ALIVE_TTL = 30

Check = Literal["ok", "error"]


class Health(BaseModel):
    status: Check
    env: str
    version: str
    commit: str
    redis: Check
    postgres: Check
    runner: Check = Field(
        description="Whether the backtests' runner is alive: in a process of its own, as its key "
        "in Redis says; always `ok` where the API process runs it itself"
    )


@router.get(
    "/api/health",
    summary="Health of the app",
    responses={503: {"model": Health, "description": "Redis or Postgres is unreachable"}},
)
async def health(
    settings: SettingsDep, redis: RedisDep, db: DatabaseDep, response: Response
) -> Health:
    """200 when the app, its Redis and its Postgres work, 503 with the same body when one of
    them does not. `runner` says whether the backtests' runner is alive; it does not change
    the status, as the API answers without it (a deploy's checks look at it).

    The body is compact JSON: deploy checks look for `"version":"<version>"` and the commit.
    """
    redis_ok, postgres_ok, runner_ok = await asyncio.gather(
        _ping_redis(redis), _ping_postgres(db), _runner_alive(redis, settings)
    )
    ok = redis_ok and postgres_ok
    if not ok:
        response.status_code = 503
    return Health(
        status=_check(ok),
        env=settings.app_env,
        version=settings.app_version,
        commit=settings.app_commit,
        redis=_check(redis_ok),
        postgres=_check(postgres_ok),
        runner=_check(runner_ok),
    )


def _check(ok: bool) -> Check:  # noqa: FBT001 (the result of a check)
    return "ok" if ok else "error"


async def _ping_redis(redis: Redis) -> bool:
    try:
        async with asyncio.timeout(REDIS_TIMEOUT):
            await redis.ping()
    except (RedisError, OSError, TimeoutError) as exc:
        logger.warning("Redis ping failed: %s", type(exc).__name__)
        return False
    return True


async def _ping_postgres(db: Database) -> bool:
    try:
        async with asyncio.timeout(POSTGRES_TIMEOUT):
            await db.ping()
    except DATABASE_ERRORS as exc:
        logger.warning("Postgres ping failed: %s", type(exc).__name__)
        return False
    return True


def runner_alive_key(app_env: str) -> str:
    """The key the runner of the environment keeps while it is alive."""
    return f"candlestack:{app_env}:runner:alive"


async def _runner_alive(redis: Redis, settings: Settings) -> bool:
    """Whether the runner is alive: always where this process runs it (``APP_ROLE`` other than
    ``api``), else while its key is in Redis."""
    if settings.app_role != "api":
        return True
    try:
        async with asyncio.timeout(REDIS_TIMEOUT):
            return bool(await redis.exists(runner_alive_key(settings.app_env)))
    except (RedisError, OSError, TimeoutError):
        return False  # the Redis check says why


async def keep_runner_alive(redis: Redis, app_env: str) -> None:
    """Writes the runner's key every ``RUNNER_ALIVE_EVERY`` seconds, until cancelled (the
    lifespan of a ``runner`` process). Redis out of reach is a warning, once until it answers
    again."""
    reachable = True
    while True:
        try:
            await redis.set(runner_alive_key(app_env), b"1", ex=RUNNER_ALIVE_TTL)
        except (RedisError, OSError) as error:
            if reachable:
                logger.warning("The runner could not say it is alive (%s)", type(error).__name__)
            reachable = False
        else:
            reachable = True
        await asyncio.sleep(RUNNER_ALIVE_EVERY)
