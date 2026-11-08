"""Sweeps: a grid of backtests from one request (``POST /api/v1/sweeps``), ranked once they all
ended (``GET /api/v1/sweeps/{id}``).

A sweep is a base setup, the body of ``POST /api/v1/backtests``, and lists of values to vary:
models (each with its manifest, so also what it reads and its timeframe), instruments, periods
and decision rules. Every combination is one backtest, in the order models, instruments,
periods, rules. Each setup passes the checks of a new backtest (``BacktestRuns``) before any is
stored, and all of them go to the end of the user's queue in one transaction, so a sweep starts
whole or not at all. They are ordinary backtests with the sweep's ID: listed, comparable and
publishable as any other.

The ranking orders them by one statistic with the marking rules of the comparison
(``compare.metric_rows`` and ``compare.ranks``), from the statistics each run stored.
"""

from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from itertools import product
from math import prod
from typing import Annotated, Any, Literal, Self

import anyio
from pydantic import BaseModel, ConfigDict, Field, model_validator

from candlestack.data import DataError, InstrumentId
from candlestack.experiments.compare import Mark, metric_rows, ranks
from candlestack.experiments.document import (
    BacktestIn,
    BenchmarkIn,
    DataIn,
    InstrumentRef,
    ModelRef,
    Period,
    Rule,
)
from candlestack.experiments.errors import (
    InstrumentNotForModel,
    InvalidBacktest,
    ModelNeedsSetup,
    ModelNotFound,
    SetupFailed,
    SweepNotFound,
    SweepQueueFull,
    SweepTooLarge,
)
from candlestack.experiments.idempotency import Committed
from candlestack.experiments.models import Sweep, new_sweep_id
from candlestack.experiments.runs import BacktestRuns, Prepared
from candlestack.experiments.schemas import PAGE_SIZE, BacktestStatus, Representation
from candlestack.experiments.service import pages
from candlestack.experiments.spec import RunSpec
from candlestack.experiments.store import QUEUE_MAX, BacktestStore, Listed

# A sweep runs 2 to 20 backtests: as many as wait in a queue and as the comparison takes.
SWEEP_MIN = 2
SWEEP_MAX = 20
# An ID of a model or an instrument in a list to vary.
_Id = Annotated[str, Field(min_length=1, max_length=64)]

# The errors of a setup's checks that the client should see, named with the setup they hit.
_SETUP_ERRORS = (InvalidBacktest, ModelNotFound, ModelNeedsSetup, InstrumentNotForModel, DataError)


class SweepMetric(StrEnum):
    """A statistic of `results.stats` the ranking can use: one that is better higher or lower."""

    total_return = "total_return"
    max_drawdown = "max_drawdown"
    sharpe = "sharpe"
    sortino = "sortino"
    win_rate = "win_rate"
    avg_trade_return = "avg_trade_return"
    best_trade_return = "best_trade_return"
    worst_trade_return = "worst_trade_return"
    fees_paid = "fees_paid"


class SweepVary(BaseModel):
    """The values to vary, each list in place of the base's value; a list left out or empty
    keeps the base's. Every combination is one backtest."""

    model_config = ConfigDict(extra="forbid")

    models: list[_Id] = Field(
        default_factory=list,
        max_length=SWEEP_MAX,
        description="Public IDs of the user's models. A model's manifest sets what it reads (the "
        "representation) and its timeframe, so list models to vary those",
        examples=[["mdl_k2m9qx47f3", "mdl_t9n8v7d6a5"]],
    )
    instruments: list[_Id] = Field(
        default_factory=list,
        max_length=SWEEP_MAX,
        description="`<market>:<symbol>` IDs. A benchmark that holds the base's instrument "
        "holds each backtest's own",
        examples=[["stock:AAPL", "stock:MSFT"]],
    )
    periods: list[Period] = Field(default_factory=list, max_length=SWEEP_MAX)
    rules: list[Rule] = Field(
        default_factory=list,
        max_length=SWEEP_MAX,
        description="Decision rules in place of `decision.rule`: other limits of one rule, or "
        "other rules",
        examples=[[{"name": "threshold", "limit": 0.1}, {"name": "threshold", "limit": 0.2}]],
    )

    @model_validator(mode="after")
    def _each_value_once(self) -> Self:
        for field in ("models", "instruments", "periods", "rules"):
            key = _instrument_key if field == "instruments" else repr
            values = [key(value) for value in getattr(self, field)]
            if len(set(values)) < len(values):
                raise ValueError(f"{field} has a value twice")
        return self


