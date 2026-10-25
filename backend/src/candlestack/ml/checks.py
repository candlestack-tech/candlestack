"""The worker's check of a model with its manifest: the trial run on built-in candles and the
comparison with the manifest's example (docs/preprocessing.md, checks 4 and 5), which makes the
model ``verified`` (docs/architecture.md, "Models").

Each check runs in a task of the API process, outside the request that added the manifest:
it reads the model's file SHA-256 and manifest, lets the session go, has the worker run the
``check`` job through the job queue (``jobs.JobClient``) and keeps what it came to in another
session, under the lock of the user's models and only while the stored manifest is still the
one the job ran with (a manifest replaced meanwhile gets a check of its own). A user's checks
run one after the other, as their backtests do: a manifest set while one of the user's checks
runs waits for it, so a user who keeps replacing manifests holds at most one job in the
worker's queue, and a model set again before its turn is checked once, with the manifest it
has then.

A check that says something about the model sets ``checked_at``: passed (``verified_at`` too,
when the example matched) or failed (``check_error``, the worker's sentences for the user). A
worker that cannot be reached or does not answer says nothing about the model, which stays
``ready`` unchecked: so without a worker (prod for now) nothing waits for it. The API runs in
one process, so a check that a stopped process was waiting for is lost with it; at startup
``recover`` checks the models with a manifest and no ``checked_at`` one after the other, and
stops at the first that finds no worker.
"""

import asyncio
import logging
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError

from candlestack.core import DATABASE_ERRORS, Database
from candlestack.jobs import DEFAULT_WAIT, JobClient, JobResult, Reason, manifest_sha256
from candlestack.ml.store import ModelStore

logger = logging.getLogger(__name__)

# The failures that say nothing about the model: no worker answered, the worker stopped or has
# a bug, or the model's file is gone (with the model, most likely). The model stays unchecked.
_NOT_THE_MODELS: frozenset[Reason] = frozenset(
    {"expired", "worker-stopped", "internal", "model-missing"}
)


def _now() -> datetime:
    return datetime.now(UTC)


def says_something(result: JobResult) -> bool:
    """Whether the check's result says something about the model: it passed or failed on the
    model, not on the worker."""
    return result.status == "done" or result.reason not in _NOT_THE_MODELS


class ModelChecks:
    """Starts the checks of the models and keeps their tasks until they end."""

    def __init__(self, db: Database, jobs: JobClient, *, wait: float = DEFAULT_WAIT) -> None:
        self._db = db
        self._jobs = jobs
        self._wait = wait
        self._tasks: set[asyncio.Task[Any]] = set()
        # The models each user's task has yet to check, in the order they were set (a dict as
        # an ordered set); a user is here while their task runs.
        self._waiting: dict[str, dict[str, None]] = {}

    def start(self, user_id: str, model_id: str) -> None:
        """Checks the user's model with the manifest it has, after the user's checks that
        run or wait, in the user's task."""
        waiting = self._waiting.get(user_id)
        if waiting is not None:
            waiting[model_id] = None
            return
        self._waiting[user_id] = {model_id: None}
        self._spawn(self._check_waiting(user_id), f"check the models of {user_id}")

    def recover(self) -> None:
        """Checks, in a task of its own, the models whose check a stopped process lost or that
        no worker answered for."""
        self._spawn(self._recover(), "check the unchecked models")

    async def close(self) -> None:
        """Stops the checks; their models stay unchecked until the next start."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn(self, work: Coroutine[Any, Any, Any], name: str) -> None:
        task = asyncio.create_task(work, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _check_waiting(self, user_id: str) -> None:
        waiting = self._waiting[user_id]
        try:
            while waiting:
                model_id = next(iter(waiting))
                del waiting[model_id]
                await self._check(user_id, model_id)
        finally:
            del self._waiting[user_id]

    async def _recover(self) -> None:
        try:
            async with self._db.sessions() as session:
                unchecked = await ModelStore(session).unchecked()
        except DATABASE_ERRORS as error:
            logger.warning("The unchecked models were not looked up (%s)", type(error).__name__)
            return
        for user_id, model_id in unchecked:
            if not await self._check(user_id, model_id):
                logger.warning("The unchecked models wait for the next start")
                return

    async def _check(self, user_id: str, model_id: str) -> bool:
        """Checks the model; False when no worker could be reached or answered, or Postgres
        could not."""
        try:
            return await self._run(user_id, model_id)
        except DATABASE_ERRORS as error:
            logger.warning(
                "The check of model %s could not reach Postgres (%s)",
                model_id,
                type(error).__name__,
            )
            return False
        except Exception:
            logger.exception("The check of model %s failed with an error of CandleStack", model_id)
            return True

    async def _run(self, user_id: str, model_id: str) -> bool:
        async with self._db.sessions() as session:
            checked = await ModelStore(session).check_input(user_id, model_id)
        if checked is None:
            return True
        sha256, manifest = checked
        try:
            result = await self._jobs.check(
                # The file as the model storage keeps it (storage.LocalModelStorage).
                model=f"{user_id}/{model_id}.keras",
                sha256=sha256.hex(),
                manifest=manifest,
                wait=self._wait,
            )
        except (RedisError, OSError) as error:
            logger.warning(
                "The check of model %s could not reach the worker (%s)",
                model_id,
                type(error).__name__,
            )
            return False
        if not says_something(result):
            logger.warning("The check of model %s came to no end (%s)", model_id, result.reason)
            return result.reason != "expired"
        await self._record(user_id, model_id, result)
        return True

    async def _record(self, user_id: str, model_id: str, result: JobResult) -> None:
        """Keeps the result while the model is there with the manifest the job ran with."""
        async with self._db.sessions() as session:
            store = ModelStore(session)
            await store.lock_user(user_id)
            current = await store.check_input(user_id, model_id)
            if current is None or manifest_sha256(current[1]) != result.manifest_sha256:
                logger.info("The check of model %s ran with a manifest it no longer has", model_id)
                return
            verified = result.status == "done" and result.verified is True
            await store.record_check(
                model_id,
                checked_at=_now(),
                verified=verified,
                error=result.error if result.status == "failed" else None,
            )
            await store.commit()
        logger.info(
            "Model %s was checked: %s",
            model_id,
            "verified" if verified else result.reason or "not verified",
        )
