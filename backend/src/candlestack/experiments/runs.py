"""Starting a backtest and building its result document: what ``POST /api/v1/backtests`` checks
before it accepts a run, and the document ``GET /api/v1/backtests/{id}`` builds from what the
run stored (docs/engine.md, "The result document").

The document is built on request: the stored predictions, the candles from the cache and the
engine, which is deterministic. When the candles' fingerprint differs from the run's (a source
adjusted old prices), ``data.changed`` says so and the results come from the candles as they
are now.
"""

from datetime import UTC, date, datetime
from functools import partial
from typing import Any

import anyio
from redis.exceptions import RedisError

from candlestack.data import CandleSet, DataService, InstrumentId, InstrumentInfo, Timeframe
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
    results,
    rows_of,
    run,
)
from candlestack.experiments.document import (
    BacktestHeader,
    BacktestIn,
    BacktestModel,
    BenchmarkIn,
    DataOut,
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
    BacktestRunning,
    BacktestsUnavailable,
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    ModelNotFound,
    NoDocument,
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
from candlestack.experiments.store import BacktestStore
from candlestack.ml import Model
from candlestack.preprocessing import Manifest, warm_up

# The sessions of the candles each market has: a model that reads US trading hours runs on
# stocks, one that reads around the clock on crypto.
_SESSIONS = {"stock": "regular", "crypto": "24/7"}
# The representations the Backtests list names, by the catalog block's name; time windows of
# candles (candles@1) for any other.
_REPRESENTATIONS = {"renko": Representation.renko, "kagi": Representation.kagi}


class BacktestRuns:
    def __init__(
        self, store: BacktestStore, data: DataService, runner: BacktestRunner, app_version: str
    ) -> None:
        self._store = store
        self._data = data
        self._runner = runner
        self._app_version = app_version

    async def start(self, user_id: str, body: BacktestIn) -> tuple[Backtest, bytes]:
        """Checks a new backtest of the user, stores it as running, starts its run and returns
        it with its document.

        Raises:
            InvalidBacktest, ModelNotFound, ModelNeedsSetup, InstrumentNotForModel,
            BacktestRunning, BacktestsUnavailable, and the data module's errors of the
            instrument and the period (``data.DataError``).
        """
        instrument_id = InstrumentId.parse(body.data.instrument.id)
        _check_benchmarks(body.benchmarks, instrument_id)
        model = await self._store.run_model(user_id, body.model.id)
        if model is None:
            raise ModelNotFound
        manifest = _manifest(model, instrument_id)
        # Before the slower checks; again under the lock below, where it counts.
        if await self._store.running(user_id):
            raise BacktestRunning
        # The reads so far end here, so no connection waits on the queue and the data module.
        await self._store.commit()
        try:
            await self._runner.ping()
        except RedisError as error:
            raise BacktestsUnavailable from error
        spec = await self._spec(model, manifest, instrument_id, body)
        await self._store.lock_user(user_id)
        if await self._store.running(user_id):
            raise BacktestRunning
        # The model may have been deleted meanwhile; held now until the backtest is stored.
        if not await self._store.hold_model(user_id, model.id):
            raise ModelNotFound
        backtest = Backtest(
            id=new_backtest_id(),
            user_id=user_id,
            number=await self._store.last_number(user_id) + 1,
            status=BacktestStatus.running.value,
            instruments=[{"market": instrument_id.market, "symbol": instrument_id.symbol}],
            representation=_representation(manifest),
            model_id=model.id,
            model_name=model.name,
            model_framework=model.framework,
            setup=_setup(spec),
            spec=spec.model_dump(mode="json"),
            steps=pending(),
            created_at=datetime.now(UTC),
        )
        self._store.add(backtest)
        await self._store.commit()
        self._runner.start(backtest.id)
        return backtest, dumps(_document(backtest, spec))

    async def _spec(
        self, model: Model, manifest: Manifest, instrument_id: InstrumentId, body: BacktestIn
    ) -> RunSpec:
        """The setup to store: the model with its manifest, the instrument as the catalog has
        it and where the load with the warm-up starts (the period checked against the data)."""
        timeframe = Timeframe(manifest.input.timeframe)
        needed = warm_up(manifest)
        period = body.data.period
        info = await self._data.instrument(instrument_id)
        load_start = await self._data.warm_up_start(
            instrument_id, timeframe, period.start, period.end, needed.candles
        )
        return RunSpec(
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

    async def document(self, user_id: str, backtest_id: str, *, light: bool) -> tuple[int, bytes]:
        """The user's backtest's number and its result document: the light view, or with
        ``light`` False the download, which needs a finished backtest.

        Raises:
            BacktestNotFound, NoDocument, BacktestNotFinished (the download), and the data
            module's errors of loading the candles again (``data.DataError``).
        """
        backtest = await self._store.with_run(user_id, backtest_id)
        if backtest is None:
            raise BacktestNotFound
        if backtest.spec is None:
            raise NoDocument
        finished = backtest.status == BacktestStatus.finished.value
        if not light and not finished:
            raise BacktestNotFinished(backtest.status)
        spec = RunSpec.model_validate(backtest.spec)
        if not finished or backtest.report is None:
            return backtest.number, dumps(_document(backtest, spec))
        report = RunReport.model_validate(backtest.report)
        blob = await self._store.predictions(backtest.id)
        assert blob is not None  # a finished run stored them
        # The reads end here, so no connection waits on the data module and the build.
        await self._store.commit()
        candles = await self._data.candles(
            InstrumentId.parse(spec.data.instrument.id),
            Timeframe(spec.data.timeframe),
            report.loaded.start,
            report.loaded.end,
        )
        build = partial(_finished, backtest, spec, report, candles, blob, light=light)
        return backtest.number, await anyio.to_thread.run_sync(build)


def _finished(
    backtest: Backtest,
    spec: RunSpec,
    report: RunReport,
    candles: CandleSet,
    blob: bytes,
    *,
    light: bool,
) -> bytes:
    """The document of a finished backtest, its results built again (CPU work, in a thread)."""
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
        return dumps(_document(backtest, spec, report, changed=changed))
    shown = results(done, spec.benchmarks, light=light)
    return dumps(_document(backtest, spec, report, done, shown, changed=changed))


def _document(
    backtest: Backtest,
    spec: RunSpec,
    report: RunReport | None = None,
    done: Run | None = None,
    shown: dict[str, Any] | None = None,
    *,
    changed: bool | None = None,
) -> dict[str, Any]:
    return document(
        backtest=BacktestHeader(
            id=backtest.id,
            number=backtest.number,
            status=BacktestStatus(backtest.status),
            created_at=backtest.created_at,
            finished_at=backtest.finished_at,
            duration_s=backtest.duration_s,
            error=backtest.error,
            steps=shown_steps(backtest.steps, None if report is None else report.timings),
        ),
        software=Software(
            engine_version=ENGINE_VERSION,
            app_version=spec.app_version,
            runner=None if report is None else report.runner,
        ),
        model=BacktestModel(
            id=backtest.model_id,
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


def _check_benchmarks(benchmarks: list[BenchmarkIn], instrument_id: InstrumentId) -> None:
    for benchmark in benchmarks:
        if benchmark.instrument.strip().lower() != str(instrument_id).lower():
            raise InvalidBacktest(
                f"Buy and hold compares with the backtest's own instrument: set the benchmark's "
                f"instrument to {instrument_id}."
            )


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
    (0 without one), the threshold 0 for k-means. The document has the setup as it is."""
    rule, execution = spec.decision.rule, spec.execution
    limit = 0.0
    if isinstance(rule, ThresholdRule):
        limit = rule.limit
    elif isinstance(rule, HysteresisRule):
        limit = rule.enter
    setup = BacktestSetup(
        timeframe=SetupTimeframe(spec.data.timeframe),
        period_start=_day(spec.data.period.start),
        period_end=_day(spec.data.period.end - 1),
        direction=SetupDirection.long if spec.decision.long_only else SetupDirection.both,
        threshold=limit,
        stop_loss_pct=0 if execution.stop_loss is None else round(execution.stop_loss * 100),
        fees_pct=execution.fee_bps / 100,
        slippage_pct=execution.slippage_bps / 100,
        benchmark=SetupBenchmark.buy_and_hold,
    )
    return setup.model_dump(mode="json")
