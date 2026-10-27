"""The Redis cache of the data module: values fetched once (single-flight), candle chunks as
Arrow IPC with zstd, and the errors of failed fetches for a few seconds.

Redis is disposable here. When it fails, values are fetched from the source and not cached,
a warning is logged and the cache is skipped for a few seconds; a Redis failure never fails a
request by itself.
"""

import asyncio
import io
import logging
import math
import secrets
import struct
import time
from collections.abc import Awaitable, Callable, Sequence

import orjson
import polars as pl
from redis.asyncio import Redis
from redis.exceptions import RedisError

from candlestack.data.errors import DataIntegrityError, SourceUnavailable

logger = logging.getLogger(__name__)

# The version of each key family's values (the first part of a name; 1 when not listed), or of
# its values of one market (``<family>:<market>``, before the family's). Redis keeps its contents
# across deploys, so a family gets a new version whenever the meaning or format of its values
# changes: values stored under the old version are never read again and expire. candles 2:
# archive candles off the timeframe's grid are taken from REST. catalog 2: crypto pairs have the
# exchange BINANCE. Stocks' candles 3, first 2, stats 2 and spark 2: Alpaca's SIP feed replaced
# IEX (the crypto values stay).
VERSIONS = {
    "candles": 2,
    "catalog": 2,
    "candles:stock": 3,
    "first:stock": 2,
    "stats:stock": 2,
    "spark:stock": 2,
}

LOCK_PREFIX = "data:lock:"
FAIL_PREFIX = "data:fail:"
LOCK_TTL_MS = 30_000
# How long the error of a failed fetch is kept: the callers waiting for the value and new ones
# get it at once, instead of calling the source again one after another.
FAIL_SECONDS = 10
POLL_SECONDS = 0.1
# How long the cache is skipped after Redis failed, so that a dead Redis costs one timeout
# per few seconds instead of one per call.
DOWN_SECONDS = 5.0

_REDIS_ERRORS = (RedisError, OSError)
# Deletes the lock only if it is still ours (it may have expired and been taken by another).
_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
)
# Extends the lock's expiry only if it is still ours.
_EXTEND = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('pexpire', KEYS[1], ARGV[2]) end return 0"
)
_HEADER = struct.Struct(">q")

# Patched by tests to avoid real waiting.
_sleep = asyncio.sleep

Fetch = Callable[[], Awaitable[tuple[bytes, int]]]
"""Produces a value to cache and the seconds to keep it."""


def _versioned(name: str) -> str:
    family, _, rest = name.partition(":")
    version = VERSIONS.get(f"{family}:{rest.partition(':')[0]}", VERSIONS.get(family, 1))
    return f"v{version}:{name}"


def key(name: str) -> str:
    """The Redis key of a value: ``data:v<version of its family>:<name>``, e.g.
    ``data:v2:candles:crypto:BTCUSDT:1h:2024-01``, ``data:v3:candles:stock:AAPL:1m:2024-06`` or
    ``data:v1:calendar``."""
    return f"data:{_versioned(name)}"


def lock_key(name: str) -> str:
    """The single-flight lock of a value, versioned with it (``data:lock:v2:catalog:crypto``):
    during a deploy, a process that reads the old version does not hold up one that fetches the
    new one."""
    return LOCK_PREFIX + _versioned(name)


def fail_key(name: str) -> str:
    """The error of a value's failed fetch, versioned with it (``data:fail:v2:catalog:crypto``)."""
    return FAIL_PREFIX + _versioned(name)


