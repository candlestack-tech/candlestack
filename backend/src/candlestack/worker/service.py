"""The worker's loop: takes one job at a time from the queue, runs it in a process of its own
with a time limit, and publishes its result.

The loop never imports Keras and never runs a model itself, so a model that crashes, hangs or
exhausts the container's memory fails its job and the loop goes on with the next one. While a
job runs, the loop renews its lease; a job whose lease ran out (the worker stopped) is failed
by the next sweep, never run again (``jobs.queue``). Redis out of reach is a warning and a
retry, never the end of the loop. SIGTERM (a deploy, ``docker stop``) fails the running job as
``worker-stopped`` and ends the loop.
"""

import logging
import os
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

import anyio
from anyio import CancelScope
from anyio.abc import Process
from pydantic import ValidationError
from redis.exceptions import RedisError

from candlestack.core import Settings, create_redis, get_settings, setup_logging, setup_sentry
from candlestack.jobs import WORKER_STOPPED, Claimed, Job, JobFiles, JobQueue, JobResult
from candlestack.worker.protocol import Outcome, Request

logger = logging.getLogger(__name__)

# How long the loop waits before it asks the queue again when it was empty, in seconds.
POLL = 0.5
# A running job's lease, and how often the loop renews it: a worker that stopped is noticed
# within the lease.
LEASE = 30
RENEW_EVERY = 10.0
# How often the loop fails the jobs of workers that stopped, and removes the files of jobs
# nobody waits for any more.
SWEEP_STALLED_EVERY = 10.0
SWEEP_FILES_EVERY = 600.0
# How long the loop waits when Redis cannot be reached.
RETRY_AFTER = 5.0
# The outcome is a small JSON document: anything larger is no outcome of the runner's.
OUTCOME_MAX = 1024 * 1024
RUNNER = (sys.executable, "-P", "-m", "candlestack.worker.runner")
# What the job's process gets of the environment: where Python and the app are, where Keras
# keeps its settings, nothing else (no Redis URL, no DSN).
_ENVIRONMENT_KEPT = (
    "PATH",
    "PYTHONPATH",
    "SYSTEMROOT",
    "TMPDIR",
    "TEMP",
    "TMP",
    "HOME",
    "KERAS_HOME",
    "KERAS_BACKEND",
    "JAX_PLATFORMS",
)
_ENVIRONMENT_DEFAULTS = {"KERAS_BACKEND": "jax", "JAX_PLATFORMS": "cpu"}

TIME_LIMIT = (
    "The model took longer than {seconds:g} seconds and was stopped. Choose a shorter period "
    "or a smaller model."
)
STOPPED = (
    "The model's process stopped before it finished, most likely because the model needs "
    "more memory than the worker has. Choose a shorter period or a smaller model."
)
EXPIRED = "The job waited for the worker longer than its caller waited for it, so it did not run."
OUTLIVED = "The job ran past the time its caller waited for it, so it was stopped."
INTERNAL = "The worker failed on this job. This is a bug of CandleStack and it was reported."


@dataclass(frozen=True, slots=True)
class Paths:
    """Where the model files (read-only) and the jobs' files are."""

    models: Path
    files: Path


class JobProcess:
    """Runs one job in a fresh process (``RUNNER``), stopped after ``time_limit`` seconds or
    at the job's deadline, whichever comes first: past it nobody reads the result."""

    def __init__(self, paths: Paths, time_limit: float, command: Sequence[str] = RUNNER) -> None:
        self._paths = paths
        self._time_limit = time_limit
        self._command = list(command)

    async def run(self, job: Job) -> JobResult:
        """The job's result; never raises but for cancellation, which stops the process."""
        request = Request(job=job, models_dir=self._paths.models, files_dir=self._paths.files)
        spawned = time.time()
        limit = min(self._time_limit, max(job.deadline - spawned, 0))
        # A session of its own, so whatever the process started goes with it (_stop_group).
        async with await anyio.open_process(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=child_environment(),
            start_new_session=True,
        ) as process:
            try:
                with anyio.move_on_after(limit) as scope:
                    output = await _communicate(process, request.model_dump_json().encode())
                    code = await process.wait()
            finally:
                _stop_group(process)
            if scope.cancelled_caught:
                if limit < self._time_limit:
                    return JobResult.failed(job.id, job.kind, "expired", OUTLIVED)
                return JobResult.failed(
                    job.id, job.kind, "time-limit", TIME_LIMIT.format(seconds=self._time_limit)
                )
        return _result(job, output, code, spawned)


