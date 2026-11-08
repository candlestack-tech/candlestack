"""The idempotency keys of the starts against a real Redis at REDIS_URL, flushed before each
test: the first answer given again, another body refused, a key held while its first request
runs, freed when it fails, and Redis out of reach worked around."""

from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from candlestack.core import create_redis
from candlestack.experiments.errors import IdempotencyKeyInUse, IdempotencyKeyReused
from candlestack.experiments.idempotency import PENDING_TTL, TTL, once

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = create_redis(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


class Starts:
    """What a start answers, counted."""

    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> bytes:
        self.count += 1
        return f'{{"id": "bt_{self.count}"}}'.encode()


async def start(
    redis: Redis, starts: Starts, key: str | None, body: bytes = b"{}"
) -> tuple[bytes, bool]:
    return await once(redis, user_id="usr_ada", kind="start", key=key, body=body, answer=starts)


async def test_the_same_key_and_body_answer_the_first_answer_for_a_day(redis: Redis) -> None:
    starts = Starts()

    first = await start(redis, starts, "k1")
    again = await start(redis, starts, "k1")

    assert first == (b'{"id": "bt_1"}', False)
    assert again == (b'{"id": "bt_1"}', True)
    assert starts.count == 1
    (name,) = await redis.keys("idempotency:*")
    assert TTL - 5 < await redis.ttl(name) <= TTL


async def test_keys_are_apart_by_user_and_kind_and_without_a_key_it_always_starts(
    redis: Redis,
) -> None:
    starts = Starts()

    await start(redis, starts, "k1")
    await once(redis, user_id="usr_bob", kind="start", key="k1", body=b"{}", answer=starts)
    await once(redis, user_id="usr_ada", kind="sweep", key="k1", body=b"{}", answer=starts)
    await start(redis, starts, None)
    await start(redis, starts, None)

    assert starts.count == 5


async def test_the_same_key_with_another_body_is_refused(redis: Redis) -> None:
    starts = Starts()
    await start(redis, starts, "k1", b'{"a": 1}')

    with pytest.raises(IdempotencyKeyReused):
        await start(redis, starts, "k1", b'{"a": 2}')

    assert starts.count == 1


async def test_a_key_is_held_while_its_first_request_runs(redis: Redis) -> None:
    starts = Starts()
    seen: list[BaseException] = []

    async def slow() -> bytes:
        # The same request again, while this one has not answered.
        try:
            await start(redis, starts, "k1")
        except IdempotencyKeyInUse as error:
            seen.append(error)
        return b'{"id": "bt_first"}'

    answer = await once(redis, user_id="usr_ada", kind="start", key="k1", body=b"{}", answer=slow)

    assert answer == (b'{"id": "bt_first"}', False)
    assert len(seen) == 1
    assert starts.count == 0


async def test_a_start_that_fails_frees_its_key(redis: Redis) -> None:
    async def refused() -> bytes:
        raise ValueError("the queue is full")

    with pytest.raises(ValueError, match="full"):
        await once(redis, user_id="usr_ada", kind="start", key="k1", body=b"{}", answer=refused)
    starts = Starts()
    again = await start(redis, starts, "k1")

    assert again == (b'{"id": "bt_1"}', False)


async def test_a_held_key_expires_with_a_request_that_never_answered(redis: Redis) -> None:
    starts = Starts()

    async def stuck() -> bytes:
        (name,) = await redis.keys("idempotency:*")
        assert PENDING_TTL - 5 < await redis.ttl(name) <= PENDING_TTL
        return b"{}"

    await once(redis, user_id="usr_ada", kind="start", key="k1", body=b"{}", answer=stuck)

    assert starts.count == 0


async def test_without_redis_the_start_goes_on_without_the_key(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gone = create_redis("redis://127.0.0.1:1/0")
    starts = Starts()
    try:
        first = await start(gone, starts, "k1")
        again = await start(gone, starts, "k1")
    finally:
        await gone.aclose()

    assert (first[1], again[1]) == (False, False)
    assert starts.count == 2
    assert "An idempotency key was not looked up" in caplog.text
