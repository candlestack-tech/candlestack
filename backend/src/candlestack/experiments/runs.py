"""Starting a backtest and building its result document: what ``POST /api/v1/backtests`` checks
before it accepts a run, the document ``GET /api/v1/backtests/{id}`` builds from what the run
stored (docs/engine.md, "The result document"), and cancelling a queued backtest.

A user's backtests run one at a time: a new one waits in the user's queue (``queued``, at most
``store.QUEUE_MAX``) while another of theirs runs or no run slot is free, and the runner starts
the oldest when its turn comes (``BacktestRunner``). Where a backtest stands is read from its
row alone (``statuses``), with an estimate of when it ends from the user's recent runs.

The light view (``light``) is what the run stored: the statistics, and its light series and
trades as zstd Parquet (``assembly.stored_results``), so reading it loads no candles and runs no
engine. A backtest that finished before those were stored builds them once from the candles and
stores them. The download is built on request: the stored predictions, the candles from the
cache and the engine, which is deterministic. When the candles' fingerprint differs from the
run's (a source adjusted old prices), ``data.changed`` says so and the results come from the
candles as they are now.
"""

import logging
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from functools import partial
from typing import Any, NamedTuple

import anyio
import orjson
import polars as pl
from redis.exceptions import RedisError

from candlestack.core import DATABASE_ERRORS
from candlestack.data import (
    CandleSet,
    DataService,
    InstrumentId,
    InstrumentInfo,
    InvalidRequest,
    Market,
    Timeframe,
)
from candlestack.engine import ENGINE_VERSION
from candlestack.experiments.assembly import (
    PERIODS_PER_YEAR,
    RISK_FREE_RATE,
    NothingToTest,
    Run,
    align_of,
    document,
    dumps,
    read_predictions,
    read_series,
    read_trades,
    results,
    rows_of,
    run,
    stored_results,
)
from candlestack.experiments.document import (
    BENCHMARK_ETFS,
    LIGHT_ROWS,
    BacktestCheck,
    BacktestHeader,
    BacktestIn,
    BacktestModel,
    BenchmarkIn,
    BenchmarkOut,
    CheckedModel,
    CheckedWarmUp,
    DataOut,
    DocumentAuthor,
    HysteresisRule,
    InstrumentOut,
    MetricsArgs,
    SeriesColumns,
    Software,
    ThresholdRule,
    TradeColumns,
    WarmUpReport,
)
from candlestack.experiments.errors import (
    BacktestLimitReached,
    BacktestNotFinished,
    BacktestNotFound,
    BacktestNotQueued,
    BacktestsUnavailable,
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    ModelNotFound,
    NoDocument,
    NotComparable,
    QueueFull,
)
from candlestack.experiments.idempotency import Committed
from candlestack.experiments.models import Backtest, new_backtest_id
from candlestack.experiments.runner import BacktestRunner
from candlestack.experiments.schemas import (
    BacktestSetup,
    BacktestStatus,
    Representation,
    SetupBenchmark,
    SetupDirection,
    SetupTimeframe,
)
from candlestack.experiments.spec import (
    RunReport,
    RunSpec,
    SpecData,
    SpecModel,
    SpecWarmUp,
    run_manifest,
)
from candlestack.experiments.steps import pending, shown_steps
from candlestack.experiments.store import QUEUE_MAX, BacktestStore, Compared, StatusRow
from candlestack.ml import Model
from candlestack.preprocessing import Manifest, warm_up

# The sessions of the candles each market has: a model that reads US trading hours runs on
# stocks, one that reads around the clock on crypto.
_SESSIONS = {"stock": "regular", "crypto": "24/7"}
# The representations the Backtests list names, by the catalog block's name; time windows of
# candles (candles@1) for any other.
_REPRESENTATIONS = {"renko": Representation.renko, "kagi": Representation.kagi}
CANCELLED = "You cancelled the backtest before it ran."
logger = logging.getLogger(__name__)
# What an estimate counts a run of a user without a finished one as, in seconds.
RUN_SECONDS = 60.0
_ENDED = {BacktestStatus.finished, BacktestStatus.failed}


class Standing(NamedTuple):
    """Where a backtest stands (``BacktestRuns.statuses``): the ``backtest`` group of its
    document and ``eta_s``, about how many seconds until it ends, from its place in its user's
    queue and the median of their recent runs; None once it ended, for another account's, and
    before the user had a run finish."""

    header: BacktestHeader
    eta_s: float | None