def _stop_group(process: Process) -> None:
    """Kills the job's process and anything it started that is still running (Linux: its
    process group), so no part of one model outlives its job."""
    if sys.platform == "win32":
        with suppress(ProcessLookupError):
            process.kill()
        return
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(process.pid, signal.SIGKILL)


def child_environment() -> dict[str, str]:
    kept = {name: os.environ[name] for name in _ENVIRONMENT_KEPT if name in os.environ}
    return {**_ENVIRONMENT_DEFAULTS, **kept}


async def _communicate(process: Process, payload: bytes) -> bytes:
    """Sends the request and reads the outcome until the process closes its output."""
    assert process.stdin is not None
    assert process.stdout is not None
    # The process may end before it reads the request: its exit code says how.
    with suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
        await process.stdin.send(payload)
        await process.stdin.aclose()
    chunks, size = [], 0
    async for chunk in process.stdout:
        size += len(chunk)
        if size > OUTCOME_MAX:
            process.kill()
            return b""
        chunks.append(chunk)
    return b"".join(chunks)


def _result(job: Job, output: bytes, code: int, spawned: float) -> JobResult:
    """The result in the process' outcome, or the failure its exit says."""
    try:
        outcome = Outcome.model_validate_json(output)
    except ValidationError:
        # Killed (a negative code: the signal), mostly by the kernel when the container ran
        # out of memory: the model's doing, not a bug.
        if code < 0:
            logger.warning("The process of job %s was killed (signal %d)", job.id, -code)
            return JobResult.failed(job.id, job.kind, "stopped", STOPPED)
        logger.error("The process of job %s ended without a result (exit code %d)", job.id, code)
        return JobResult.failed(job.id, job.kind, "internal", INTERNAL)
    if outcome.trace is not None:
        logger.error(
            "Job %s failed with an error of the worker", job.id, extra={"trace": outcome.trace}
        )
    start = round(max(outcome.began_at - spawned, 0) * 1000, 1)
    timings = outcome.result.timings.model_copy(update={"start": start})
    return outcome.result.model_copy(update={"timings": timings})


