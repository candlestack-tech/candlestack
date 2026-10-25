"""The job queue against a real Redis at REDIS_URL, flushed before each test: what the backend
and the worker see of a job, and what becomes of a job whose worker stopped."""

import time
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from candlestack.core import create_redis
from candlestack.jobs import WORKER_STOPPED, Job, JobQueue, JobResult

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

SHA256 = "ab" * 32


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = create_redis(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def queue(redis: Redis) -> JobQueue:
    return JobQueue(redis)


def job(kind: str = "predict", wait: float = 60) -> Job:
    return Job.model_validate(
        {
            "kind": kind,
            "model": "u/m.keras",
            "sha256": SHA256,
            "manifest": {"version": 1},
            "deadline": time.time() + wait,
        }
    )


async def test_a_job_goes_from_the_backend_to_a_worker_and_back(
    queue: JobQueue, redis: Redis
) -> None:
    first, second = job(), job("check")
    await queue.submit(first)
    await queue.submit(second)

    claimed = await queue.claim("worker-1", 30)

    assert claimed is not None
    assert (claimed.id, claimed.job, claimed.kind) == (first.id, first, "predict")
    assert await redis.lrange("jobs:running", 0, -1) == [first.id.encode()]
    assert await redis.get(f"jobs:lease:{first.id}") == b"worker-1"
    assert 0 < await redis.ttl(f"jobs:lease:{first.id}") <= 30

    done = JobResult(id=first.id, kind="predict", status="done", samples=3)
    assert await queue.finish(done)

    assert await queue.result(first.id, 1) == done
    assert await redis.lrange("jobs:running", 0, -1) == []
    assert not await redis.exists(f"jobs:lease:{first.id}", f"jobs:job:{first.id}")
    assert await redis.lrange("jobs:queue", 0, -1) == [second.id.encode()]


async def test_an_empty_queue_has_nothing_to_claim(queue: JobQueue) -> None:
    assert await queue.claim("worker-1", 30) is None


async def test_no_result_in_time_is_none(queue: JobQueue) -> None:
    started = time.monotonic()

    assert await queue.result("0" * 32, 0.3) is None
    assert time.monotonic() - started < 1.5


async def test_a_result_nobody_reads_expires(queue: JobQueue, redis: Redis) -> None:
    submitted = job()
    await queue.submit(submitted)
    await queue.claim("worker-1", 30)

    await queue.finish(JobResult(id=submitted.id, kind="predict", status="done"))

    assert 0 < await redis.ttl(f"jobs:result:{submitted.id}") <= 600


async def test_a_job_body_outlives_its_deadline(queue: JobQueue, redis: Redis) -> None:
    submitted = job(wait=30)

    await queue.submit(submitted)

    assert 60 < await redis.ttl(f"jobs:job:{submitted.id}") <= 91


async def test_a_job_whose_lease_ran_out_fails_and_never_runs_again(
    queue: JobQueue, redis: Redis
) -> None:
    submitted = job("check")
    await queue.submit(submitted)
    await queue.claim("worker-1", 30)
    await redis.delete(f"jobs:lease:{submitted.id}")  # the worker stopped: its lease ran out

    (swept,) = await queue.sweep()

    expected = JobResult.failed(submitted.id, "check", "worker-stopped", WORKER_STOPPED)
    assert swept == expected
    assert await queue.result(submitted.id, 1) == expected
    assert await redis.lrange("jobs:running", 0, -1) == []
    assert await queue.claim("worker-2", 30) is None
    # The stopped worker's late result is not published over the sweep's.
    assert not await queue.finish(JobResult(id=submitted.id, kind="check", status="done"))
    assert await queue.result(submitted.id, 0.2) is None


async def test_a_sweep_leaves_the_jobs_with_a_lease(queue: JobQueue) -> None:
    submitted = job()
    await queue.submit(submitted)
    await queue.claim("worker-1", 30)
    await queue.renew(submitted.id, "worker-1", 30)

    assert await queue.sweep() == []


async def test_a_job_that_is_gone_or_unreadable_is_claimed_for_failing(
    queue: JobQueue, redis: Redis
) -> None:
    gone, unreadable = job(), job("check")
    await queue.submit(gone)
    await queue.submit(unreadable)
    await redis.delete(f"jobs:job:{gone.id}")
    await redis.set(f"jobs:job:{unreadable.id}", '{"version": 2, "kind": "check"}')

    first = await queue.claim("worker-1", 30)
    second = await queue.claim("worker-1", 30)

    assert first is not None
    assert (first.id, first.job, first.gone) == (gone.id, None, True)
    assert second is not None
    assert (second.id, second.job, second.kind, second.gone) == (
        unreadable.id,
        None,
        "check",
        False,
    )


async def test_a_job_without_a_body_is_swept_as_a_predict(queue: JobQueue, redis: Redis) -> None:
    submitted = job("check")
    await queue.submit(submitted)
    await queue.claim("worker-1", 30)
    await redis.delete(f"jobs:lease:{submitted.id}", f"jobs:job:{submitted.id}")

    (swept,) = await queue.sweep()

    assert (swept.kind, swept.reason) == ("predict", "worker-stopped")


@pytest.mark.parametrize("body", ["not json", "[1]", '{"kind": "train"}'])
async def test_the_kind_of_an_unreadable_job_defaults_to_predict(
    queue: JobQueue, redis: Redis, body: str
) -> None:
    submitted = job("check")
    await queue.submit(submitted)
    await redis.set(f"jobs:job:{submitted.id}", body)

    claimed = await queue.claim("worker-1", 30)

    assert claimed is not None
    assert (claimed.job, claimed.kind) == (None, "predict")
