"""Starting a backtest and building its result document: what ``POST /api/v1/backtests`` checks
before it accepts a run, the document ``GET /api/v1/backtests/{id}`` builds from what the run
stored (docs/engine.md, "The result document"), and cancelling a queued backtest.

A user's backtests run one at a time: a new one waits in the user's queue (``queued``, at most
``store.QUEUE_MAX``) while another of theirs runs, and the runner starts the oldest when that
one ends (``BacktestRunner``).

The document is built on request: the stored predictions, the candles from the cache and the
engine, which is deterministic. When the candles' fingerprint differs from the run's (a source
adjusted old prices), ``data.changed`` says so and the results come from the candles as they
are now. The comparison reads only what the run stored (``stored``), so it never loads candles.
"""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from functools import partial
from typing import Any, NamedTuple

import anyio
import numpy as np
import polars as pl
from redis.exceptions import RedisError

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
    CURVES,
    PERIODS_PER_YEAR,
    RISK_FREE_RATE,
    NothingToTest,
    Run,
    align_of,
    document,
    dumps,
    read_curves,
    read_predictions,
    results,
    rows_of,
    run,
)
from candlestack.experiments.document import (
    BENCHMARK_ETFS,
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
    Software,
    ThresholdRule,
    WarmUpReport,
)
from candlestack.experiments.errors import (
    BacktestNotFinished,
    BacktestNotFound,
    BacktestNotQueued,
    BacktestsUnavailable,
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    ModelNotFound,
    NoDocument,
    QueueFull,
)
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
from candlestack.experiments.spec import RunReport, RunSpec, SpecData, SpecModel, SpecWarmUp
from candlestack.experiments.steps import pending, shown_steps
from candlestack.experiments.store import QUEUE_MAX, BacktestStore
from candlestack.ml import Model
from candlestack.preprocessing import Manifest, warm_up