class Worker:
    def __init__(self, queue: JobQueue, files: JobFiles, process: JobProcess, name: str) -> None:
        self._queue = queue
        self._files = files
        self._process = process
        self._name = name
        self._next_stalled_sweep = 0.0
        self._next_files_sweep = 0.0

    async def serve(self) -> None:
        """Runs jobs until cancelled."""
        while True:
            if not await self.step():
                await anyio.sleep(POLL)

    async def step(self) -> bool:
        """Sweeps when it is time, then runs the oldest job; False when there was none or the
        queue could not be reached."""
        try:
            await self._sweep_when_due()
            claimed = await self._queue.claim(self._name, LEASE)
        except RedisError as error:
            logger.warning("The job queue cannot be reached (%s)", type(error).__name__)
            await anyio.sleep(RETRY_AFTER)
            return False
        if claimed is None:
            return False
        await self._handle(claimed)
        return True

    async def _handle(self, claimed: Claimed) -> None:
        taken = time.time()
        job = claimed.job
        if job is None:
            result = _unreadable(claimed)
        elif job.deadline < taken:
            result = JobResult.failed(job.id, job.kind, "expired", EXPIRED)
        else:
            result = await self._run(job)
        queued = round(max(taken - job.enqueued_at, 0) * 1000, 1) if job is not None else None
        total = round((time.time() - taken) * 1000, 1)
        timings = result.timings.model_copy(update={"queued": queued, "total": total})
        result = result.model_copy(update={"timings": timings})
        await self._finish(result)
        _log(result)

    async def _run(self, job: Job) -> JobResult:
        """Runs the job while it renews its lease. Cancelled, it fails the job as
        ``worker-stopped`` (the process is stopped on the way out) and stops."""
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(self._keep_lease, job.id)
                result = await self._process.run(job)
                group.cancel_scope.cancel()
        except anyio.get_cancelled_exc_class():
            logger.warning("Job %s failed: the worker stopped while it ran", job.id)
            with anyio.CancelScope(shield=True):
                await self._finish(
                    JobResult.failed(job.id, job.kind, "worker-stopped", WORKER_STOPPED)
                )
            raise
        return result

    async def _keep_lease(self, job_id: str) -> None:
        while True:
            await anyio.sleep(RENEW_EVERY)
            try:
                await self._queue.renew(job_id, self._name, LEASE)
            except RedisError as error:
                logger.warning(
                    "The lease of job %s was not renewed (%s)", job_id, type(error).__name__
                )

    async def _finish(self, result: JobResult) -> None:
        try:
            published = await self._queue.finish(result)
        except RedisError as error:
            # Its lease runs out, and a sweep fails it then.
            logger.warning(
                "The result of job %s was not published (%s)", result.id, type(error).__name__
            )
            return
        if not published:
            logger.warning("Job %s was failed by a sweep before it finished", result.id)

    async def _sweep_when_due(self) -> None:
        now = time.monotonic()
        if now >= self._next_stalled_sweep:
            for result in await self._queue.sweep():
                logger.warning("Job %s was failed: its worker stopped while it ran", result.id)
            self._next_stalled_sweep = now + SWEEP_STALLED_EVERY
        if now >= self._next_files_sweep:
            try:
                removed = await anyio.to_thread.run_sync(self._files.sweep)
            except OSError as error:
                logger.warning("The jobs' files were not swept (%s)", type(error).__name__)
            else:
                if removed:
                    logger.info("Removed the files of %d jobs nobody waits for", removed)
            self._next_files_sweep = now + SWEEP_FILES_EVERY


def _unreadable(claimed: Claimed) -> JobResult:
    if claimed.gone:
        return JobResult.failed(claimed.id, claimed.kind, "expired", EXPIRED)
    logger.error("Job %s cannot be read", claimed.id)
    return JobResult.failed(claimed.id, claimed.kind, "internal", INTERNAL)


def _log(result: JobResult) -> None:
    extra = {
        "job_id": result.id,
        "kind": result.kind,
        "status": result.status,
        "reason": result.reason,
        "duration_ms": result.timings.total,
    }
    logger.info("Job %s %s", result.id, result.status, extra=extra)


async def serve(settings: Settings) -> None:
    """Runs the worker until SIGTERM or SIGINT."""
    redis = create_redis(settings.jobs_redis_url)
    worker = Worker(
        JobQueue(redis),
        JobFiles(settings.job_files_dir),
        JobProcess(
            Paths(settings.model_storage_dir, settings.job_files_dir), settings.job_time_limit
        ),
        name=platform.node(),
    )
    logger.info("The worker is ready for jobs", extra={"time_limit_s": settings.job_time_limit})
    try:
        async with anyio.create_task_group() as group:
            if sys.platform != "win32":
                group.start_soon(_stop_on_signal, group.cancel_scope)
            await worker.serve()
    finally:
        # Stopped by a signal, the loop is cancelled: the connections still close.
        with anyio.CancelScope(shield=True):
            await redis.aclose()


async def _stop_on_signal(scope: CancelScope) -> None:
    with anyio.open_signal_receiver(signal.SIGTERM, signal.SIGINT) as signals:
        async for received in signals:
            logger.info("The worker stops (%s)", signal.Signals(received).name)
            scope.cancel()
            return


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    setup_sentry(settings)
    anyio.run(serve, settings)