def _instrument_key(value: str) -> str:
    """The instrument an ID names, so two spellings of one count as the same value; an ID that
    does not parse stays as written (its check fails later, with its setup)."""
    try:
        return repr(InstrumentId.parse(value))
    except DataError:
        return value


class SweepIn(BaseModel):
    """A new sweep: a base setup, the values to vary and the statistic to rank by."""

    # A mistyped field, such as a dry run flag, is an error rather than a sweep started.
    model_config = ConfigDict(extra="forbid")

    base: BacktestIn = Field(
        description="The body of `POST /api/v1/backtests`. It still needs `model.id` and "
        "`data.instrument` when `vary` lists models or instruments, whose values then replace "
        "them"
    )
    vary: SweepVary = Field(default_factory=SweepVary)
    metric: SweepMetric = Field(
        default=SweepMetric.sharpe,
        description="What the ranking orders by, unless `GET /api/v1/sweeps/{sweep_id}` names "
        "another",
    )
    validate_only: bool = Field(
        default=False,
        description="Check every setup and the queue's room and answer with the plan, without "
        "starting anything",
    )


class SweepSetup(BaseModel):
    """What one backtest of the sweep runs on and with."""

    model: str = Field(description="Public ID of the model", examples=["mdl_k2m9qx47f3"])
    model_name: str = Field(description="Its name when the sweep started", examples=["lstm.keras"])
    representation: Representation
    timeframe: str = Field(description="The model's timeframe", examples=["30m"])
    instrument: str = Field(description="`<market>:<symbol>`", examples=["stock:AAPL"])
    period: Period
    rule: dict[str, Any] = Field(
        description="The decision rule, as `decision.rule` of the result document",
        examples=[{"name": "threshold", "limit": 0.1}],
    )


class SweepPlan(BaseModel):
    """The answer of a dry run (`validate_only`): what the sweep would start."""

    count: int = Field(description="Backtests the sweep would start", examples=[6])
    limit: int = Field(description="The most a sweep starts", examples=[SWEEP_MAX])
    queue_room: int = Field(
        description="Backtests the user's queue has room for now", examples=[QUEUE_MAX]
    )
    setups: list[SweepSetup] = Field(description="Each backtest's setup, in the grid's order")


class SweepBacktest(BaseModel):
    id: str = Field(examples=["bt_7f3k2m9qx4"])
    number: int = Field(description="The user's own number, shown as #N", examples=[12])
    name: str = Field(examples=["Silent Orion"])
    status: BacktestStatus
    queue_position: int | None = Field(
        description="Where a queued backtest is in the user's queue: 1 starts next"
    )
    error: str | None = Field(description="What went wrong, for a failed one")
    setup: SweepSetup


class SweepProgress(BaseModel):
    """How many of the sweep's backtests have each status."""

    queued: int
    running: int
    finished: int
    failed: int


class SweepRanked(BaseModel):
    rank: int | None = Field(
        description="1 for the best; values that read the same in the app share a rank (1, 1, "
        "3); null without a value (failed, or the statistic is undefined)",
        examples=[1],
    )
    id: str = Field(examples=["bt_7f3k2m9qx4"])
    number: int = Field(examples=[12])
    name: str = Field(examples=["Silent Orion"])
    value: float | None = Field(description="The statistic, as in `results.stats`", examples=[1.42])
    mark: Mark | None = Field(
        description="`best` or `worst` as `GET /api/v1/compare` marks it, else null"
    )


class SweepRanking(BaseModel):
    """The backtests by one statistic, the best first, those without a value last."""

    metric: SweepMetric
    label: str = Field(description="What the app calls it", examples=["Sharpe"])
    better: Literal["higher", "lower"] = Field(
        description="Which way is better: `lower` for the fees, and for the max drawdown by the "
        "size of the fall: its values are 0 or below and a smaller fall is better, so -0.05 "
        "beats -0.20 and ranks first"
    )
    backtests: list[SweepRanked]


class SweepOut(BaseModel):
    """A sweep: its backtests, how far they are, and once all ended the ranking."""

    id: str = Field(examples=["sw_7f3k2m9qx4"])
    created_at: datetime = Field(description="When it was started (UTC)")
    metric: SweepMetric = Field(description="What the ranking orders by")
    count: int = Field(description="Its backtests", examples=[6])
    done: bool = Field(description="None of its backtests is queued or running")
    progress: SweepProgress
    backtests: list[SweepBacktest] = Field(description="In the grid's order")
    ranking: SweepRanking | None = Field(
        description="Null until every backtest ended. For every statistic, the setups and the "
        "curves, ask `GET /api/v1/compare` with the IDs of `backtests`"
    )