class Prepared(NamedTuple):
    """A new backtest after the reads of its checks (``BacktestRuns.prepare``): the user's
    model, the manifest it runs with, the instrument and the body with its benchmarks checked."""

    model: Model
    manifest: Manifest
    instrument_id: InstrumentId
    body: BacktestIn

    @property
    def representation(self) -> Representation:
        """What the model reads, as the app names it."""
        return Representation(_representation(self.manifest))


class BacktestRuns:
    def __init__(
        self,
        store: BacktestStore,
        data: DataService,
        runner: BacktestRunner,
        app_version: str,
        *,
        limit: int,
    ) -> None:
        self._store = store
        self._data = data
        self._runner = runner
        self._app_version = app_version
        # The most backtests an account keeps (``BACKTEST_LIMIT``).
        self._limit = limit

    async def check(self, user_id: str, body: BacktestIn) -> BacktestCheck:
        """Checks a new backtest of the user as ``start`` does, stores nothing, and says what
        would run and whether it would wait in the user's queue.

        Raises:
            The errors of ``start`` but none of the lock (the model deleted meanwhile).
        """
        prepared = await self.prepare(user_id, body)
        model = prepared.model
        # As in ``start``, before the slower checks.
        if await self._store.queued(user_id) >= QUEUE_MAX:
            raise QueueFull(await self.retry_after(user_id))
        await self.fits(user_id, 1)
        await self.ready()
        spec = await self.spec(prepared)
        queued = await self._store.queued(user_id)
        waits = queued > 0 or not self._runner.free or await self._store.running(user_id)
        data = spec.data
        return BacktestCheck(
            model=CheckedModel(id=model.id, name=model.name, framework=model.framework),
            instrument=data.instrument,
            timeframe=data.timeframe,
            session=data.session,
            period=data.period,
            warm_up=CheckedWarmUp(
                candles=data.warm_up.candles,
                kind="estimate" if data.warm_up.kind == "estimate" else "exact",
                load_start=data.load_start,
            ),
            benchmarks=[
                BenchmarkOut.model_validate(benchmark.model_dump()) for benchmark in spec.benchmarks
            ],
            starts="queued" if waits else "now",
            queue_position=queued + 1 if waits else None,
        )

    async def start(
        self, user_id: str, body: BacktestIn, committed: Committed | None = None
    ) -> tuple[Backtest, bytes]:
        """Checks a new backtest of the user, stores it, and returns it with its document: it
        runs at once when no other backtest of the user runs or waits and a run slot is free,
        else it waits at the end of their queue (``queued``). ``committed`` is awaited with its
        ID once it is stored (an idempotency key keeps it, ``idempotency.once``).

        From its commit on nothing cancels the start (a client that went away): the new
        backtest gets its slot or stays queued, never running without a run. A read that fails
        after the commit answers it as stored, queued and without its place.

        Raises:
            InvalidBacktest, ModelNotFound, ModelNeedsSetup, InstrumentNotForModel, QueueFull,
            BacktestLimitReached, BacktestsUnavailable, and the data module's errors of the
            instrument and the period (``data.DataError``).
        """
        prepared = await self.prepare(user_id, body)
        author = DocumentAuthor(id=user_id, name=await self._store.author_name(user_id), mine=True)
        # Before the slower checks; again under the lock below, where it counts.
        if await self._store.queued(user_id) >= QUEUE_MAX:
            raise QueueFull(await self.retry_after(user_id))
        await self.fits(user_id, 1)
        await self.ready()
        spec = await self.spec(prepared)
        await self._store.lock_user(user_id)
        if await self._store.queued(user_id) >= QUEUE_MAX:
            raise QueueFull(await self.retry_after(user_id))
        await self.fits(user_id, 1)
        # The model may have been deleted meanwhile; held now until the backtest is stored.
        if not await self._store.hold_model(user_id, prepared.model.id):
            raise ModelNotFound
        backtest = self.new_backtest(
            user_id, prepared, spec, number=await self._store.last_number(user_id) + 1
        )
        await self._store.add_named(backtest)
        with anyio.CancelScope(shield=True):
            await self._store.commit()
            if committed is not None:
                await committed(backtest.id)
            await self.schedule()
            position = await self._stored_place(backtest)
        document = _document(backtest, spec, author=author, queue_position=position)
        return backtest, dumps(document)

    async def fits(self, user_id: str, adding: int) -> None:
        """Checks that the user can keep ``adding`` more backtests, failed ones counted.

        Raises:
            BacktestLimitReached.
        """
        count = await self._store.count(user_id, [])
        if count + adding > self._limit:
            raise BacktestLimitReached(self._limit, count, adding)

    async def _stored_place(self, backtest: Backtest) -> int | None:
        """Reads the new backtest's status again (running now when a slot was free for it)
        and its place in the queue. Postgres out of reach is a warning: it is answered queued,
        as stored, without its place."""
        try:
            await self._store.refresh_status(backtest)
            position = await self._store.queue_position(backtest.id)
            await self._store.commit()
        except DATABASE_ERRORS as error:
            logger.warning(
                "Backtest %s was answered as stored (%s)", backtest.id, type(error).__name__
            )
            return None
        return position

    async def prepare(self, user_id: str, body: BacktestIn) -> Prepared:
        """The reads of a new backtest's checks: its benchmarks, and the user's model with the
        manifest it runs with on the instrument.

        Raises:
            InvalidBacktest, ModelNotFound, ModelNeedsSetup, InstrumentNotForModel.
        """
        instrument_id = InstrumentId.parse(body.data.instrument.id)
        body = body.model_copy(
            update={"benchmarks": _check_benchmarks(body.benchmarks, instrument_id)}
        )
        model = await self._store.run_model(user_id, body.model.id)
        if model is None:
            raise ModelNotFound
        return Prepared(model, _manifest(model, instrument_id), instrument_id, body)

    async def ready(self) -> None:
        """Ends the reads, so no connection waits on the queue and the data module, and checks
        that the worker's queue answers.

        Raises:
            BacktestsUnavailable.
        """
        await self._store.commit()
        try:
            await self._runner.ping()
        except RedisError as error:
            raise BacktestsUnavailable from error

    def new_backtest(
        self,
        user_id: str,
        prepared: Prepared,
        spec: RunSpec,
        *,
        number: int,
        sweep_id: str | None = None,
    ) -> Backtest:
        """The row of a new backtest, at the end of the user's queue (``queued``): it runs at
        once when it is the queue's first."""
        model, manifest, instrument_id = prepared.model, prepared.manifest, prepared.instrument_id
        return Backtest(
            id=new_backtest_id(),
            user_id=user_id,
            number=number,
            status=BacktestStatus.queued.value,
            instruments=[{"market": instrument_id.market, "symbol": instrument_id.symbol}],
            representation=_representation(manifest),
            model_id=model.id,
            model_name=model.name,
            model_framework=model.framework,
            setup=_setup(spec),
            spec=spec.model_dump(mode="json"),
            input_shape=list(manifest.signature.input.shape),
            steps=pending(),
            sweep_id=sweep_id,
            created_at=datetime.now(UTC),
        )

    async def schedule(self) -> None:
        """Gives the free run slots to the queues in turn (``BacktestRunner.fill``), once the
        new backtests are committed: the new one starts when it is its turn."""
        await self._runner.fill()

    async def retry_after(self, user_id: str, places: int = 1) -> int:
        """About how many seconds until ``places`` more backtests fit in the user's queue: one
        frees a place when it starts, about each run of theirs (the median of their recent
        runs, else ``RUN_SECONDS``)."""
        seconds = await self._store.run_seconds(user_id)
        return max(1, math.ceil(places * (RUN_SECONDS if seconds is None else seconds)))

    async def cancel(self, user_id: str, backtest_id: str) -> bytes:
        """Cancels the user's queued backtest: it fails, as cancelled, and never runs. Returns
        its document.

        Raises:
            BacktestNotFound, BacktestNotQueued (it runs or has ended).
        """
        # The runner cannot start it between the check and the commit.
        await self._store.lock_user(user_id)
        found = await self._store.backtest(user_id, backtest_id)
        if found is None:
            raise BacktestNotFound
        backtest = found.backtest
        if backtest.status != BacktestStatus.queued.value:
            raise BacktestNotQueued(backtest.status)
        await self._store.cancel(backtest, CANCELLED, datetime.now(UTC))
        await self._store.commit()
        self._runner.changed(backtest_id)
        _, content = await self.document(user_id, backtest_id, light=True)
        return content

    async def spec(self, prepared: Prepared) -> RunSpec:
        """The setup to store: the model with its manifest, the instrument as the catalog has
        it and where the load with the warm-up starts (the period checked against the data, of
        the index fund a benchmark holds too).

        Raises:
            The data module's errors of the instrument and the period (``data.DataError``).
        """
        model, manifest, instrument_id, body = prepared
        timeframe = Timeframe(manifest.input.timeframe)
        needed = warm_up(manifest)
        period = body.data.period
        info = await self._data.instrument(instrument_id)
        load_start = await self._data.warm_up_start(
            instrument_id, timeframe, period.start, period.end, needed.candles
        )
        spec = RunSpec(
            model=SpecModel(
                name=model.name,
                framework=model.framework,
                sha256=model.sha256.hex(),
                size_bytes=model.size_bytes,
                manifest=run_manifest(model.manifest or {}),
            ),
            data=SpecData(
                instrument=_instrument(info),
                timeframe=timeframe.value,
                timeframe_seconds=timeframe.seconds,
                session=manifest.input.session,
                period=period,
                load_start=load_start,
                warm_up=SpecWarmUp(candles=needed.candles, kind=needed.kind),
            ),
            decision=body.decision,
            execution=body.execution,
            benchmarks=body.benchmarks,
            metrics=MetricsArgs(
                periods_per_year=PERIODS_PER_YEAR[instrument_id.market],
                risk_free_rate=RISK_FREE_RATE,
            ),
            app_version=self._app_version,
        )
        # A fund without candles in the period fails the start, not the run minutes later.
        for etf in spec.etf_benchmarks().values():
            await self._data.warm_up_start(etf, timeframe, period.start, period.end, 0)
        return spec

    async def document(
        self, user_id: str, backtest_id: str, *, light: bool, summary: bool = False
    ) -> tuple[int, bytes]:
        """The number and the result document of the user's backtest, or of another account's
        while it is published: the light view (``light``, with ``summary`` its statistics
        without the series and the trades, ``summarized``), or with ``light`` False the
        download, built again from the candles, which needs a finished backtest.

        Raises:
            BacktestNotFound, NoDocument, BacktestNotFinished (the download), and the data
            module's errors of loading the candles again (``data.DataError``): the download's,
            and those of the first read of a backtest from before the light results were
            stored.
        """
        if light:
            number, built = await self.light(
                user_id, backtest_id, series=not summary, trades=not summary
            )
            if summary:
                return number, dumps(summarized(built))
            # Up to about 1 MB of JSON: written in a thread too.
            return number, await anyio.to_thread.run_sync(dumps, built)
        found = await self._store.with_run(user_id, backtest_id)
        if found is None:
            raise BacktestNotFound
        backtest = found.backtest
        if backtest.spec is None:
            raise NoDocument
        if backtest.status != BacktestStatus.finished.value or backtest.report is None:
            raise BacktestNotFinished(backtest.status)
        spec = RunSpec.model_validate(backtest.spec)
        report = RunReport.model_validate(backtest.report)
        author = _author(backtest, found.author, user_id)
        blob, candles, held = await self._inputs(backtest, spec, report)
        build = partial(
            _finished, backtest, spec, report, candles, blob, author=author, light=False, held=held
        )
        return backtest.number, await anyio.to_thread.run_sync(build)

    async def light(
        self, user_id: str, backtest_id: str, *, series: bool = True, trades: bool = True
    ) -> tuple[int, dict[str, Any]]:
        """The number and the light result document of the user's backtest, or of another
        account's while it is published, from what its run stored, without the candles and
        the engine. Its ``results`` have the statistics and the benchmarks' returns, and the
        series and the trades when asked for (``series``, ``trades``), as NumPy arrays for
        ``dumps``. ``data.changed`` is null: only the download compares the candles again.

        A backtest that finished before its light results were stored builds them from the
        candles once and stores them; while its candles differ from the run's, it answers that
        build (``data.changed`` true) and stores nothing.

        Raises:
            BacktestNotFound, NoDocument, and the data module's errors of that build.
        """
        found = await self._store.with_run(user_id, backtest_id, series=series, trades=trades)
        if found is None:
            raise BacktestNotFound
        backtest = found.backtest
        if backtest.spec is None:
            raise NoDocument
        spec = RunSpec.model_validate(backtest.spec)
        author = _author(backtest, found.author, user_id)
        if backtest.status != BacktestStatus.finished.value or backtest.report is None:
            position = None
            if backtest.status == BacktestStatus.queued.value:
                position = await self._store.queue_position(backtest.id)
            return backtest.number, _document(
                backtest, spec, author=author, queue_position=position
            )
        report = RunReport.model_validate(backtest.report)
        if report.benchmarks is None:
            return backtest.number, await self._first_light(backtest, spec, report, author)
        shown: dict[str, Any] = {
            "stats": backtest.stats,
            "benchmarks": _returns(report.benchmarks),
        }
        if series or trades:
            read = partial(
                _read_light,
                backtest.series if series else None,
                backtest.trades if trades else None,
            )
            shown |= await anyio.to_thread.run_sync(read)
        return backtest.number, _stored_document(backtest, spec, report, shown, author)

    async def _first_light(
        self, backtest: Backtest, spec: RunSpec, report: RunReport, author: DocumentAuthor
    ) -> dict[str, Any]:
        """The light document of a backtest that finished before its light results were
        stored, built from the candles; stored only while the build gives the run's statistics
        (the candles are still the run's, and the engine counts as it did), so the stored
        series and trades never go with statistics of another build."""
        blob, candles, held = await self._inputs(backtest, spec, report)
        changed = candles.fingerprint != report.loaded.fingerprint
        build = partial(_again, spec, candles.frame, blob, held, light=True)
        done, shown = await anyio.to_thread.run_sync(build)
        if done is None or shown is None or changed or not _same_stats(shown, backtest):
            return _document(backtest, spec, report, done, shown, author=author, changed=changed)
        kept = await anyio.to_thread.run_sync(stored_results, shown)
        report = report.model_copy(update={"benchmarks": kept.benchmarks})
        await self._store.keep_results(
            backtest.id,
            series=kept.series,
            trades=kept.trades,
            curves=kept.curves,
            report=report.model_dump(mode="json"),
        )
        await self._store.commit()
        return _stored_document(backtest, spec, report, shown, author)

    async def _inputs(
        self, backtest: Backtest, spec: RunSpec, report: RunReport
    ) -> tuple[bytes, CandleSet, dict[str, pl.DataFrame]]:
        """What a build of a finished backtest's results runs on: its stored predictions, the
        candles it loaded, and those of the index fund of each benchmark that holds one."""
        blob = await self._store.predictions(backtest.id)
        assert blob is not None  # a finished run stored them
        # The reads end here, so no connection waits on the data module and the build.
        await self._store.commit()
        timeframe = Timeframe(spec.data.timeframe)
        candles = await self._data.candles(
            InstrumentId.parse(spec.data.instrument.id),
            timeframe,
            report.loaded.start,
            report.loaded.end,
        )
        period = spec.data.period
        held = {
            benchmark: (await self._data.candles(etf, timeframe, period.start, period.end)).frame
            for benchmark, etf in spec.etf_benchmarks().items()
        }
        return blob, candles, held

    async def compared(
        self, user_id: str, backtest_ids: Sequence[str], *, curves: bool
    ) -> dict[str, Compared]:
        """What the comparison reads of the backtests, the user's own and other accounts'
        published ones, by ID (``BacktestStore.compared``)."""
        return await self._store.compared(user_id, backtest_ids, curves=curves)

    async def header(self, user_id: str, backtest_id: str) -> BacktestHeader:
        """The ``backtest`` group of the document of the user's backtest, or of another
        account's while it is published, from its row alone: its status, its place in the queue
        and its steps, without building the results (what an agent polls).

        Raises:
            BacktestNotFound, NoDocument.
        """
        rows = await self._store.statuses(user_id, [backtest_id])
        if not rows:
            raise BacktestNotFound
        if not rows[0].has_spec:
            raise NoDocument
        return _status_header(rows[0], user_id)

    async def statuses(self, user_id: str, backtest_ids: Sequence[str]) -> list[Standing]:
        """Where each of the backtests stands, in the order given: the user's own or other
        accounts' published ones, from their rows alone (``BacktestStore.statuses``), with the
        estimate of when the user's own end. What a wait reads again and again.

        Raises:
            NotComparable: Neither the user nor a published backtest has one of the IDs.
        """
        rows = {row.id: row for row in await self._store.statuses(user_id, backtest_ids)}
        for backtest_id in backtest_ids:
            if backtest_id not in rows:
                raise NotComparable(backtest_id, found=False)
        headers = [_status_header(rows[backtest_id], user_id) for backtest_id in backtest_ids]
        going = [h for h in headers if h.author.mine and h.status not in _ENDED]
        run = await self._store.run_seconds(user_id) if going else None
        if run is None:
            return [Standing(header, None) for header in headers]
        running = any(h.status is BacktestStatus.running for h in going)
        if not running and any(h.status is BacktestStatus.queued for h in going):
            running = await self._store.running(user_id)
        now = datetime.now(UTC)
        return [Standing(header, _eta(header, run, running=running, now=now)) for header in headers]