class Cache:
    """Values under ``data:v<version>:<name>`` (see ``key``); ``name`` is e.g. ``calendar`` or
    ``candles:crypto:BTCUSDT:1h:2024-01``."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._down_until = 0.0

    def available(self) -> bool:
        """Whether the cache is used now: ``False`` for a few seconds after Redis failed."""
        return not self._is_down()

    async def get(self, name: str) -> bytes | None:
        """The cached value, or ``None`` on a miss or when Redis fails."""
        if self._is_down():
            return None
        try:
            # redis-py types values as bytes | str; this client never decodes (create_redis).
            return await self._redis.get(key(name))  # ty: ignore[invalid-return-type]
        except _REDIS_ERRORS as exc:
            self._failed(exc)
            return None

    async def get_many(self, names: Sequence[str]) -> list[bytes | None]:
        """The cached values of ``names`` in one round trip (``MGET``), ``None`` for each miss;
        all ``None`` when Redis fails."""
        if not names or self._is_down():
            return [None] * len(names)
        try:
            # redis-py types values as bytes | str; this client never decodes (create_redis).
            return await self._redis.mget([key(name) for name in names])  # ty: ignore[invalid-return-type]
        except _REDIS_ERRORS as exc:
            self._failed(exc)
            return [None] * len(names)

    async def set(self, name: str, value: bytes, ttl: int) -> None:
        """Stores a value for ``ttl`` seconds; a Redis failure only logs."""
        await self._store(key(name), value, ttl)

    async def delete(self, name: str) -> None:
        """Removes a value; a Redis failure only logs."""
        if self._is_down():
            return
        try:
            await self._redis.delete(key(name))
        except _REDIS_ERRORS as exc:
            self._failed(exc)

    async def get_or_fetch(self, name: str, fetch: Fetch, *, background: bool = False) -> bytes:
        """The cached value, or the one ``fetch`` produces, which is then cached.

        Single-flight: one caller takes the lock ``lock_key(name)`` (``SET NX``, 30 s) and
        fetches; concurrent callers poll until the value appears, or take the lock themselves
        when it expires. When ``fetch`` fails with ``SourceUnavailable`` or
        ``DataIntegrityError``, the error is kept as ``fail_key(name)`` for ``FAIL_SECONDS``
        (at most its ``retry_after``): the waiting callers and new ones raise it without calling
        the source. Other errors propagate and nothing is kept. A ``background`` caller, whose
        ``fetch`` waits for the source's budget, does not take a kept error that waiting helps
        (one with ``retry_after``: a budget spent, a source limiting us): it fetches itself.
        """
        lock, token = lock_key(name), secrets.token_hex(8)
        while True:
            if self._is_down():
                return (await fetch())[0]
            try:
                value = await self._lookup(name, background=background)
                if value is not None:
                    return value
                if await self._redis.set(lock, token, nx=True, px=LOCK_TTL_MS):
                    value = await self._recheck(name, lock, token, background=background)
                    if value is None:
                        break
                    return value
            except _REDIS_ERRORS as exc:
                self._failed(exc)
                return (await fetch())[0]
            await _sleep(POLL_SECONDS)
        try:
            try:
                value, ttl = await fetch()
            except (SourceUnavailable, DataIntegrityError) as exc:
                await self._store(fail_key(name), *_encode_failure(exc))
                raise
            await self.set(name, value, ttl)
            return value
        finally:
            await self._release(lock, token)

    async def _lookup(self, name: str, *, background: bool = False) -> bytes | None:
        """The stored value, or ``None``; raises the error kept by a failed fetch, except one
        that waiting helps for a ``background`` caller."""
        value, failure = await self._redis.mget(key(name), fail_key(name))
        if value is not None:
            # redis-py types values as bytes | str; this client never decodes.
            return value  # ty: ignore[invalid-return-type]
        if failure is not None:
            error = _decode_failure(failure)
            waitable = isinstance(error, SourceUnavailable) and error.retry_after is not None
            if not (background and waitable):
                raise error
        return None

    async def _recheck(
        self, name: str, lock: str, token: str, *, background: bool = False
    ) -> bytes | None:
        """Looks again right after the lock was taken: the holder may have stored the value and
        released the lock between the first lookup and the lock, and it would be fetched twice.
        When it is there (or its error), the lock is given back."""
        try:
            value = await self._lookup(name, background=background)
        except (SourceUnavailable, DataIntegrityError):
            await self._release(lock, token)
            raise
        if value is not None:
            await self._release(lock, token)
        return value

    async def lock(self, name: str, ttl_ms: int = LOCK_TTL_MS) -> str | None:
        """Takes the single-flight lock of ``name`` without waiting: a token to give back with
        ``unlock``, or ``None`` when another holds the lock. It expires after ``ttl_ms``, which
        must outlast the work it guards. Without Redis there is nobody to coordinate with, so
        the lock is granted."""
        token = secrets.token_hex(8)
        if self._is_down():
            return token
        try:
            taken = await self._redis.set(lock_key(name), token, nx=True, px=ttl_ms)
        except _REDIS_ERRORS as exc:
            self._failed(exc)
            return token
        return token if taken else None

    async def extend(self, name: str, token: str, ttl_ms: int) -> bool:
        """Sets the lock of ``name`` to expire ``ttl_ms`` from now if ``token`` still holds it;
        ``False`` when another holds it now (or nobody). Without Redis it is granted."""
        if self._is_down():
            return True
        try:
            return bool(await self._redis.eval(_EXTEND, 1, lock_key(name), token, ttl_ms))
        except _REDIS_ERRORS as exc:
            self._failed(exc)
            return True

    async def unlock(self, name: str, token: str) -> None:
        """Gives the lock back, only if ``token`` still holds it (it may have expired and been
        taken by another)."""
        await self._release(lock_key(name), token)

    async def head(self, name: str, size: int) -> bytes | None:
        """The first ``size`` bytes of a value (``GETRANGE``), or ``None`` on a miss or when
        Redis fails: a large value's header without reading all of it."""
        if self._is_down():
            return None
        try:
            # redis-py types values as bytes | str; this client never decodes (create_redis).
            value: bytes = await self._redis.getrange(key(name), 0, size - 1)  # ty: ignore[invalid-assignment]
        except _REDIS_ERRORS as exc:
            self._failed(exc)
            return None
        return value or None

    async def _store(self, key: str, value: bytes, ttl: int) -> None:
        if self._is_down():
            return
        try:
            await self._redis.set(key, value, ex=ttl)
        except _REDIS_ERRORS as exc:
            self._failed(exc)

    async def _release(self, lock: str, token: str) -> None:
        if self._is_down():
            return
        try:
            await self._redis.eval(_RELEASE, 1, lock, token)
        except _REDIS_ERRORS as exc:
            self._failed(exc)

    def _is_down(self) -> bool:
        return time.monotonic() < self._down_until

    def _failed(self, exc: BaseException) -> None:
        logger.warning(
            "Redis failed (%s): working without the cache for %.0f s",
            type(exc).__name__,
            DOWN_SECONDS,
        )
        self._down_until = time.monotonic() + DOWN_SECONDS