class SweepBest(BaseModel):
    """The sweep's best backtest so far by its `metric`, as its ranking would put it first."""

    id: str = Field(examples=["bt_t9n8v7d6a5"])
    number: int = Field(examples=[13])
    name: str = Field(examples=["Bright Vega"])
    value: float = Field(description="The statistic, as in `results.stats`", examples=[1.42])


class SweepListed(BaseModel):
    """A sweep in the list: how far it is and its best backtest so far."""

    id: str = Field(examples=["sw_7f3k2m9qx4"])
    created_at: datetime = Field(description="When it was started (UTC)")
    metric: SweepMetric = Field(description="What its ranking orders by")
    count: int = Field(description="Its backtests", examples=[6])
    done: bool = Field(description="None of its backtests is queued or running")
    progress: SweepProgress
    best: SweepBest | None = Field(
        description="The finished backtest with the best `metric` so far; null until one has "
        "a value. `GET /api/v1/sweeps/{sweep_id}` ranks them all"
    )


class SweepsPage(BaseModel):
    sweeps: list[SweepListed] = Field(description="The page, newest first")
    total: int = Field(description="The user's sweeps", examples=[3])
    page: int = Field(description="The page, from 1", examples=[1])
    pages: int = Field(description="Pages of the sweeps, at least 1", examples=[1])


# --- The grid -------------------------------------------------------------------------------


def grid_size(vary: SweepVary) -> int:
    """How many backtests the lists make: the product of their lengths, an empty one 1."""
    lists = (vary.models, vary.instruments, vary.periods, vary.rules)
    return prod(max(1, len(values)) for values in lists)


def grid(base: BacktestIn, vary: SweepVary) -> list[BacktestIn]:
    """Each combination of the values as a new backtest's body, models first, then instruments,
    periods and rules; a list left empty keeps the base's value.

    Raises:
        SweepTooLarge: More than ``SWEEP_MAX`` combinations.
        InvalidBacktest: Fewer than ``SWEEP_MIN``.
    """
    count = grid_size(vary)
    if count > SWEEP_MAX:
        raise SweepTooLarge(count)
    if count < SWEEP_MIN:
        raise InvalidBacktest(
            f"A sweep runs {SWEEP_MIN} to {SWEEP_MAX} backtests: list at least two values to vary."
        )
    combinations = product(
        vary.models or [base.model.id],
        vary.instruments or [base.data.instrument.id],
        vary.periods or [base.data.period],
        vary.rules or [base.decision.rule],
    )
    return [
        base.model_copy(
            update={
                "model": ModelRef(id=model),
                "data": DataIn(instrument=InstrumentRef(id=instrument), period=period),
                "decision": base.decision.model_copy(update={"rule": rule}),
                "benchmarks": [
                    _follow(benchmark, base.data.instrument.id, instrument)
                    for benchmark in base.benchmarks
                ],
            }
        )
        for model, instrument, period, rule in combinations
    ]


def _same_instrument(first: str, second: str) -> bool:
    try:
        return InstrumentId.parse(first) == InstrumentId.parse(second)
    except DataError:
        return first == second


def _follow(benchmark: BenchmarkIn, base: str, instrument: str) -> BenchmarkIn:
    """Buy and hold of the base's instrument becomes buy and hold of the setup's own; a fund
    stays."""
    if _same_instrument(benchmark.instrument, base):
        return benchmark.model_copy(update={"instrument": instrument})
    return benchmark


@contextmanager
def _setup(index: int) -> Iterator[None]:
    """Names the setup a check failed for."""
    try:
        yield
    except _SETUP_ERRORS as error:
        raise SetupFailed(index, error) from error


def _setup_of(prepared: Prepared, spec: RunSpec) -> SweepSetup:
    return SweepSetup(
        model=prepared.model.id,
        model_name=prepared.model.name,
        representation=prepared.representation,
        timeframe=spec.data.timeframe,
        instrument=spec.data.instrument.id,
        period=spec.data.period,
        rule=spec.decision.rule.model_dump(mode="json"),
    )


# --- The service ----------------------------------------------------------------------------


