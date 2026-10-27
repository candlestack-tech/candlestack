"""The job queue in Redis, for both sides: the backend submits a job and waits for its result,
the worker claims a job, keeps its lease while it runs and finishes it with its result.

A claimed job moves from the queue to the running list in one step, with a lease that the
worker renews while the job runs. A job whose lease ran out was left by a worker that stopped
(a crash, its container's memory, a deploy): ``sweep`` fails it as ``worker-stopped``, and
it is never run again, as a job that took its worker down would take the next one down too.
The backend may submit it again.
"""

import json
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from redis.asyncio import Redis

from candlestack.jobs.protocol import (
    JOB_PREFIX,
    LEASE_PREFIX,
    QUEUE,
    RESULT_PREFIX,
    RESULT_TTL,
    RUNNING,
    Job,
    JobKind,
    JobResult,
)

logger = logging.getLogger(__name__)

# The longest a single BLPOP blocks, below the socket timeout of the app's Redis client
# (core.create_redis: 2 s), which would otherwise cut it off.
_WAIT_STEP = 1
# A job body outlives its deadline by this, so the worker can still read it to fail it.
_JOB_TTL_MARGIN = 60

WORKER_STOPPED = (
    "The worker stopped while it ran the job, so it has no result. Run it again. If it stops "
    "again, the model may need more memory than the worker has."
)

# Takes the oldest job and gives it a lease in one step: a sweep never sees it running without
# one. The lease's key is made here, as the job's ID is known only once it is taken.
_CLAIM = """
local id = redis.call("LMOVE", KEYS[1], KEYS[2], "LEFT", "RIGHT")
if id then
    redis.call("SET", ARGV[1] .. id, ARGV[2], "EX", ARGV[3])
end
return id
"""
# Publishes the result only while the job is still this worker's: one that a sweep failed
# already keeps the sweep's result.
_FINISH = """
if redis.call("LREM", KEYS[1], 1, ARGV[1]) == 0 then
    return 0
end
redis.call("DEL", KEYS[2], KEYS[3])
redis.call("RPUSH", KEYS[4], ARGV[2])
redis.call("EXPIRE", KEYS[4], ARGV[3])
return 1
"""
# Takes the running jobs without a lease off the running list; returns each one's ID and body
# (empty when it is gone), so the caller can fail them.
_SWEEP = """
local stalled = {}
for _, id in ipairs(redis.call("LRANGE", KEYS[1], 0, -1)) do
    if redis.call("EXISTS", ARGV[1] .. id) == 0 then
        redis.call("LREM", KEYS[1], 1, id)
        table.insert(stalled, id)
        table.insert(stalled, redis.call("GET", ARGV[2] .. id) or "")
        redis.call("DEL", ARGV[2] .. id)
    end
end
return stalled
"""


@dataclass(frozen=True, slots=True)
class Claimed:
    """A job a worker took: its ID, and the job, or None when it is ``gone`` (its body expired
    long after its deadline) or cannot be read (written by another version of the backend). The
    ``kind`` of a job that is None is a guess, for its failed result."""

    id: str
    job: Job | None
    kind: JobKind
    gone: bool = False


class JobQueue:
    """Raises ``redis.exceptions.RedisError`` from every method when Redis fails."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._claim = redis.register_script(_CLAIM)
        self._finish = redis.register_script(_FINISH)
        self._sweep = redis.register_script(_SWEEP)

    async def ping(self) -> None:
        """Raises ``redis.exceptions.RedisError`` when the queue cannot be reached."""
        await self._redis.ping()

    async def submit(self, job: Job) -> None:
        ttl = max(math.ceil(job.deadline - time.time()), 1) + _JOB_TTL_MARGIN
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(JOB_PREFIX + job.id, job.model_dump_json(), ex=ttl)
            pipe.rpush(QUEUE, job.id)
            await pipe.execute()

    async def result(
        self,
        job_id: str,
        wait: float,
        *,
        claimed: Callable[[], Awaitable[None]] | None = None,
    ) -> JobResult | None:
        """The job's result, waiting up to ``wait`` seconds for it; None when none came.
        ``claimed`` is awaited once, at most ``_WAIT_STEP`` after a worker took the job (it
        holds its lease), when that is seen before the result."""
        deadline = time.monotonic() + wait
        while (left := deadline - time.monotonic()) > 0:
            popped = await self._redis.blpop(
                [RESULT_PREFIX + job_id], timeout=min(left, _WAIT_STEP)
            )
            if popped is not None:
                return JobResult.model_validate_json(popped[1])
            if claimed is not None and await self.taken(job_id):
                await claimed()
                claimed = None
        return None

    async def taken(self, job_id: str) -> bool:
        """Whether a worker runs the job: it holds the job's lease from its claim until it
        publishes the result."""
        return bool(await self._redis.exists(LEASE_PREFIX + job_id))

    async def claim(self, worker: str, lease: int) -> Claimed | None:
        """The oldest job in the queue, now running under ``worker``'s lease of ``lease``
        seconds; None when the queue is empty."""
        job_id = await self._claim(keys=[QUEUE, RUNNING], args=[LEASE_PREFIX, worker, lease])
        if job_id is None:
            return None
        job_id = job_id.decode() if isinstance(job_id, bytes) else str(job_id)
        body = await self._redis.get(JOB_PREFIX + job_id)
        return _claimed(job_id, body)

    async def renew(self, job_id: str, worker: str, lease: int) -> None:
        await self._redis.set(LEASE_PREFIX + job_id, worker, ex=lease)

    async def finish(self, result: JobResult) -> bool:
        """Publishes the result of a running job; False when the job is not running (a sweep
        failed it after its lease ran out), which keeps the sweep's result."""
        published = await self._finish(
            keys=[
                RUNNING,
                LEASE_PREFIX + result.id,
                JOB_PREFIX + result.id,
                RESULT_PREFIX + result.id,
            ],
            args=[result.id, result.model_dump_json(), RESULT_TTL],
        )
        return bool(published)

    async def sweep(self) -> list[JobResult]:
        """Fails the running jobs whose lease ran out, as ``worker-stopped``; returns their
        results."""
        stalled: list[Any] = await self._sweep(keys=[RUNNING], args=[LEASE_PREFIX, JOB_PREFIX])
        results = []
        for raw_id, body in zip(stalled[::2], stalled[1::2], strict=True):
            claimed = _claimed(raw_id.decode(), body or None)
            result = JobResult.failed(claimed.id, claimed.kind, "worker-stopped", WORKER_STOPPED)
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.rpush(RESULT_PREFIX + claimed.id, result.model_dump_json())
                pipe.expire(RESULT_PREFIX + claimed.id, RESULT_TTL)
                await pipe.execute()
            results.append(result)
        return results


def _claimed(job_id: str, body: bytes | str | None) -> Claimed:
    if body is None:
        return Claimed(job_id, None, "predict", gone=True)
    try:
        job = Job.model_validate_json(body)
    except ValidationError:
        return Claimed(job_id, None, _kind_of(body))
    return Claimed(job_id, job, job.kind)


def _kind_of(body: bytes | str) -> JobKind:
    """The kind a job that cannot be read says it is, for its failed result."""
    try:
        kind = json.loads(body).get("kind")
    except (ValueError, AttributeError):
        kind = None
    return "check" if kind == "check" else "predict"