def _encode_failure(error: SourceUnavailable | DataIntegrityError) -> tuple[bytes, int]:
    """A failed fetch's error as kept in Redis, and how long to keep it."""
    ttl = FAIL_SECONDS
    retry_at = None
    if isinstance(error, SourceUnavailable) and error.retry_after is not None:
        ttl = max(1, min(ttl, error.retry_after))
        retry_at = time.time() + error.retry_after
    kind = "unavailable" if isinstance(error, SourceUnavailable) else "invalid"
    value = {"kind": kind, "source": error.source, "detail": error.detail, "retry_at": retry_at}
    return orjson.dumps(value), ttl


def _decode_failure(blob: bytes | str) -> SourceUnavailable | DataIntegrityError:
    value = orjson.loads(blob)
    if value["kind"] == "invalid":
        return DataIntegrityError(value["detail"], source=value["source"])
    retry_at = value["retry_at"]
    retry_after = None if retry_at is None else max(1, math.ceil(retry_at - time.time()))
    return SourceUnavailable(value["source"], value["detail"], retry_after=retry_after)


def encode_chunk(frame: pl.DataFrame, fetched_at: int) -> bytes:
    """A candle chunk as cached: the fetch time (big-endian int64 epoch seconds), then the frame
    as Arrow IPC with zstd."""
    buffer = io.BytesIO()
    buffer.write(_HEADER.pack(fetched_at))
    frame.write_ipc(buffer, compression="zstd")
    return buffer.getvalue()


HEADER_SIZE = _HEADER.size


def decode_fetched_at(blob: bytes) -> int:
    """The fetch time of an ``encode_chunk`` blob (its first ``HEADER_SIZE`` bytes are enough),
    without decoding its frame."""
    return _HEADER.unpack_from(blob)[0]


def decode_chunk(blob: bytes) -> tuple[pl.DataFrame, int]:
    """The frame and fetch time of an ``encode_chunk`` blob."""
    (fetched_at,) = _HEADER.unpack_from(blob)
    return pl.read_ipc(io.BytesIO(blob[_HEADER.size :])), fetched_at