def _author(backtest: Backtest, name: str, user_id: str) -> DocumentAuthor:
    """The document's author: only the account's display name and public ID, never its email."""
    return DocumentAuthor(id=backtest.user_id, name=name, mine=backtest.user_id == user_id)


def _status_header(row: StatusRow, user_id: str) -> BacktestHeader:
    """The ``backtest`` group of a document from where the backtest stands."""
    mine = row.user_id == user_id
    return BacktestHeader(
        id=row.id,
        name=row.name,
        generated_name=row.generated_name,
        number=row.number,
        author=DocumentAuthor(id=row.user_id, name=row.author, mine=mine),
        public=row.published_at is not None,
        published_at=row.published_at,
        status=BacktestStatus(row.status),
        queue_position=row.queue_position,
        # Only the author can open the sweep: others see that there was none.
        sweep_id=row.sweep_id if mine else None,
        created_at=row.created_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        duration_s=row.duration_s,
        error=row.error,
        steps=shown_steps(row.steps, row.timings),
    )


def _eta(header: BacktestHeader, run: float, *, running: bool, now: datetime) -> float | None:
    """About how many seconds until the user's backtest ends, each run taking ``run`` seconds:
    a running one what is left of its run (0 once past it); a queued one the backtests ahead of
    it in the queue, the user's running one (``running``) and its own run. The wait for a free
    run slot while many users run is left out. None for another account's or an ended one."""
    if not header.author.mine:
        return None
    if header.status is BacktestStatus.running:
        began = header.started_at or header.created_at
        return round(max(run - (now - began).total_seconds(), 0.0), 1)
    if header.status is BacktestStatus.queued and header.queue_position is not None:
        return round((header.queue_position + running) * run, 1)
    return None


