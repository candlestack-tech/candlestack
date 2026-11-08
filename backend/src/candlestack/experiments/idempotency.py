"""Idempotency of the starts: a client that sends an idempotency key with a start (the
``Idempotency-Key`` header of ``POST /api/v1/backtests`` and ``POST /api/v1/sweeps``, the
``idempotency_key`` of the MCP tools ``start_backtest`` and ``run_sweep``) can send it again,
after a timeout or a lost answer, and gets the first answer instead of a second start.

Each key is kept in the app's Redis for ``TTL`` seconds, per user and per kind of start, with
the SHA-256 of the body it came with, and then what the start made: the ID of what it stored as
soon as that is committed (``Committed``), and its answer once it answered. A key repeated with
another body is ``IdempotencyKeyReused``; a key repeated while its first request is still being
answered, before anything was committed, is ``IdempotencyKeyInUse``. A repeat after the commit
gets the answer, or, when the first request stopped between its commit and its answer (the
client went away, a read failed), an answer built again from the ID (``rebuild``): never a
second start. A start that fails before its commit keeps nothing, so it can be sent again with
the same key. Redis out of reach is a warning, and the start goes on without the key. The
Redis is a cache that drops the least recently used keys when it is full, so a key may be
forgotten before 24 hours: then the request starts again.
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
# request runs: longer than the longest start (a sweep checks 20 setups against the data, each
# of which may wait for a source's request budget).
TTL = 24 * 3600
PENDING_TTL = 900
_PREFIX = "idempotency:"
_DIGEST = 64  # the hex SHA-256 of the body, before the rest of the stored value
# After the digest: the ID of what the start committed, or its answer (JSON, so never this).
_ID_MARK = b"#"

# What a start calls once it committed what it stored, with its ID.
type Committed = Callable[[str], Awaitable[None]]


def _name(user_id: str, kind: str, key: str) -> str:
    # The key hashed, so a long one costs no more than a short one.
    return f"{_PREFIX}{user_id}:{kind}:{hashlib.sha256(key.encode()).hexdigest()}"


async def once(  # noqa: PLR0913 (the start and what it is known by)
    redis: Redis,
    *,
    user_id: str,
    kind: str,
    key: str | None,
    body: bytes,
    answer: Callable[[Committed], Awaitable[bytes]],
    rebuild: Callable[[str], Awaitable[bytes]],
) -> tuple[bytes, bool]:
    """The answer of the start ``answer`` makes, and whether it is the first answer given
    again: without ``key`` it always starts. ``answer`` gets the callback to await with the ID
    of what it stored once that is committed; ``rebuild`` makes the answer from that ID for a
    repeat of a start that committed and never answered.

    Raises:
        IdempotencyKeyReused, IdempotencyKeyInUse, and what ``answer`` or ``rebuild`` raises.
    """
    if key is None:
        return await answer(_nothing), False
    name = _name(user_id, kind, key)
    digest = hashlib.sha256(body).hexdigest().encode()
    try:
        held = await redis.set(name, digest, nx=True, ex=PENDING_TTL)
        # Bytes: the app's client does not decode (core.create_redis).
        stored: bytes | None = None if held else await redis.get(name)  # ty: ignore[invalid-assignment]
    except RedisError as error:
        logger.warning("An idempotency key was not looked up (%s)", type(error).__name__)
        return await answer(_nothing), False
    if stored is not None:
        return await _again(stored, digest, rebuild), True
    made_id: list[str] = []

    async def committed(made: str) -> None:
        made_id.append(made)
        await _keep(redis, name, digest + _ID_MARK + made.encode())

    try:
        made = await answer(committed)
    except BaseException:
        with anyio.CancelScope(shield=True):
            # Nothing started, so the key is free for the next try; else it keeps the ID.
            if not made_id:
                await _forget(redis, name)
        raise
    await _keep(redis, name, digest + made)
    return made, False


async def _again(stored: bytes, digest: bytes, rebuild: Callable[[str], Awaitable[bytes]]) -> bytes:
    """The answer to a repeated key: the first answer, or one built from the committed ID."""
    if stored[:_DIGEST] != digest:
        raise IdempotencyKeyReused
    rest = stored[_DIGEST:]
    if not rest:
        raise IdempotencyKeyInUse
    if rest.startswith(_ID_MARK):
        return await rebuild(rest[len(_ID_MARK) :].decode())
    return rest


async def _nothing(_made: str) -> None:
    """The callback of a start without a key."""


async def _keep(redis: Redis, name: str, value: bytes) -> None:
    try:
        await redis.set(name, value, ex=TTL)
    except RedisError as error:
        logger.warning("An idempotency key was not kept (%s)", type(error).__name__)


async def _forget(redis: Redis, name: str) -> None:
    try:
        await redis.delete(name)
    except RedisError as error:
        logger.warning("An idempotency key was not freed (%s)", type(error).__name__)