# The sessions of the candles each market has: a model that reads US trading hours runs on
# stocks, one that reads around the clock on crypto.
_SESSIONS = {"stock": "regular", "crypto": "24/7"}
# The representations the Backtests list names, by the catalog block's name; time windows of
# candles (candles@1) for any other.
_REPRESENTATIONS = {"renko": Representation.renko, "kagi": Representation.kagi}
CANCELLED = "You cancelled the backtest before it ran."


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
        self, store: BacktestStore, data: DataService, runner: BacktestRunner, app_version: str
    ) -> None:
        self._store = store
        self._data = data
        self._runner = runner
        self._app_version = app_version

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
            raise QueueFull
        await self.ready()
        spec = await self.spec(prepared)
        queued = await self._store.queued(user_id)
        waits = queued > 0 or await self._store.running(user_id)
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

    async def start(self, user_id: str, body: BacktestIn) -> tuple[Backtest, bytes]:
        """Checks a new backtest of the user, stores it, and returns it with its document: it
        runs at once when no other backtest of the user runs or waits, else it waits at the
        end of their queue (``queued``).

        Raises:
            InvalidBacktest, ModelNotFound, ModelNeedsSetup, InstrumentNotForModel, QueueFull,
            BacktestsUnavailable, and the data module's errors of the instrument and the
            period (``data.DataError``).
        """
        prepared = await self.prepare(user_id, body)
        author = DocumentAuthor(id=user_id, name=await self._store.author_name(user_id), mine=True)
        # Before the slower checks; again under the lock below, where it counts.
        if await self._store.queued(user_id) >= QUEUE_MAX:
            raise QueueFull
        await self.ready()
        spec = await self.spec(prepared)
        await self._store.lock_user(user_id)
        if await self._store.queued(user_id) >= QUEUE_MAX:
            raise QueueFull
        # The model may have been deleted meanwhile; held now until the backtest is stored.
        if not await self._store.hold_model(user_id, prepared.model.id):
            raise ModelNotFound
        backtest = self.new_backtest(
            user_id, prepared, spec, number=await self._store.last_number(user_id) + 1
        )
        await self._store.add_named(backtest)
        started = await self.take_next(user_id)
        position = await self._store.queue_position(backtest.id)
        await self._store.commit()
        self.run(started)
        document = _document(backtest, spec, author=author, queue_position=position)
        return backtest, dumps(document)

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

    async def take_next(self, user_id: str) -> Backtest | None:
        """Marks the user's oldest queued backtest running, under ``lock_user``, while none of
        theirs runs: the one just stored, or an older one should the queue have stood still
        (``BacktestRunner.resume``). Hand it to ``run`` once committed."""
        return await self._store.take_next(user_id, datetime.now(UTC))

    def run(self, started: Backtest | None) -> None:
        """Runs the backtest ``take_next`` started, once its transaction committed."""
        if started is not None:
            self._runner.start(started.id)

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
                manifest=model.manifest or {},
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

    async def document(self, user_id: str, backtest_id: str, *, light: bool) -> tuple[int, bytes]:
        """The number and the result document of the user's backtest, or of another account's
        while it is published: the light view, or with ``light`` False the download, which
        needs a finished backtest.

        Raises:
            BacktestNotFound, NoDocument, BacktestNotFinished (the download), and the data
            module's errors of loading the candles again (``data.DataError``).
        """
        found = await self._store.with_run(user_id, backtest_id)
        if found is None:
            raise BacktestNotFound
        backtest = found.backtest
        if backtest.spec is None:
            raise NoDocument
        finished = backtest.status == BacktestStatus.finished.value
        if not light and not finished:
            raise BacktestNotFinished(backtest.status)
        spec = RunSpec.model_validate(backtest.spec)
        # Only the account's display name and public ID: never its email.
        author = DocumentAuthor(
            id=backtest.user_id, name=found.author, mine=backtest.user_id == user_id
        )
        if not finished or backtest.report is None:
            position = None
            if backtest.status == BacktestStatus.queued.value:
                position = await self._store.queue_position(backtest.id)
            document = _document(backtest, spec, author=author, queue_position=position)
            return backtest.number, dumps(document)
        report = RunReport.model_validate(backtest.report)
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
        build = partial(
            _finished, backtest, spec, report, candles, blob, author=author, light=light, held=held
        )
        return backtest.number, await anyio.to_thread.run_sync(build)

    async def header(self, user_id: str, backtest_id: str) -> BacktestHeader:
        """The ``backtest`` group of the document of the user's backtest, or of another
        account's while it is published, from its row alone: its status, its place in the queue
        and its steps, without building the results (what an agent polls).

        Raises:
            BacktestNotFound, NoDocument.
        """
        found = await self._store.with_run(user_id, backtest_id)
        if found is None:
            raise BacktestNotFound
        backtest = found.backtest
        if backtest.spec is None:
            raise NoDocument
        author = DocumentAuthor(
            id=backtest.user_id, name=found.author, mine=backtest.user_id == user_id
        )
        report = None if backtest.report is None else RunReport.model_validate(backtest.report)
        position = None
        if backtest.status == BacktestStatus.queued.value:
            position = await self._store.queue_position(backtest.id)
        return _header(backtest, report, author=author, queue_position=position)

    async def stored(self, user_id: str, backtest_id: str) -> dict[str, Any]:
        """The light result document of the user's backtest, or of another account's while it
        is published, from what its run stored and never the data source: what the comparison
        reads. ``results`` has only the statistics and the series' ``CURVES`` columns (null
        until it finished), ``data`` no alignment and ``backtest`` no queue position.

        Raises:
            BacktestNotFound, NoDocument.
        """
        found = await self._store.with_run(user_id, backtest_id, curves=True)
        if found is None:
            raise BacktestNotFound
        backtest = found.backtest
        if backtest.spec is None:
            raise NoDocument
        spec = RunSpec.model_validate(backtest.spec)
        author = DocumentAuthor(
            id=backtest.user_id, name=found.author, mine=backtest.user_id == user_id
        )
        finished = backtest.status == BacktestStatus.finished.value
        if not finished or backtest.report is None or backtest.stats is None:
            return _document(backtest, spec, author=author)
        report = RunReport.model_validate(backtest.report)
        if backtest.curves is None:
            columns = _short_curves(backtest, report)
        else:
            columns = read_curves(backtest.curves)
        shown = {"stats": backtest.stats, "series": {"columns": columns}}
        document = _document(backtest, spec, report, shown=shown, author=author)
        # The rows the run found, as a rebuild of the document finds them again.
        document["data"]["rows"] = report.rows.model_dump()
        return document


def _short_curves(backtest: Backtest, report: RunReport) -> dict[str, list[Any]]:
    """The curves of a backtest that finished before they were stored, from its short equity
    series (``Backtest.set_equity``): its points at the evenly spaced times they were taken at
    from the first row to the last, the fall of those points, and the close as buy and hold of
    its own instrument when that was its benchmark (else no close, so no line)."""
    if backtest.equity is None:
        return {name: [] for name in CURVES}
    equity = np.asarray(backtest.equity["strategy"], dtype=np.float64) + 1.0
    rows = report.rows
    ts = np.linspace(rows.first_ts, rows.last_ts, equity.size).astype(np.int64)
    fall = equity / np.maximum.accumulate(equity) - 1.0
    close: list[float | None] = [None] * equity.size
    if backtest.setup.get("benchmark") == SetupBenchmark.buy_and_hold.value:
        close = [value + 1.0 for value in backtest.equity["benchmark"]]
    return {
        "ts": ts.tolist(),
        "close": close,
        "equity": equity.tolist(),
        "drawdown": fall.tolist(),
    }


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
    try:
        done = run(
            candles.frame,
            read_predictions(blob),
            period=spec.data.period,
            decision=spec.decision,
            execution=spec.execution,
            periods_per_year=spec.metrics.periods_per_year,
        )
    except NothingToTest:
        # The candles changed so much that no prediction lands on one any more.
        return dumps(_document(backtest, spec, report, author=author, changed=changed))
    shown = results(done, spec.benchmarks, light=light, held=held)
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
