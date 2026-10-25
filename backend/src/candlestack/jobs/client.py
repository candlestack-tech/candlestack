"""What the backend calls to run a model: hands the job to the worker and waits for it.

The backend has the network and the candle cache, so it writes a job's candles to the shared
volume; the worker, which has neither, reads them there and writes the predictions next to
them. The job's files go once the result is read, whatever it is.
"""

import logging
import time
from typing import Any

import anyio
import polars as pl

from candlestack.jobs.files import JobFiles
from candlestack.jobs.protocol import DEFAULT_WAIT, Job, JobKind, JobResult, manifest_sha256
from candlestack.jobs.queue import JobQueue

logger = logging.getLogger(__name__)

NO_ANSWER = (
    "The worker did not answer in time. It may be busy with other models. Try again in a few "
    "minutes."
)


class JobClient:
    """Raises ``redis.exceptions.RedisError`` when the queue cannot be reached, and
    ``OSError`` when the shared volume cannot be written or read."""

    def __init__(self, queue: JobQueue, files: JobFiles) -> None:
        self._queue = queue
        self._files = files

    async def ping(self) -> None:
        """Raises ``redis.exceptions.RedisError`` when the queue cannot be reached, so a caller
        can refuse work at once instead of waiting for a result that cannot come."""
        await self._queue.ping()

    async def predict(
        self,
        *,
        model: str,
        sha256: str,
        manifest: dict[str, Any],
        candles: pl.DataFrame,
        wait: float = DEFAULT_WAIT,
    ) -> tuple[JobResult, pl.DataFrame | None]:
        """Runs the model (``<user_id>/<model_id>.keras`` in the model storage, with the
        SHA-256 it was uploaded with) on ``candles`` (``ts``, open, high, low, close, volume),
        its input built from ``manifest``. Returns the result and, when it is done, the
        predictions: ``ts`` and ``prediction``, one row per sample."""
        job = _job("predict", model, sha256, manifest, wait)
        try:
            await anyio.to_thread.run_sync(self._files.write_candles, job.id, candles)
            result = await self._run(job, wait)
            predictions = None
            if result.status == "done":
                predictions = await anyio.to_thread.run_sync(self._files.read_predictions, job.id)
            return result, predictions
        finally:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(self._files.discard, job.id)

    async def check(
        self, *, model: str, sha256: str, manifest: dict[str, Any], wait: float = DEFAULT_WAIT
    ) -> JobResult:
        """The trial run of the model with its manifest on built-in candles and, when the
        manifest has an ``example``, its comparison (docs/preprocessing.md, checks 4 and 5)."""
        return await self._run(_job("check", model, sha256, manifest, wait), wait)

    async def _run(self, job: Job, wait: float) -> JobResult:
        await self._queue.submit(job)
        result = await self._queue.result(job.id, wait)
        if result is None:
            result = JobResult.failed(job.id, job.kind, "expired", NO_ANSWER)
        elif result.reason == "internal":
            # The worker has no way to Sentry (no network): its bug becomes an issue here, and
            # its log has the traceback under the job's ID.
            logger.error("Job %s failed with an error of the worker", job.id)
        return result.model_copy(update={"manifest_sha256": manifest_sha256(job.manifest)})


def _job(kind: JobKind, model: str, sha256: str, manifest: dict[str, Any], wait: float) -> Job:
    return Job(
        kind=kind, model=model, sha256=sha256, manifest=manifest, deadline=time.time() + wait
    )