class Sweeps:
    def __init__(self, store: BacktestStore, runs: BacktestRuns) -> None:
        self._store = store
        self._runs = runs

    async def plan(self, user_id: str, body: SweepIn) -> SweepPlan:
        """What the sweep would start, every setup checked as a new backtest, without storing
        anything (``validate_only``).

        Raises:
            The errors of ``start`` but ``ModelNotFound`` of a model deleted meanwhile.
        """
        checked, room = await self._checked(user_id, body)
        setups = [_setup_of(prepared, spec) for prepared, spec in checked]
        return SweepPlan(count=len(setups), limit=SWEEP_MAX, queue_room=room, setups=setups)

    async def start(
        self, user_id: str, body: SweepIn, committed: Committed | None = None
    ) -> SweepOut:
        """Checks every setup of the sweep as a new backtest, then stores the sweep and its
        backtests at the end of the user's queue, all or none. Returns the sweep.
        ``committed`` is awaited with its ID once it is stored (``idempotency.once``).

        Raises:
            SweepTooLarge, InvalidBacktest (fewer than two setups), SweepQueueFull,
            BacktestLimitReached, BacktestsUnavailable, and SetupFailed with the setup's error.
        """
        checked, _ = await self._checked(user_id, body)
        await self._store.lock_user(user_id)
        await self._fits(user_id, len(checked))
        held: set[str] = set()
        for index, (prepared, _spec) in enumerate(checked):
            model_id = prepared.model.id
            # The model may have been deleted meanwhile; held now until the backtests are stored.
            if model_id not in held and not await self._store.hold_model(user_id, model_id):
                raise SetupFailed(index, ModelNotFound())
            held.add(model_id)
        sweep_id = new_sweep_id()
        last = await self._store.last_number(user_id)
        backtests = [
            self._runs.new_backtest(user_id, prepared, spec, number=last + index, sweep_id=sweep_id)
            for index, (prepared, spec) in enumerate(checked, start=1)
        ]
        grid_rows = [
            {"backtest_id": backtest.id, **_setup_of(prepared, spec).model_dump(mode="json")}
            for backtest, (prepared, spec) in zip(backtests, checked, strict=True)
        ]
        await self._store.add_sweep(
            Sweep(
                id=sweep_id,
                user_id=user_id,
                metric=body.metric.value,
                grid=grid_rows,
                created_at=datetime.now(UTC),
            )
        )
        for backtest in backtests:
            await self._store.add_named(backtest)
        # From the commit on nothing cancels the start: its backtests get their slots.
        with anyio.CancelScope(shield=True):
            await self._store.commit()
            if committed is not None:
                await committed(sweep_id)
            await self._runs.schedule()
        return await self.sweep(user_id, sweep_id)

    async def _checked(
        self, user_id: str, body: SweepIn
    ) -> tuple[list[tuple[Prepared, RunSpec]], int]:
        """Each setup of the grid after the checks of a new backtest, and the queue's room."""
        setups = grid(body.base, body.vary)
        prepared = []
        for index, setup in enumerate(setups):
            with _setup(index):
                prepared.append(await self._runs.prepare(user_id, setup))
        # Before the slower checks; again under the lock, where it counts.
        room = await self._fits(user_id, len(setups))
        await self._runs.ready()
        specs = []
        for index, each in enumerate(prepared):
            with _setup(index):
                specs.append(await self._runs.spec(each))
        return list(zip(prepared, specs, strict=True)), room

    async def _fits(self, user_id: str, count: int) -> int:
        """The room of the user's queue.

        Raises:
            SweepQueueFull: It has room for fewer than ``count``.
            BacktestLimitReached: The account cannot keep ``count`` more backtests.
        """
        room = max(0, QUEUE_MAX - await self._store.queued(user_id))
        if count > room:
            raise SweepQueueFull(count, room, await self._runs.retry_after(user_id, count - room))
        await self._runs.fits(user_id, count)
        return room

    async def progress(self, user_id: str, sweep_id: str) -> tuple[list[str], SweepProgress]:
        """The IDs of the user's sweep's backtests and how many have each status, from their
        rows alone: what a wait for the sweep reads again and again.

        Raises:
            SweepNotFound.
        """
        rows = await self._store.sweep_statuses(user_id, sweep_id)
        if rows is None:
            raise SweepNotFound
        counts = Counter(status for _, status in rows)
        progress = SweepProgress(
            **{status.value: counts[status.value] for status in BacktestStatus}
        )
        return [backtest_id for backtest_id, _ in rows], progress

    async def sweep(
        self, user_id: str, sweep_id: str, metric: SweepMetric | None = None
    ) -> SweepOut:
        """The user's sweep with its backtests and, once all ended, their ranking by ``metric``
        (else the sweep's own).

        Raises:
            SweepNotFound.
        """
        sweep = await self._store.sweep(user_id, sweep_id)
        if sweep is None:
            raise SweepNotFound
        rows = await self._store.sweep_backtests(sweep)
        setups = {row["backtest_id"]: row for row in sweep.grid}
        backtests = [_backtest_out(row, setups[row.backtest.id]) for row in rows]
        progress, done = _progress(rows)
        chosen = metric or SweepMetric(sweep.metric)
        return SweepOut(
            id=sweep.id,
            created_at=sweep.created_at,
            metric=chosen,
            count=len(rows),
            done=done,
            progress=progress,
            backtests=backtests,
            ranking=ranking(rows, chosen) if done else None,
        )

    async def sweeps(self, user_id: str, page: int = 1, per_page: int = PAGE_SIZE) -> SweepsPage:
        """A page of the user's sweeps, newest first, each with its progress and its best
        backtest so far: three queries, whatever the page holds."""
        total = await self._store.sweep_total(user_id)
        found = await self._store.sweeps(user_id, limit=per_page, offset=(page - 1) * per_page)
        rows = await self._store.backtests_of_sweeps(user_id, [sweep.id for sweep in found])
        by_sweep: dict[str, list[Listed]] = {sweep.id: [] for sweep in found}
        for row in rows:
            by_sweep[str(row.backtest.sweep_id)].append(row)
        return SweepsPage(
            sweeps=[_listed(sweep, by_sweep[sweep.id]) for sweep in found],
            total=total,
            page=page,
            pages=pages(total, per_page),
        )


