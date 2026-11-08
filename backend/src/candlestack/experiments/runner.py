"""The runs of the backtests: each in a task of the API process, outside the request that
started it, so it outlives the request (docs/architecture.md, "Backtests").

A run loads the candles with the model's warm-up from the data module (further back when the
warm-up fell short, ``_with_warm_up``), has the worker predict on them through the job queue
(``jobs.JobClient``), aligns the predictions with the candles, applies the decision's steps,
runs the engine and the statistics (``assembly.run``) and stores the result. It records its
steps as it goes (``steps.py``), so the backtest's page shows where it is. Whatever fails marks
the backtest failed with a plain error for the user, and the step it was at failed; a bug of
ours is logged as an error (a Sentry issue) and the user is told so.
A run cannot hold a database connection while the worker runs: it reads the backtest, lets the
session go, and writes the result in another.

The API runs in one process (granian with one worker), so a backtest that began to run before
the process started was left by one that stopped: ``recover`` marks those failed (again at each
check until Postgres answers), and ``close`` on shutdown fails the runs it stops.

The runner has ``slots`` run slots (``BACKTEST_SLOTS``) over all users, and a user's backtests
run one at a time: every other backtest waits in its user's queue in Postgres (``queued``),
which costs nothing while it waits. A free slot goes to the queues in turn (``fill``): of the
users with a queued backtest and none running, the one served longest ago (never, first; ties
to the one whose oldest backtest waits longest), whose oldest queued backtest starts. The slots
are filled when a backtest is stored (the request waits for it, so its answer says whether it
runs), when a run ends however it ends (``wake``), and every ``QUEUE_CHECK_EVERY`` seconds for
queues that stood still (Postgres out of reach). Queued backtests outlive a stop of the
process: ``recover`` fills the slots. As each running backtest has one job in the worker's
queue at most, the worker's queue holds as many jobs as there are slots, so one user's queue
never holds back another user's backtest, and a job's wait for a worker is short.

A wait (the MCP tools) learns of a backtest that changed here (``watch``): the runner tells it
when one starts, ends or is cancelled, so it reads the backtest again only then.
"""