def _returns(benchmarks: Mapping[str, float | None]) -> dict[str, dict[str, float | None]]:
    return {benchmark: {"total_return": value} for benchmark, value in benchmarks.items()}


def _read_light(series: bytes | None, trades: bytes | None) -> dict[str, Any]:
    """The stored light series and trades given (CPU work, in a thread)."""
    shown: dict[str, Any] = {}
    if series is not None:
        shown["series"] = read_series(series)
    if trades is not None:
        shown["trades"] = read_trades(trades)
    return shown


def _same_stats(shown: dict[str, Any], backtest: Backtest) -> bool:
    """Whether a build's statistics are the ones its run stored, as their JSON reads them."""
    return orjson.loads(dumps(shown["stats"])) == backtest.stats


def _stored_document(
    backtest: Backtest,
    spec: RunSpec,
    report: RunReport,
    shown: dict[str, Any],
    author: DocumentAuthor,
) -> dict[str, Any]:
    """The light document of a finished backtest from what its run stored."""
    built = _document(backtest, spec, report, shown=shown, author=author)
    # The engine that made what it stored.
    built["software"]["engine_version"] = backtest.engine_version or ENGINE_VERSION
    # The rows and the alignment the run found, as a build from the candles finds them again.
    built["data"]["rows"] = report.rows.model_dump(mode="json")
    built["data"]["align"] = report.align.model_dump(mode="json")
    return built