def _progress(rows: Sequence[Listed]) -> tuple[SweepProgress, bool]:
    """How many of the backtests have each status, and whether none is queued or running."""
    counts = Counter(row.backtest.status for row in rows)
    done = not counts[BacktestStatus.queued.value] and not counts[BacktestStatus.running.value]
    return SweepProgress(**{status.value: counts[status.value] for status in BacktestStatus}), done


def _listed(sweep: Sweep, rows: Sequence[Listed]) -> SweepListed:
    progress, done = _progress(rows)
    stats = [
        row.backtest.stats if row.backtest.status == BacktestStatus.finished.value else None
        for row in rows
    ]
    ranked = ranks(stats, sweep.metric)
    best = None
    if 1 in ranked:
        backtest, values = rows[ranked.index(1)].backtest, stats[ranked.index(1)]
        assert values is not None  # a ranked backtest has its statistics
        best = SweepBest(
            id=backtest.id,
            number=backtest.number,
            name=backtest.shown_name,
            value=values[sweep.metric],
        )
    return SweepListed(
        id=sweep.id,
        created_at=sweep.created_at,
        metric=SweepMetric(sweep.metric),
        count=len(rows),
        done=done,
        progress=progress,
        best=best,
    )


def _backtest_out(row: Listed, setup: dict[str, Any]) -> SweepBacktest:
    backtest = row.backtest
    return SweepBacktest(
        id=backtest.id,
        number=backtest.number,
        name=backtest.shown_name,
        status=BacktestStatus(backtest.status),
        queue_position=row.queue_position,
        error=backtest.error,
        setup=SweepSetup.model_validate(
            {key: value for key, value in setup.items() if key != "backtest_id"}
        ),
    )


def ranking(rows: Sequence[Listed], metric: SweepMetric) -> SweepRanking:
    """The backtests by ``metric``, the best first; those without a value last, in their
    order. Only a finished backtest has values."""
    stats = [
        row.backtest.stats if row.backtest.status == BacktestStatus.finished.value else None
        for row in rows
    ]
    shown = next(each for each in metric_rows(stats) if each.key == metric.value)
    ranked = ranks(stats, metric.value)
    order = sorted(
        range(len(rows)), key=lambda index: (ranked[index] is None, ranked[index] or 0, index)
    )
    assert shown.better != "none"  # a SweepMetric has a direction
    return SweepRanking(
        metric=metric,
        label=shown.label,
        better=shown.better,
        backtests=[
            SweepRanked(
                rank=ranked[index],
                id=rows[index].backtest.id,
                number=rows[index].backtest.number,
                name=rows[index].backtest.shown_name,
                value=shown.values[index],
                mark=shown.marks[index],
            )
            for index in order
        ],
    )
