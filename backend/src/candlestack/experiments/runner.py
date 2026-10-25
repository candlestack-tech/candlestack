"""The runs of the backtests: each in a task of the API process, outside the request that
started it, so it outlives the request (docs/architecture.md, "Backtests").

A run loads the candles with the model's warm-up from the data module, has the worker predict
on them through the job queue (``jobs.JobClient``), aligns the predictions with the candles,
applies the decision's steps, runs the engine and the statistics (``assembly.run``) and stores
the result. Whatever fails marks the backtest failed with a plain error for the user; a bug of
ours is logged as an error (a Sentry issue) and the user is told so. A run cannot hold a
database connection while the worker runs: it reads the backtest, lets the session go, and
writes the result in another.

The API runs in one process (granian with one worker), so a backtest still ``running`` when the
process starts was left by one that stopped: ``recover`` marks those failed, and ``close`` on
shutdown fails the runs it stops.
"""

import asyncio
import logging
import time
from datetime import UTC, datetime
from functools import partial

import anyio
import polars as pl
from redis.exceptions import RedisError

from candlestack.core import DATABASE_ERRORS, Database
from candlestack.data import CandleSet, DataError, DataService, InstrumentId, Timeframe
from candlestack.experiments.assembly import (
    NothingToTest,
    Run,
    align_of,
    equity_points,
    predictions_parquet,
    rows_of,
    run,
    stats_out,
)
from candlestack.experiments.document import Loaded, PredictionsOut, RunnerOut
from candlestack.experiments.models import Backtest
from candlestack.experiments.schemas import EQUITY_POINTS, BacktestStatus
from candlestack.experiments.spec import RunReport, RunSpec
from candlestack.experiments.store import BacktestStore
from candlestack.jobs import DEFAULT_WAIT, JobClient, JobResult

logger = logging.getLogger(__name__)

RESTARTED = "The server restarted while the backtest ran, so it stopped. Run it again."
INTERNAL = (
    "The backtest failed because of a bug of CandleStack, which was reported. Try again later."
)
UNREACHABLE = (
    "The worker that runs the models cannot be reached right now. Try again in a few minutes."
)
JOB_FAILED = "The model could not run on the candles. Try again later."
MODEL_GONE = "The model was deleted before the backtest could run it."