# The columns of the series and the trades, which the summary view leaves empty.
_SERIES_COLUMNS = tuple(SeriesColumns.model_fields)
_TRADE_COLUMNS = tuple(field.alias or name for name, field in TradeColumns.model_fields.items())


def summarized(built: dict[str, Any]) -> dict[str, Any]:
    """The summary view of a light document (``light`` without the series and the trades):
    its ``results`` have the statistics and the benchmarks' returns, and the series and the
    trades of the document's form left empty (``count`` 0; ``trades.total`` still how many
    trades the run had)."""
    shown = built["results"]
    if shown is None:
        return built
    day = built["data"]["rows"]["count"] > LIGHT_ROWS
    total = shown["stats"]["num_trades"]
    columns = [name for name in _SERIES_COLUMNS if not day or name not in ("prediction", "signal")]
    empty = {
        **shown,
        "series": {
            "resolution": "day" if day else "candle",
            "count": 0,
            "columns": {name: [] for name in columns},
            "benchmarks": {benchmark: [] for benchmark in shown["benchmarks"]},
        },
        "trades": {
            "count": 0,
            "total": total,
            "complete": total == 0,
            "columns": {name: [] for name in _TRADE_COLUMNS},
        },
    }
    return {**built, "results": empty}


def _again(
    spec: RunSpec,
    candles: pl.DataFrame,
    blob: bytes,
    held: Mapping[str, pl.DataFrame],
    *,
    light: bool,
) -> tuple[Run | None, dict[str, Any] | None]:
    """The engine's run of a finished backtest again and its ``results`` group (CPU work, in a
    thread); neither when the candles changed so much that no prediction lands on one."""
    try:
        done = run(
            candles,
            read_predictions(blob),
            period=spec.data.period,
            decision=spec.decision,
            execution=spec.execution,
            periods_per_year=spec.metrics.periods_per_year,
        )
    except NothingToTest:
        return None, None
    return done, results(done, spec.benchmarks, light=light, held=held)


