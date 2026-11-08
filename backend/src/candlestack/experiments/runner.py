"""The runs of the backtests: each in a task of the API process, outside the request that
started it, so it outlives the request (docs/architecture.md, "Backtests").

A run loads the candles with the model's warm-up from the data module, has the worker predict
on them through the job queue (``jobs.JobClient``), aligns the predictions with the candles,
applies the decision's steps, runs the engine and the statistics (``assembly.run``) and stores
the result. It records its steps as it goes (``steps.py``), so the backtest's page shows where
it is. Whatever fails marks the backtest failed with a plain error for the user, and the step
it was at failed; a bug of ours is logged as an error (a Sentry issue) and the user is told so.
A run cannot hold a database connection while the worker runs: it reads the backtest, lets the
session go, and writes the result in another.

The API runs in one process (granian with one worker), so a backtest that began to run before
the process started was left by one that stopped: ``recover`` marks those failed (again at each
check until Postgres answers), and ``close`` on shutdown fails the runs it stops.

A user's backtests run one at a time; the others wait in the user's queue (``queued``). When a
run ends, however it ends, the runner starts the oldest queued backtest of its user. Queued
backtests outlive a stop of the process: ``recover`` starts the first of each queue, and every
``QUEUE_CHECK_EVERY`` seconds ``resume`` starts those of a queue that stood still (its next
could not be started, Postgres out of reach). As each user has at most one run, and so one job
in the worker's queue, which takes the oldest job first, one user's queue never holds back
another user's backtest.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from functools import partial

import anyio
import polars as pl
from redis.exceptions import RedisError

from candlestack.core import DATABASE_ERRORS, Database
from candlestack.data import (
    CandleSet,
    DataError,
    DataService,
    InstrumentId,
    SourceUnavailable,
    Timeframe,
)
from candlestack.experiments.assembly import (
    NothingToTest,
    Run,
    align_of,
    equity_points,
    main_benchmark,
    predictions_parquet,
    rows_of,
    run,
    stats_out,
)
from candlestack.experiments.document import Loaded, PredictionsOut, RunnerOut
from candlestack.experiments.models import Backtest
from candlestack.experiments.schemas import EQUITY_POINTS, BacktestStatus
from candlestack.experiments.spec import RunReport, RunSpec
from candlestack.experiments.steps import StepRecorder, Steps
from candlestack.experiments.store import BacktestStore
from candlestack.jobs import DEFAULT_WAIT, JobClient, JobResult
from candlestack.ml import model_file

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
# How often the queues that stood still are moved on, in seconds.
QUEUE_CHECK_EVERY = 30.0


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
        self,
        db: Database,
        data: DataService,
        jobs: JobClient,
        *,
        wait: float = DEFAULT_WAIT,
        check_every: float = QUEUE_CHECK_EVERY,
    ) -> None:
        self._db = db
        self._data = data
        self._jobs = jobs
        self._wait = wait
        self._check_every = check_every
        # The runs going on, by their backtest.
        self._tasks: dict[asyncio.Task[None], str] = {}
        self._watch: asyncio.Task[None] | None = None
        self._closed = False
        # A backtest that began to run before this was left by a stopped process.
        self._born = _now()
        self._recovered = False
        # The backtests put back in their queue after ``close``.
        self._requeues: set[asyncio.Task[None]] = set()

    async def ping(self) -> None:
        """Raises ``redis.exceptions.RedisError`` when the worker's queue cannot be reached."""
        await self._jobs.ping()

    def start(self, backtest_id: str) -> None:
        """Runs the backtest, which must be stored as running, in a task of its own. Once
        ``close`` began, it goes back to the head of its queue instead, for the next start of
        the process."""
        if self._closed:
            requeue = asyncio.create_task(self._requeue(backtest_id), name=f"requeue {backtest_id}")
            self._requeues.add(requeue)
            requeue.add_done_callback(self._requeues.discard)
            return
        task = asyncio.create_task(self._run(backtest_id), name=f"backtest {backtest_id}")
        self._tasks[task] = backtest_id
        task.add_done_callback(self._ended)

    def _ended(self, task: asyncio.Task[None]) -> None:
        self._tasks.pop(task, None)

    async def recover(self) -> None:
        """Marks failed the backtests a stopped process left running, starts the first queued
        backtest of each user, and from now on does both every ``check_every`` seconds: moves
        on the queues that stand still, and fails those left running until Postgres answered.
        """
        self._watch = asyncio.create_task(self._keep_moving(), name="backtest queues")
        await self._check()

    async def _check(self) -> None:
        """Fails the backtests left running, until that once succeeded, then ``resume``.
        Postgres out of reach is a warning: the next check tries again, and meanwhile the
        queues of their users stand still."""
        if not self._recovered:
            try:
                async with self._db.sessions() as session:
                    store = BacktestStore(session)
                    # Only those that began before this process: its own runs go on.
                    left = await store.fail(RESTARTED, _now(), before=self._born)
                    await store.commit()
            except DATABASE_ERRORS as error:
                logger.warning(
                    "The backtests left running were not checked (%s)", type(error).__name__
                )
                return
            self._recovered = True
            if left:
                logger.warning("%d backtests left running by a stopped process failed", left)
        await self.resume()

    async def resume(self) -> None:
        """Starts the oldest queued backtest of each user who has one and none running.
        Postgres out of reach is a warning: the next call tries again."""
        try:
            async with self._db.sessions() as session:
                users = await BacktestStore(session).waiting()
        except DATABASE_ERRORS as error:
            logger.warning("The backtest queues were not checked (%s)", type(error).__name__)
            return
        for user_id in users:
            await self._next(user_id)

    async def _keep_moving(self) -> None:
        while True:
            await asyncio.sleep(self._check_every)
            try:
                await self._check()
            except Exception:
                # A bug of ours (a Sentry issue); the next check still comes.
                logger.exception("The backtest queues could not be checked")

    async def _next(self, user_id: str) -> None:
        """Starts the user's oldest queued backtest, unless one of theirs runs. Postgres out of
        reach is a warning: ``resume`` starts it later."""
        if self._closed:
            return
        try:
            async with self._db.sessions() as session:
                store = BacktestStore(session)
                await store.lock_user(user_id)
                started = await store.take_next(user_id, _now())
                await store.commit()
        except DATABASE_ERRORS as error:
            logger.warning(
                "The next backtest of a queue was not started (%s)", type(error).__name__
            )
            return
        if started is not None:
            self.start(started.id)

    async def _advance(self, backtest_id: str) -> None:
        """Starts the next backtest of the user of the one whose run ended."""
        try:
            async with self._db.sessions() as session:
                user_id = await BacktestStore(session).user_of(backtest_id)
        except DATABASE_ERRORS as error:
            logger.warning(
                "The queue of backtest %s was not moved on (%s)", backtest_id, type(error).__name__
            )
            return
        if user_id is not None:
            await self._next(user_id)

    async def close(self) -> None:
        """Stops the runs and marks their backtests failed (also a run that had not begun).
        Queued backtests stay queued, for the next start of the process."""
        self._closed = True
        if self._watch is not None:
            self._watch.cancel()
            await asyncio.gather(self._watch, return_exceptions=True)
        stopped = dict(self._tasks)
        for task in stopped:
            task.cancel()
        await asyncio.gather(*stopped, return_exceptions=True)
        for backtest_id in stopped.values():
            await self._fail(backtest_id, RESTARTED)
        await asyncio.gather(*self._requeues, return_exceptions=True)

    async def _requeue(self, backtest_id: str) -> None:
        """Puts the backtest that was to run back at the head of its queue, as it never began.
        Postgres out of reach is a warning: the next start of the process fails it."""
        try:
            async with self._db.sessions() as session:
                store = BacktestStore(session)
                await store.requeue(backtest_id)
                await store.commit()
        except DATABASE_ERRORS as error:
            logger.warning(
                "Backtest %s was not put back in its queue (%s)", backtest_id, type(error).__name__
            )

    async def _run(self, backtest_id: str) -> None:
        try:
            await self._execute(backtest_id)
        except RunFailed as failure:
            logger.info("Backtest %s failed (%s)", backtest_id, failure.reason)
            await self._fail(backtest_id, failure.message)
        except Exception:
            logger.exception("Backtest %s failed with an error of CandleStack", backtest_id)
            await self._fail(backtest_id, INTERNAL)
        # Not after a run that ``close`` cancelled: the process stops.
        await self._advance(backtest_id)

    async def _execute(self, backtest_id: str) -> None:
        async with self._db.sessions() as session:
            backtest = await BacktestStore(session).run_of(backtest_id)
        if backtest is None or backtest.status != BacktestStatus.running.value:
            return
        spec = RunSpec.model_validate(backtest.spec)
        if backtest.model_id is None:
            raise RunFailed("model-deleted", MODEL_GONE)
        steps = StepRecorder(partial(self._save_steps, backtest_id), spec.data.period.end)
        timings: dict[str, float | None] = {}
        clock = time.monotonic()
        candles, held = await self._candles(spec, steps)
        timings["candles"] = _ms(clock)
        # The file as the model storage keeps it (ml.LocalModelStorage).
        model = model_file(backtest.user_id, backtest.model_id, spec.model.framework)
        result, predictions = await self._predict(
            backtest_id, model, spec, candles.marked_frame(), steps
        )
        timings.update({f"job_{name}": value for name, value in result.timings})
        await steps.start("trades")
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
        await self._finish(backtest_id, done, report, blob, steps, held=held)

    async def _save_steps(self, backtest_id: str, steps: Steps) -> None:
        """Stores the run's steps; Postgres out of reach is a warning, as the run goes on."""
        try:
            async with self._db.sessions() as session:
                store = BacktestStore(session)
                await store.set_steps(backtest_id, steps)
                await store.commit()
        except DATABASE_ERRORS as error:
            logger.warning(
                "The steps of backtest %s were not saved (%s)", backtest_id, type(error).__name__
            )

    async def _candles(
        self, spec: RunSpec, steps: StepRecorder
    ) -> tuple[CandleSet, dict[str, pl.DataFrame]]:
        """The candles with the warm-up, loaded in the background: a cold load of years of
        stock candles waits for the source's request budget, minutes if need be; and those of
        the index fund each benchmark holds (``_benchmark_candles``)."""
        data = spec.data
        await steps.start("candles")
        try:
            async with steps.loading():
                candles = await self._data.candles(
                    InstrumentId.parse(data.instrument.id),
                    Timeframe(data.timeframe),
                    data.load_start,
                    data.period.end,
                    background=True,
                )
        except SourceUnavailable as error:
            raise RunFailed("candles", _source_unavailable(error)) from error
        except DataError as error:
            raise RunFailed("candles", error.detail) from error
        held = await self._benchmark_candles(spec)
        await steps.loaded(candles.frame.height)
        return candles, held

    async def _benchmark_candles(self, spec: RunSpec) -> dict[str, pl.DataFrame]:
        """The candles of the index fund each benchmark holds, of the period, by the benchmark's
        ID, loaded in the background. The run stores only the fund's return and short equity
        series from them, but a fund that cannot be loaded fails the run here rather than every
        view of its document, which then finds them in the cache."""
        data = spec.data
        held: dict[str, pl.DataFrame] = {}
        for benchmark, etf in spec.etf_benchmarks().items():
            failed = f"The candles of the benchmark {etf.symbol} could not be loaded."
            try:
                loaded = await self._data.candles(
                    etf,
                    Timeframe(data.timeframe),
                    data.period.start,
                    data.period.end,
                    background=True,
                )
            except SourceUnavailable as error:
                source = f"The market data source ({error.source.capitalize()}) is unavailable."
                raise RunFailed("benchmark-candles", f"{failed} {source} {error.detail}") from error
            except DataError as error:
                raise RunFailed("benchmark-candles", f"{failed} {error.detail}") from error
            held[benchmark] = loaded.frame
        return held

    async def _predict(
        self,
        backtest_id: str,
        model: str,
        spec: RunSpec,
        candles: pl.DataFrame,
        steps: StepRecorder,
    ) -> tuple[JobResult, pl.DataFrame]:
        """The worker's predictions on the candles of the model's file ``model``: the queue's
        step until a worker takes the job, then the model's."""
        await steps.start("queue")
        try:
            result, predictions = await self._jobs.predict(
                model=model,
                sha256=spec.model.sha256,
                manifest=spec.model.manifest,
                candles=candles,
                wait=self._wait,
                claimed=steps.claimed,
            )
        except (RedisError, OSError) as error:
            logger.warning("A backtest could not reach the worker (%s)", type(error).__name__)
            raise RunFailed("worker-unreachable", UNREACHABLE) from error
        await steps.answered(result.timings)
        if result.status != "done" or predictions is None:
            # A model deleted while the run went on takes its file along: the worker cannot
            # find it, and the user should read why.
            if await self._model_gone(backtest_id):
                raise RunFailed("model-deleted", MODEL_GONE)
            raise RunFailed(f"job {result.reason}", result.error or JOB_FAILED)
        await steps.ran(result.timings)
        return result, predictions

    async def _model_gone(self, backtest_id: str) -> bool:
        async with self._db.sessions() as session:
            return await BacktestStore(session).model_of(backtest_id) is None

    async def _finish(
        self,
        backtest_id: str,
        done: Run,
        report: RunReport,
        blob: bytes,
        steps: StepRecorder,
        *,
        held: Mapping[str, pl.DataFrame],
    ) -> None:
        """Stores the results, and the steps all done, while the backtest is still running and
        there (its account may have been deleted meanwhile). ``held`` has the candles of the
        index fund of each benchmark that holds one, by its ID."""
        benchmark, benchmark_return = main_benchmark(done, held)
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
            backtest.benchmark_return = benchmark_return
            backtest.sharpe = shown["sharpe"]
            backtest.max_drawdown = shown["max_drawdown"]
            backtest.win_rate = shown["win_rate"]
            backtest.num_trades = int(shown["num_trades"] or 0)
            points = equity_points(done.result, done.rows, EQUITY_POINTS, benchmark)
            if points is not None:
                backtest.set_equity(*points)
            backtest.engine_version = done.result.engine_version
            backtest.finished_at = now
            # The time it ran: the wait in the queue left out.
            began = backtest.started_at or backtest.created_at
            backtest.duration_s = (now - began).total_seconds()
            backtest.steps = steps.finished()
            await session.commit()
        logger.info("Backtest %s finished in %.1f s", backtest_id, backtest.duration_s)

    async def _fail(self, backtest_id: str, error: str) -> None:
        """Marks the backtest failed, and the step it was at as stored (``store.fail``)."""
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


def _source_unavailable(error: SourceUnavailable) -> str:
    """The message of a load the source failed: which source, then its own detail, which says
    what happened and when to try again."""
    return (
        f"The market data source ({error.source.capitalize()}) is unavailable, so the candles "
        f"could not be loaded. {error.detail}"
    )


def _runner(spec: RunSpec, result: JobResult) -> RunnerOut | None:
    if result.runner is None:
        return None
    runner = result.runner
    return RunnerOut(
        format=spec.model.framework,
        keras=runner.keras,
        backend=f"{runner.backend} {runner.backend_version}",
    )