class RunFailed(Exception):
    """A run that cannot go on: ``reason`` names why for the log, ``message`` tells the user."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.message = message


def _now() -> datetime:
    return datetime.now(UTC)


def _ms(since: float) -> float:
    return round((time.monotonic() - since) * 1000, 1)


class BacktestRunner:
    """Starts the runs of the backtests and keeps their tasks until they end."""

    def __init__(
        self, db: Database, data: DataService, jobs: JobClient, *, wait: float = DEFAULT_WAIT
    ) -> None:
        self._db = db
        self._data = data
        self._jobs = jobs
        self._wait = wait
        # The runs going on, by their backtest.
        self._tasks: dict[asyncio.Task[None], str] = {}

    async def ping(self) -> None:
        """Raises ``redis.exceptions.RedisError`` when the worker's queue cannot be reached."""
        await self._jobs.ping()

    def start(self, backtest_id: str) -> None:
        """Runs the backtest, which must be stored as running, in a task of its own."""
        task = asyncio.create_task(self._run(backtest_id), name=f"backtest {backtest_id}")
        self._tasks[task] = backtest_id
        task.add_done_callback(self._ended)

    def _ended(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)

    async def recover(self) -> None:
        """Marks failed the backtests a stopped process left running. Postgres out of reach is
        a warning: they stay running until the next start."""
        try:
            async with self._db.sessions() as session:
                store = BacktestStore(session)
                left = await store.fail(RESTARTED, _now())
                await store.commit()
        except DATABASE_ERRORS as error:
            logger.warning("The backtests left running were not checked (%s)", type(error).__name__)
            return
        if left:
            logger.warning("%d backtests left running by a stopped process failed", left)

    async def close(self) -> None:
        """Stops the runs and marks their backtests failed (also a run that had not begun)."""
        stopped = dict(self._tasks)
        for task in stopped:
            task.cancel()
        await asyncio.gather(*stopped, return_exceptions=True)
        for backtest_id in stopped.values():
            await self._fail(backtest_id, RESTARTED)

    async def _run(self, backtest_id: str) -> None:
        try:
            await self._execute(backtest_id)
        except RunFailed as failure:
            logger.info("Backtest %s failed (%s)", backtest_id, failure.reason)
            await self._fail(backtest_id, failure.message)
        except Exception:
            logger.exception("Backtest %s failed with an error of CandleStack", backtest_id)
            await self._fail(backtest_id, INTERNAL)

    async def _execute(self, backtest_id: str) -> None:
        async with self._db.sessions() as session:
            backtest = await BacktestStore(session).run_of(backtest_id)
        if backtest is None or backtest.status != BacktestStatus.running.value:
            return
        spec = RunSpec.model_validate(backtest.spec)
        if backtest.model_id is None:
            raise RunFailed("model-deleted", MODEL_GONE)
        timings: dict[str, float | None] = {}
        clock = time.monotonic()
        candles = await self._candles(spec)
        timings["candles"] = _ms(clock)
        # The file as the model storage keeps it (ml.LocalModelStorage).
        model = f"{backtest.user_id}/{backtest.model_id}.keras"
        result, predictions = await self._predict(backtest_id, model, spec, candles.marked_frame())
        timings.update({f"job_{name}": value for name, value in result.timings})
        clock = time.monotonic()
        period = spec.data.period
        stored = predictions.filter(pl.col("ts").is_between(period.start, period.end - 1))
        done = await _backtest(spec, candles.frame, stored)
        timings["backtest"] = _ms(clock)
        blob, digest = predictions_parquet(stored)
        report = RunReport(
            loaded=Loaded(
                start=candles.start,
                end=candles.end,
                count=candles.frame.height,
                fingerprint=candles.fingerprint,
                gaps_total=candles.gaps_total,
            ),
            dropped=result.dropped,
            rows=rows_of(done),
            predictions=PredictionsOut(count=stored.height, sha256=digest),
            align=align_of(done),
            runner=_runner(spec, result),
            timings=timings,
        )
        await self._finish(backtest_id, done, report, blob)

    async def _candles(self, spec: RunSpec) -> CandleSet:
        data = spec.data
        try:
            return await self._data.candles(
                InstrumentId.parse(data.instrument.id),
                Timeframe(data.timeframe),
                data.load_start,
                data.period.end,
            )
        except DataError as error:
            raise RunFailed("candles", error.detail) from error

    async def _predict(
        self, backtest_id: str, model: str, spec: RunSpec, candles: pl.DataFrame
    ) -> tuple[JobResult, pl.DataFrame]:
        """The worker's predictions on the candles of the model's file ``model``."""
        try:
            result, predictions = await self._jobs.predict(
                model=model,
                sha256=spec.model.sha256,
                manifest=spec.model.manifest,
                candles=candles,
                wait=self._wait,
            )
        except (RedisError, OSError) as error:
            logger.warning("A backtest could not reach the worker (%s)", type(error).__name__)
            raise RunFailed("worker-unreachable", UNREACHABLE) from error
        if result.status != "done" or predictions is None:
            # A model deleted while the run went on takes its file along: the worker cannot
            # find it, and the user should read why.
            if await self._model_gone(backtest_id):
                raise RunFailed("model-deleted", MODEL_GONE)
            raise RunFailed(f"job {result.reason}", result.error or JOB_FAILED)
        return result, predictions

    async def _model_gone(self, backtest_id: str) -> bool:
        async with self._db.sessions() as session:
            return await BacktestStore(session).model_of(backtest_id) is None

    async def _finish(self, backtest_id: str, done: Run, report: RunReport, blob: bytes) -> None:
        """Stores the results while the backtest is still running and there (its account may
        have been deleted meanwhile)."""
        now = _now()
        async with self._db.sessions() as session:
            backtest = await session.get(Backtest, backtest_id, with_for_update=True)
            if backtest is None or backtest.status != BacktestStatus.running.value:
                return
            shown = stats_out(done.stats)
            backtest.status = BacktestStatus.finished.value
            backtest.stats = shown
            backtest.report = report.model_dump(mode="json")
            backtest.predictions = blob
            backtest.total_return = shown["total_return"]
            backtest.buy_and_hold_return = shown["buy_and_hold_return"]
            backtest.sharpe = shown["sharpe"]
            backtest.max_drawdown = shown["max_drawdown"]
            backtest.win_rate = shown["win_rate"]
            backtest.num_trades = int(shown["num_trades"] or 0)
            points = equity_points(done.result, done.rows, EQUITY_POINTS)
            if points is not None:
                backtest.set_equity(*points)
            backtest.engine_version = done.result.engine_version
            backtest.finished_at = now
            backtest.duration_s = (now - backtest.created_at).total_seconds()
            await session.commit()
        logger.info("Backtest %s finished in %.1f s", backtest_id, backtest.duration_s)

    async def _fail(self, backtest_id: str, error: str) -> None:
        try:
            async with self._db.sessions() as session:
                store = BacktestStore(session)
                await store.fail(error, _now(), backtest_id)
                await store.commit()
        except DATABASE_ERRORS as failure:
            # It stays running until the next start of the process marks it failed.
            logger.warning(
                "Backtest %s was not marked failed (%s)", backtest_id, type(failure).__name__
            )


async def _backtest(spec: RunSpec, candles: pl.DataFrame, predictions: pl.DataFrame) -> Run:
    """The engine's run on the period (CPU work, in a thread)."""
    try:
        return await anyio.to_thread.run_sync(
            partial(
                run,
                candles,
                predictions,
                period=spec.data.period,
                decision=spec.decision,
                execution=spec.execution,
                periods_per_year=spec.metrics.periods_per_year,
            )
        )
    except NothingToTest as error:
        raise RunFailed("no-predictions", str(error)) from error


def _runner(spec: RunSpec, result: JobResult) -> RunnerOut | None:
    if result.runner is None:
        return None
    runner = result.runner
    return RunnerOut(
        format=spec.model.framework,
        keras=runner.keras,
        backend=f"{runner.backend} {runner.backend_version}",
    )