def _finished(  # noqa: PLR0913 (what a build of the document is made of)
    backtest: Backtest,
    spec: RunSpec,
    report: RunReport,
    candles: CandleSet,
    blob: bytes,
    *,
    author: DocumentAuthor,
    light: bool,
    held: Mapping[str, pl.DataFrame],
) -> bytes:
    """The document of a finished backtest, its results built again (CPU work, in a thread).
    ``held`` has the candles of the index fund of each benchmark that holds one, by its ID."""
    changed = candles.fingerprint != report.loaded.fingerprint
    # Without a run the candles changed so much that no prediction lands on one any more.
    done, shown = _again(spec, candles.frame, blob, held, light=light)
    return dumps(_document(backtest, spec, report, done, shown, author=author, changed=changed))


def _document(  # noqa: PLR0913 (the document's sources)
    backtest: Backtest,
    spec: RunSpec,
    report: RunReport | None = None,
    done: Run | None = None,
    shown: dict[str, Any] | None = None,
    *,
    author: DocumentAuthor,
    changed: bool | None = None,
    queue_position: int | None = None,
) -> dict[str, Any]:
    return document(
        backtest=_header(backtest, report, author=author, queue_position=queue_position),
        software=Software(
            engine_version=ENGINE_VERSION,
            app_version=spec.app_version,
            runner=None if report is None else report.runner,
        ),
        model=BacktestModel(
            # Others never reach the model: they only read what it is.
            id=backtest.model_id if author.mine else None,
            name=spec.model.name,
            framework=spec.model.framework,
            sha256=spec.model.sha256,
            size_bytes=spec.model.size_bytes,
            manifest=spec.model.manifest,
        ),
        data=_data(spec, report, done, changed=changed),
        decision=spec.decision,
        execution=spec.execution,
        benchmarks=spec.benchmarks,
        metrics=spec.metrics,
        results=shown,
    )


