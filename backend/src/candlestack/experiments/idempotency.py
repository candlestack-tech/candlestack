"""Idempotency of the starts: a client that sends an idempotency key with a start (the
``Idempotency-Key`` header of ``POST /api/v1/backtests`` and ``POST /api/v1/sweeps``, the
``idempotency_key`` of the MCP tools ``start_backtest`` and ``run_sweep``) can send it again,
after a timeout or a lost answer, and gets the first answer instead of a second start.

Each key is kept in the app's Redis for ``TTL`` seconds, per user and per kind of start, with
the SHA-256 of the body it came with and the first answer. A key repeated with another body is
``IdempotencyKeyReused``; a key repeated while its first request is still being answered is
``IdempotencyKeyInUse``. A start that fails keeps nothing, so it can be sent again with the same
key. Redis out of reach is a warning, and the start goes on without the key. The Redis is a
cache that drops the least recently used keys when it is full, so a key may be forgotten before
24 hours: then the request starts again.
"""

import hashlib
import logging
from collections.abc import Awaitable, Callable

import anyio
from redis.asyncio import Redis
from redis.exceptions import RedisError

from candlestack.experiments.errors import IdempotencyKeyInUse, IdempotencyKeyReused

logger = logging.getLogger(__name__)

# A key is 1 to 255 visible ASCII characters, as a UUID or any other the client makes.
KEY_PATTERN = r"^[!-~]{1,255}$"
KEY_MAX = 255
# How long a key and its first answer are kept, and how long a key is held while its first
# request runs (a start checks its setup against the data, which may take a while).
TTL = 24 * 3600
PENDING_TTL = 300
_PREFIX = "idempotency:"
_DIGEST = 64  # the hex SHA-256 of the body, before the answer in the stored value


def _name(user_id: str, kind: str, key: str) -> str:
    # The key hashed, so a long one costs no more than a short one.
    return f"{_PREFIX}{user_id}:{kind}:{hashlib.sha256(key.encode()).hexdigest()}"


async def once(
    redis: Redis,
    *,
    user_id: str,
    kind: str,
    key: str | None,
    body: bytes,
    answer: Callable[[], Awaitable[bytes]],
) -> tuple[bytes, bool]:
    """The answer of the start ``answer`` makes, and whether it is the first answer given
    again: without ``key`` it always starts.

    Raises:
        IdempotencyKeyReused, IdempotencyKeyInUse, and what ``answer`` raises.
    """
    if key is None:
        return await answer(), False
    name = _name(user_id, kind, key)
    digest = hashlib.sha256(body).hexdigest().encode()
    try:
        held = await redis.set(name, digest, nx=True, ex=PENDING_TTL)
        # Bytes: the app's client does not decode (core.create_redis).
        stored: bytes | None = None if held else await redis.get(name)  # ty: ignore[invalid-assignment]
    except RedisError as error:
        logger.warning("An idempotency key was not looked up (%s)", type(error).__name__)
        return await answer(), False
    if stored is not None:
        if stored[:_DIGEST] != digest:
            raise IdempotencyKeyReused
        if len(stored) == _DIGEST:
            raise IdempotencyKeyInUse
        return stored[_DIGEST:], True
    try:
        made = await answer()
    except BaseException:
        # Nothing started, so the key is free for the next try.
        with anyio.CancelScope(shield=True):
            await _forget(redis, name)
        raise
    try:
        await redis.set(name, digest + made, ex=TTL)
    except RedisError as error:
        logger.warning("An idempotency key was not kept (%s)", type(error).__name__)
    return made, False


async def _forget(redis: Redis, name: str) -> None:
    try:
        await redis.delete(name)
    except RedisError as error:
        logger.warning("An idempotency key was not freed (%s)", type(error).__name__)