import asyncio
import logging
import time
from collections.abc import Collection, Iterable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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
    TooManyCandles,
    WarmUpOutOfRange,
)
from candlestack.experiments.assembly import (
    NothingToTest,
    Run,
    Stored,
    align_of,
    equity_points,
    main_benchmark,
    predictions_parquet,
    results,
    rows_of,
    run,
    stats_out,
    stored_results,
)
from candlestack.experiments.document import Loaded, PredictionsOut, RunnerOut
from candlestack.experiments.models import Backtest
from candlestack.experiments.schemas import EQUITY_POINTS, BacktestStatus
from candlestack.experiments.spec import RunReport, RunSpec, run_manifest
from candlestack.experiments.steps import StepRecorder, Steps
from candlestack.experiments.store import BacktestStore
from candlestack.jobs import DEFAULT_WAIT, QUEUE_WAIT, JobClient, JobResult
from candlestack.ml import model_file
from candlestack.preprocessing import InvalidManifest, Manifest, build_input, parse_manifest

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
# How many backtests run at once by default, over all users (``BACKTEST_SLOTS``).
SLOTS = 2
# How long a backtest may be running without a run of the process before a check queues it
# again, in seconds: far longer than a turn takes from its commit to its run.
ORPHAN_GRACE = 60.0
# How many times a run loads further back when its warm-up fell short, each time twice as many
# candles before the period: 403 estimated candles become up to 3224.
LONGER_WARM_UPS = 3


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
    """Gives the run slots to the queues in turn, starts the runs of the backtests and keeps
    their tasks until they end."""

    def __init__(  # noqa: PLR0913 (what the runner is set up with)
        self,
        db: Database,
        data: DataService,
        jobs: JobClient,
        *,
        slots: int = SLOTS,
        wait: float = DEFAULT_WAIT,
        queue_wait: float = QUEUE_WAIT,
        check_every: float = QUEUE_CHECK_EVERY,
    ) -> None:
        self._db = db
        self._data = data
        self._jobs = jobs
        self._slots = slots
        self._wait = wait
        self._queue_wait = queue_wait
        self._check_every = check_every
        self._orphan_grace = ORPHAN_GRACE
        # The runs going on, by their backtest: each holds a slot.
        self._tasks: dict[asyncio.Task[None], str] = {}
        self._watch: asyncio.Task[None] | None = None
        self._closed = False
        # A backtest that began to run before this was left by a stopped process.
        self._born = _now()
        self._recovered = False
        # The backtests put back in their queue after ``close``.
        self._requeues: set[asyncio.Task[None]] = set()
        # One fill at a time; ``_woken`` while a fill is asked for and has not begun.
        self._filling = asyncio.Lock()
        self._woken = False
        self._wakeups: set[asyncio.Task[None]] = set()
        # The turn each user was last served in (a slot given to their queue), for the order
        # of the turns; the turns are counted from the start of the process.
        self._served: dict[str, int] = {}
        self._turns = 0
        # The waits for a backtest to change, by its ID (``watch``).
        self._watchers: dict[str, set[asyncio.Event]] = {}

    async def ping(self) -> None:
        """Raises ``redis.exceptions.RedisError`` when the worker's queue cannot be reached."""
        await self._jobs.ping()

    @property
    def free(self) -> bool:
        """Whether a run slot is free now."""
        return len(self._tasks) < self._slots

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
        self.changed(backtest_id)

    def stop(self, backtest_ids: Collection[str]) -> None:
        """Cancels the runs of the backtests, deleted while they ran: no failure is stored, and
        each slot goes to the next queue once its task has ended (``_ended``). The worker may
        still finish a job it took; nothing reads its answer. The waits for them read again."""
        for task, backtest_id in list(self._tasks.items()):
            if backtest_id in backtest_ids:
                task.cancel()
        for backtest_id in backtest_ids:
            self.changed(backtest_id)

    def _ended(self, task: asyncio.Task[None]) -> None:
        """Frees the slot of the run that ended, and gives it to the next queue."""
        self._tasks.pop(task, None)
        self.wake()

    # --- The waits --------------------------------------------------------------------------

    @contextmanager
    def watch(self, backtest_ids: Iterable[str]) -> Iterator[asyncio.Event]:
        """An event set whenever one of the backtests starts, ends or is cancelled, while the
        block runs: a wait clears it, reads the backtests, and waits for it. Only for the runs
        of this process, which is the only one (a wait still reads again now and then)."""
        event = asyncio.Event()
        watched = set(backtest_ids)
        for backtest_id in watched:
            self._watchers.setdefault(backtest_id, set()).add(event)
        try:
            yield event
        finally:
            for backtest_id in watched:
                waiting = self._watchers.get(backtest_id)
                if waiting is not None:
                    waiting.discard(event)
                    if not waiting:
                        del self._watchers[backtest_id]

    def changed(self, backtest_id: str) -> None:
        """Tells the waits for the backtest that it changed."""
        for event in self._watchers.get(backtest_id, ()):
            event.set()

    # --- The slots and the queues ----------------------------------------------------------

    async def recover(self) -> None:
        """Marks failed the backtests a stopped process left running, fills the slots, and from
        now on does both every ``check_every`` seconds: moves on the queues that stand still,
        and fails those left running until Postgres answered."""
        self._watch = asyncio.create_task(self._keep_moving(), name="backtest queues")
        await self._check()

    async def _check(self) -> None:
        """Fails the backtests left running, until that once succeeded, then ``resume``.
        Postgres out of reach is a warning: the next check tries again, and meanwhile the
        queues stand still."""
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
        await self._requeue_orphans()
        await self.resume()

    async def _requeue_orphans(self) -> None:
        """Puts back at the head of their queues the backtests running for ``orphan_grace``
        seconds without a run of this process, the only one: they never began (nothing should
        leave one, this is the net). Under the lock of the fills, so none is taken meanwhile.
        Postgres out of reach is a warning: the next check tries again."""
        async with self._filling:
            ours = set(self._tasks.values())
            before = _now() - timedelta(seconds=self._orphan_grace)
            try:
                async with self._db.sessions() as session:
                    store = BacktestStore(session)
                    orphans = [
                        backtest_id
                        for backtest_id in await store.running_since(before)
                        if backtest_id not in ours
                    ]
                    for backtest_id in orphans:
                        await store.requeue(backtest_id)
                    await store.commit()
            except DATABASE_ERRORS as error:
                logger.warning(
                    "The backtests running without a run were not checked (%s)",
                    type(error).__name__,
                )
                return
        if orphans:
            logger.error("%d backtests ran without a run and were queued again", len(orphans))

    async def resume(self) -> None:
        """Gives the free slots to the queues in turn (``fill``)."""
        await self.fill()

    async def _keep_moving(self) -> None:
        while True:
            await asyncio.sleep(self._check_every)
            try:
                await self._check()
            except Exception:
                # A bug of ours (a Sentry issue); the next check still comes.
                logger.exception("The backtest queues could not be checked")

    def wake(self) -> None:
        """Fills the slots in a task of its own, soon; once for every call made before that
        fill began."""
        if self._closed or self._woken:
            return
        self._woken = True
        task = asyncio.create_task(self._fill_woken(), name="backtest slots")
        self._wakeups.add(task)
        task.add_done_callback(self._wakeups.discard)

    async def _fill_woken(self) -> None:
        try:
            await self.fill()
        except Exception:
            # A bug of ours (a Sentry issue); the next check fills them.
            logger.exception("The backtest slots could not be filled")

    async def fill(self) -> None:
        """Starts queued backtests while a slot is free, one queue's turn at a time. Postgres
        out of reach is a warning: the next check fills them."""
        async with self._filling:
            self._woken = False
            while not self._closed and len(self._tasks) < self._slots:
                # A turn taken is a run started: a cancel (the request went away) between the
                # two would leave a backtest running without a run.
                with anyio.CancelScope(shield=True):
                    try:
                        started = await self._take_turn()
                    except DATABASE_ERRORS as error:
                        logger.warning(
                            "The backtest queues were not checked (%s)", type(error).__name__
                        )
                        return
                    if started is None:
                        return
                    self.start(started)

    async def _take_turn(self) -> str | None:
        """Marks running the oldest queued backtest of the next user in turn and returns its ID;
        None when no queue can take a slot. Under the lock of each user it tries, so a start or
        a cancel of theirs cannot cross it."""
        async with self._db.sessions() as session:
            store = BacktestStore(session)
            users = await store.waiting()
            # Least recently served first; the order of ``waiting`` among equals.
            for user_id in sorted(users, key=lambda user: self._served.get(user, 0)):
                await store.lock_user(user_id)
                started = await store.take_next(user_id, _now())
                await store.commit()
                if started is not None:
                    self._turns += 1
                    self._served[user_id] = self._turns
                    return started.id
        return None

    async def close(self) -> None:
        """Stops the runs and marks their backtests failed (also a run that had not begun).
        Queued backtests stay queued, for the next start of the process."""
        self._closed = True
        for background in (self._watch, *self._wakeups):
            if background is not None:
                background.cancel()
                await asyncio.gather(background, return_exceptions=True)
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
        # Its slot goes to the next queue once the task is done (``_ended``).
        self.changed(backtest_id)

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
        # The light results, so reading them never runs the engine again (CPU work, in a thread).
        kept = await anyio.to_thread.run_sync(
            lambda: stored_results(results(done, spec.benchmarks, light=True, held=held))
        )
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
            benchmarks=kept.benchmarks,
        )
        await self._finish(backtest_id, done, report, kept, steps, blob=blob, held=held)

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
        instrument, timeframe = InstrumentId.parse(data.instrument.id), Timeframe(data.timeframe)
        await steps.start("candles")
        try:
            async with steps.loading():
                candles = await self._data.candles(
                    instrument, timeframe, data.load_start, data.period.end, background=True
                )
                candles = await self._with_warm_up(spec, candles)
        except SourceUnavailable as error:
            raise RunFailed("candles", _source_unavailable(error)) from error
        except DataError as error:
            raise RunFailed("candles", error.detail) from error
        held = await self._benchmark_candles(spec)
        await steps.loaded(candles.frame.height)
        return candles, held

    async def _with_warm_up(self, spec: RunSpec, candles: CandleSet) -> CandleSet:
        """The candles, loaded further back until the model's input has a sample on the
        period's first candle, so the engine's rows start there whatever the representation.

        The warm-up of rows that span several candles (Renko, Kagi) is an estimate: where the
        bricks or lines formed slowly before the period, the first sample falls on a later
        candle, and the run would start there, its buy and hold from a later close. Each time
        the run loads twice as many candles before the period (``LONGER_WARM_UPS`` times at
        most), and stops where the data has no more or the load would pass the maximum: the
        rows then start at the first prediction, as before.
        """
        data = spec.data
        instrument, timeframe = InstrumentId.parse(data.instrument.id), Timeframe(data.timeframe)
        try:
            manifest = parse_manifest(spec.model.manifest)
        except InvalidManifest:
            return candles  # the worker refuses it and says why
        for _ in range(LONGER_WARM_UPS):
            covered = partial(_covers, candles.marked_frame(), manifest, data.period.start)
            if await anyio.to_thread.run_sync(covered):
                break
            before = candles.frame.filter(pl.col("ts") < data.period.start).height
            try:
                start = await self._data.warm_up_start(
                    instrument, timeframe, data.period.start, data.period.end, 2 * max(before, 1)
                )
            except (WarmUpOutOfRange, TooManyCandles):
                break
            if start >= candles.start:
                break
            candles = await self._data.candles(
                instrument, timeframe, start, data.period.end, background=True
            )
        return candles

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
                # A backtest stored before the copy was cut still has the example in it.
                manifest=run_manifest(spec.model.manifest),
                candles=candles,
                wait=self._wait,
                queue_wait=self._queue_wait,
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

    async def _finish(  # noqa: PLR0913 (what a run stores)
        self,
        backtest_id: str,
        done: Run,
        report: RunReport,
        kept: Stored,
        steps: StepRecorder,
        *,
        blob: bytes,
        held: Mapping[str, pl.DataFrame],
    ) -> None:
        """Stores the results (``kept`` the light ones, ``blob`` the predictions), and the steps
        all done, while the backtest is still running and there (its account may have been
        deleted meanwhile). ``held`` has the candles of the index fund of each benchmark that
        holds one, by its ID."""
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
            backtest.series, backtest.trades = kept.series, kept.trades
            backtest.curves = kept.curves
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


def _covers(candles: pl.DataFrame, manifest: Manifest, start: int) -> bool:
    """Whether the model's input from ``candles`` has a sample on the first candle at or after
    ``start``; also True without such a candle, or when the input cannot be built (the worker
    then says why). A sample at a candle needs no later one, so the candles up to it do."""
    first = candles.filter(pl.col("ts") >= start).get_column("ts").head(1)
    if first.is_empty():
        return True
    try:
        made = build_input(candles.filter(pl.col("ts") <= first[0]), manifest)
    except ValueError:
        return True
    return made.ts.len() > 0 and made.ts[-1] == first[0]


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