def _header(
    backtest: Backtest,
    report: RunReport | None,
    *,
    author: DocumentAuthor,
    queue_position: int | None,
) -> BacktestHeader:
    return BacktestHeader(
        id=backtest.id,
        name=backtest.shown_name,
        generated_name=backtest.generated_name,
        number=backtest.number,
        author=author,
        public=backtest.published_at is not None,
        published_at=backtest.published_at,
        status=BacktestStatus(backtest.status),
        queue_position=queue_position,
        # Only the author can open the sweep: others see that there was none.
        sweep_id=backtest.sweep_id if author.mine else None,
        created_at=backtest.created_at,
        started_at=backtest.started_at,
        finished_at=backtest.finished_at,
        duration_s=backtest.duration_s,
        error=backtest.error,
        steps=shown_steps(backtest.steps, None if report is None else report.timings),
    )


def _data(
    spec: RunSpec, report: RunReport | None, done: Run | None, *, changed: bool | None
) -> DataOut:
    data = spec.data
    return DataOut(
        instrument=data.instrument,
        timeframe=data.timeframe,
        timeframe_seconds=data.timeframe_seconds,
        session=data.session,
        period=data.period,
        loaded=None if report is None else report.loaded,
        changed=changed,
        warm_up=WarmUpReport(
            candles=data.warm_up.candles,
            kind="estimate" if data.warm_up.kind == "estimate" else "exact",
            dropped=None if report is None else report.dropped,
        ),
        rows=None if done is None else rows_of(done),
        predictions=None if report is None else report.predictions,
        align=None if done is None else align_of(done),
    )


def _check_benchmarks(
    benchmarks: list[BenchmarkIn], instrument_id: InstrumentId
) -> list[BenchmarkIn]:
    """The benchmarks with their instrument's ID as the catalog writes it: buy and hold of the
    backtest's instrument, or of an index fund for a stock, each sent in any case.

    Raises:
        InvalidBacktest: a benchmark holds another instrument, or a fund on a crypto backtest.
    """
    checked = []
    for benchmark in benchmarks:
        try:
            held = InstrumentId.parse(benchmark.instrument)
        except InvalidRequest:
            held = None
        etf = held is not None and held.market == Market.STOCK and held.symbol in BENCHMARK_ETFS
        if held != instrument_id and not etf:
            raise InvalidBacktest(
                f"A benchmark is buy and hold of the backtest's instrument ({instrument_id}), "
                "or of SPY, QQQ or DIA for a stock."
            )
        if held != instrument_id and instrument_id.market != Market.STOCK:
            raise InvalidBacktest(
                "SPY, QQQ and DIA compare stock backtests only. Choose buy and hold of the pair."
            )
        checked.append(benchmark.model_copy(update={"instrument": str(held)}))
    return checked


def _manifest(model: Model, instrument_id: InstrumentId) -> Manifest:
    """The model's manifest, which the model runs with on the instrument.

    Raises:
        ModelNeedsSetup, InstrumentNotForModel.
    """
    if model.manifest is None:
        raise ModelNeedsSetup
    # It passed every check when it was stored with the model.
    manifest = Manifest.model_validate(model.manifest)
    if _SESSIONS[instrument_id.market] != manifest.input.session:
        raise InstrumentNotForModel(manifest.input.session, instrument_id.market)
    return manifest


def _instrument(info: InstrumentInfo) -> InstrumentOut:
    instrument = info.instrument
    return InstrumentOut(
        id=str(instrument.id),
        market=instrument.market.value,
        symbol=instrument.symbol,
        name=instrument.name,
        exchange=instrument.exchange,
        source=instrument.source,
        feed=instrument.feed,
        # A crypto pair is priced in its quote asset, a US stock in dollars.
        currency=instrument.quote or "USD",
    )


def _representation(manifest: Manifest) -> str:
    name = manifest.input.representation.id.split("@", 1)[0]
    kind = next(
        (shown for prefix, shown in _REPRESENTATIONS.items() if name.startswith(prefix)),
        Representation.windows,
    )
    return kind.value


def _day(ts: int) -> date:
    return datetime.fromtimestamp(ts, UTC).date()


def _setup(spec: RunSpec) -> dict[str, Any]:
    """The summary the Backtests list shows of the setup (``BacktestSetup``, the fields of
    Settings, Backtest defaults), rounded where its fields are: the stop loss in whole percent
    (0 without one), the threshold 0 for k-means, the index fund a benchmark holds, else buy
    and hold (also without a benchmark). The document has the setup as it is."""
    rule, execution = spec.decision.rule, spec.execution
    limit = 0.0
    if isinstance(rule, ThresholdRule):
        limit = rule.limit
    elif isinstance(rule, HysteresisRule):
        limit = rule.enter
    etfs = [SetupBenchmark(etf.symbol) for etf in spec.etf_benchmarks().values()]
    setup = BacktestSetup(
        timeframe=SetupTimeframe(spec.data.timeframe),
        period_start=_day(spec.data.period.start),
        period_end=_day(spec.data.period.end - 1),
        direction=SetupDirection.long if spec.decision.long_only else SetupDirection.both,
        threshold=limit,
        stop_loss_pct=0 if execution.stop_loss is None else round(execution.stop_loss * 100),
        fees_pct=execution.fee_bps / 100,
        slippage_pct=execution.slippage_bps / 100,
        benchmark=etfs[0] if etfs else SetupBenchmark.buy_and_hold,
    )
    return setup.model_dump(mode="json")
