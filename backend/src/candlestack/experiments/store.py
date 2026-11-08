"""The backtests table in Postgres: the queries the service and the seed command need, in the
given session. Nothing here commits; the caller does, once a change is complete.

A backtest shows the current name of its model while the model exists, else the name it had
when the backtest ran: ``MODEL_NAME``, over a left join of ``models``.

Experiments reads the published backtests of every account (``PUBLISHED``) with the display
name of their author, joined from ``users`` (``accounts.User``, through the package root).
It never reads the author's email, and shows the model's name of when the backtest ran.
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Select,
    String,
    TableValuedAlias,
    UnaryExpression,
    case,
    cast,
    column,
    delete,
    exists,
    func,
    literal,
    select,
    text,
    true,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, array_agg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased, undefer
from sqlalchemy.sql import visitors

from candlestack.accounts import User
from candlestack.experiments.models import Backtest
from candlestack.experiments.names import generated_name
from candlestack.experiments.schemas import (
    BacktestShow,
    BacktestSort,
    BacktestStatus,
    ExperimentSort,
    InstrumentMarket,
    Representation,
    SetupTimeframe,
    SortOrder,
)
from candlestack.experiments.steps import Steps, failed
from candlestack.ml import Model, ModelUsageStats

# A column of the table, or an expression over the columns.
type SortKey = InstrumentedAttribute[Any] | ColumnElement[Any]

MODEL_NAME = func.coalesce(Model.name, Backtest.model_name)
# What a backtest is called (``Backtest.shown_name``): the user's own name, else the generated.
SHOWN_NAME = func.coalesce(Backtest.name, Backtest.generated_name)
# How often a new backtest draws its generated name again when the unique index refuses it.
NAME_ATTEMPTS = 3
_NAME_INDEX = "uq_backtests_user_id_generated_name"

_FINISHED = Backtest.status == BacktestStatus.finished.value
_FAILED = Backtest.status == BacktestStatus.failed.value
_RUNNING = Backtest.status == BacktestStatus.running.value
_QUEUED = Backtest.status == BacktestStatus.queued.value
# The most backtests a user can have waiting in their queue, besides the one running.
QUEUE_MAX = 20
# Only a finished backtest can be published, and it stays finished; the status is checked
# anyway, so nothing else can ever show in Experiments.
PUBLISHED = Backtest.published_at.is_not(None) & _FINISHED

_AHEAD = aliased(Backtest)
# Where a queued backtest is in its user's queue (1 starts next), null for any other: the user's
# queued backtests up to its number, the oldest first.
QUEUE_POSITION = case(
    (
        _QUEUED,
        select(func.count())
        .where(
            _AHEAD.user_id == Backtest.user_id,
            _AHEAD.status == BacktestStatus.queued.value,
            _AHEAD.number <= Backtest.number,
        )
        .correlate(Backtest)
        .scalar_subquery(),
    ),
    else_=None,
)

# The value each sort of the list orders by; `when` is the order they were made in, as `number`.
_SORT_KEYS: dict[BacktestSort, SortKey] = {
    BacktestSort.number: Backtest.number,
    BacktestSort.when: Backtest.number,
    BacktestSort.name: func.lower(SHOWN_NAME),
    BacktestSort.model: func.lower(MODEL_NAME),
    BacktestSort.total_return: Backtest.total_return,
    BacktestSort.sharpe: Backtest.sharpe,
    BacktestSort.max_drawdown: Backtest.max_drawdown,
    BacktestSort.trades: Backtest.num_trades,
}


# The value each sort of Experiments orders by (`newest` aside).
_EXPERIMENT_SORT_KEYS: dict[ExperimentSort, SortKey] = {
    ExperimentSort.name: func.lower(SHOWN_NAME),
    ExperimentSort.total_return: Backtest.total_return,
    ExperimentSort.sharpe: Backtest.sharpe,
    ExperimentSort.max_drawdown: Backtest.max_drawdown,
    ExperimentSort.trades: Backtest.num_trades,
}


class Listed(NamedTuple):
    """A backtest, the name its model shows (``MODEL_NAME``) and where it is in the queue
    while it is queued (``QUEUE_POSITION``)."""

    backtest: Backtest
    model_name: str
    queue_position: int | None = None


class Authored(NamedTuple):
    """A backtest and its author's display name."""

    backtest: Backtest
    author: str


class Counts(NamedTuple):
    all: int
    starred: int
    failed: int
    public: int


class ExperimentCounts(NamedTuple):
    all: int
    mine: int
    people: int


class Facets(NamedTuple):
    """What the filters of Experiments offer: each value and its published backtests."""

    markets: dict[str, int]
    instruments: list[tuple[str, str, int]]
    representations: dict[str, int]
    timeframes: dict[str, int]


class ModelCount(NamedTuple):
    name: str
    framework: str
    count: int


class Namesake(NamedTuple):
    """A backtest of the user that has a name (``BacktestStore.namesakes``): its shown name and
    whether its generated name is the one."""

    id: str
    number: int
    name: str
    generated: bool


class UserModel(NamedTuple):
    id: str
    name: str
    framework: str


def escape_like(value: str) -> str:
    r"""`value` matched literally by LIKE with the escape character ``\``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _instruments(name: str = "instrument") -> TableValuedAlias:
    """The backtest's instruments, one row each (``value``, the JSONB object)."""
    return (
        func.jsonb_array_elements(Backtest.instruments)
        .table_valued(column("value", JSONB))
        .alias(name)
    )


def _any_instrument(
    matches: Callable[[ColumnElement[Any]], ColumnElement[bool]],
) -> ColumnElement[bool]:
    """Backtests with an instrument whose JSONB object meets ``matches``."""
    instrument = _instruments()
    return exists(select(literal(1)).select_from(instrument).where(matches(instrument.c.value)))


def search(query: str) -> ColumnElement[bool] | None:
    """Backtests whose shown name, ``#number``, an instrument's symbol or the model's name
    contains `query`, in any case; None for a blank query, which every backtest matches."""
    query = query.strip()
    if not query:
        return None
    pattern = f"%{escape_like(query)}%"
    symbol = _any_instrument(lambda value: value["symbol"].astext.ilike(pattern, escape="\\"))
    number = (literal("#") + cast(Backtest.number, String)).ilike(pattern, escape="\\")
    name = SHOWN_NAME.ilike(pattern, escape="\\")
    return number | name | symbol | MODEL_NAME.ilike(pattern, escape="\\")


def filters(
    *, query: str = "", show: BacktestShow = BacktestShow.all, model: str | None = None
) -> list[ColumnElement[bool]]:
    """The conditions of the list's search and filters (all of them must hold)."""
    conditions: list[ColumnElement[bool]] = []
    if (matches := search(query)) is not None:
        conditions.append(matches)
    if show is BacktestShow.starred:
        conditions.append(Backtest.starred.is_(True))
    elif show is BacktestShow.failed:
        conditions.append(_FAILED)
    elif show is BacktestShow.public:
        conditions.append(PUBLISHED)
    if model is not None:
        conditions.append(func.lower(MODEL_NAME) == func.lower(model))
    return conditions


# --- Experiments --------------------------------------------------------------------------------


def experiment_search(query: str) -> ColumnElement[bool] | None:
    """Published backtests whose shown name, instrument's symbol, model's name (as it was when
    the backtest ran) or author's display name contains `query`, in any case; None for a blank
    query."""
    query = query.strip()
    if not query:
        return None
    pattern = f"%{escape_like(query)}%"
    name = SHOWN_NAME.ilike(pattern, escape="\\")
    symbol = _any_instrument(lambda value: value["symbol"].astext.ilike(pattern, escape="\\"))
    model = Backtest.model_name.ilike(pattern, escape="\\")
    return name | symbol | model | User.display_name.ilike(pattern, escape="\\")


def experiment_scope(
    viewer: str, *, query: str = "", mine: bool = False, author: str | None = None
) -> list[ColumnElement[bool]]:
    """The published backtests of the scope (everyone's, the viewer's or one author's) that
    match the search: what the facets count, whatever the filters."""
    conditions = [PUBLISHED]
    if mine:
        conditions.append(Backtest.user_id == viewer)
    if author is not None:
        conditions.append(Backtest.user_id == author)
    if (matches := experiment_search(query)) is not None:
        conditions.append(matches)
    return conditions


def experiment_filters(
    *,
    market: InstrumentMarket | None = None,
    symbol: str | None = None,
    representation: Representation | None = None,
    timeframe: SetupTimeframe | None = None,
) -> list[ColumnElement[bool]]:
    """The conditions of the filters of Experiments (all of them must hold)."""
    conditions: list[ColumnElement[bool]] = []
    if market is not None:
        name = market.value
        conditions.append(_any_instrument(lambda value: value["market"].astext == name))
    if symbol is not None:
        wanted = symbol.strip().upper()
        conditions.append(
            _any_instrument(lambda value: func.upper(value["symbol"].astext) == wanted)
        )
    if representation is not None:
        conditions.append(Backtest.representation == representation.value)
    if timeframe is not None:
        conditions.append(Backtest.setup["timeframe"].astext == timeframe.value)
    return conditions


def experiment_ordering(sort: ExperimentSort, order: SortOrder) -> list[UnaryExpression[Any]]:
    """The ORDER BY of Experiments: `newest` is the newest published first whatever `order`
    says; the others put those without the value last in both directions, ties newest
    published first."""
    newest = [Backtest.published_at.desc(), Backtest.id.desc()]
    if sort is ExperimentSort.newest:
        return newest
    key = _EXPERIMENT_SORT_KEYS[sort]
    direction = key.asc() if order is SortOrder.asc else key.desc()
    return [direction.nulls_last(), *newest]


def ordering(sort: BacktestSort, order: SortOrder) -> list[UnaryExpression[Any]]:
    """The ORDER BY of the list. Backtests without the sorted value (failed ones) come last in
    both directions, ties newest first; `default` is newest first whatever `order` says."""
    if sort is BacktestSort.default:
        return [Backtest.created_at.desc(), Backtest.number.desc()]
    key = _SORT_KEYS[sort]
    direction = key.asc() if order is SortOrder.asc else key.desc()
    return [direction.nulls_last(), Backtest.number.desc()]


def _reads_models(conditions: Sequence[ColumnElement[bool]]) -> bool:
    """Whether a condition reads a column of the models table (the model's shown name)."""
    return any(
        getattr(element, "table", None) is Model.__table__
        for condition in conditions
        for element in visitors.iterate(condition)
    )


def _listed() -> Select[Backtest, str, int | None]:
    return select(Backtest, MODEL_NAME, QUEUE_POSITION).outerjoin(
        Model, Model.id == Backtest.model_id
    )


def _authored() -> Select[Backtest, str]:
    return select(Backtest, User.display_name).join(User, User.id == Backtest.user_id)


async def model_usage(
    db: AsyncSession, user_id: str, model_ids: Sequence[str], /
) -> Mapping[str, ModelUsageStats]:
    """How many backtests used each of the user's models (failed ones included) and the best
    Sharpe ratio of its finished ones, in one query (``ml.ModelUsage``)."""
    rows = await db.execute(
        select(
            Backtest.model_id,
            func.count(),
            func.max(Backtest.sharpe).filter(_FINISHED),
        )
        .where(Backtest.user_id == user_id, Backtest.model_id.in_(model_ids))
        .group_by(Backtest.model_id)
    )
    return {
        model_id: ModelUsageStats(count, best)
        for model_id, count, best in rows
        if model_id is not None
    }


class BacktestStore:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def commit(self) -> None:
        await self._db.commit()

    async def lock_user(self, user_id: str) -> None:
        """Takes the lock of the user's backtests until the transaction ends, so two writers
        cannot give two backtests one number. An advisory lock, as the rows may not exist yet."""
        await self._db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"experiments:backtests:{user_id}"},
        )

    async def counts(self, user_id: str) -> Counts:
        """The user's backtests in all, starred, failed and published."""
        row = (
            await self._db.execute(
                select(
                    func.count(),
                    func.count().filter(Backtest.starred.is_(True)),
                    func.count().filter(_FAILED),
                    func.count().filter(PUBLISHED),
                ).where(Backtest.user_id == user_id)
            )
        ).one()
        return Counts(*row)

    async def model_counts(self, user_id: str) -> list[ModelCount]:
        """The models the user's backtests used, one per name they show in any case, as the
        model filter compares them: spelled as and with the framework of the newest backtest
        of the name, the most used first, then by name."""
        key = func.lower(MODEL_NAME)
        newest = (Backtest.created_at.desc(), Backtest.number.desc())
        name = array_agg(aggregate_order_by(MODEL_NAME, *newest))[1]
        framework = array_agg(aggregate_order_by(Backtest.model_framework, *newest))[1]
        count = func.count().label("count")
        rows = await self._db.execute(
            select(name, framework, count)
            .outerjoin(Model, Model.id == Backtest.model_id)
            .where(Backtest.user_id == user_id)
            .group_by(key)
            .order_by(count.desc(), key)
        )
        return [ModelCount(*row) for row in rows]

    async def count(self, user_id: str, conditions: Sequence[ColumnElement[bool]]) -> int:
        """The user's backtests that meet the conditions; models joined only when a condition
        reads them."""
        query = select(func.count()).select_from(Backtest)
        if _reads_models(conditions):
            query = query.outerjoin(Model, Model.id == Backtest.model_id)
        total = await self._db.scalar(query.where(Backtest.user_id == user_id, *conditions))
        return int(total or 0)

    async def backtests(
        self,
        user_id: str,
        conditions: Sequence[ColumnElement[bool]],
        order_by: Sequence[UnaryExpression[Any]],
        *,
        limit: int,
        offset: int = 0,
    ) -> list[Listed]:
        """The user's backtests that meet the conditions, in that order, from `offset` on."""
        rows = await self._db.execute(
            _listed()
            .where(Backtest.user_id == user_id, *conditions)
            .order_by(*order_by)
            .limit(limit)
            .offset(offset)
        )
        return [Listed(*row) for row in rows]

    async def best(self, user_id: str, result: SortKey, *, limit: int) -> list[Listed]:
        """The user's finished backtests with the highest `result`, ties newest first."""
        return await self.backtests(
            user_id,
            [_FINISHED, result.is_not(None)],
            [result.desc(), Backtest.number.desc()],
            limit=limit,
        )

    async def backtest(self, user_id: str, backtest_id: str) -> Listed | None:
        """The user's backtest with the ID; None also when it is another user's."""
        row = (
            await self._db.execute(
                _listed().where(Backtest.id == backtest_id, Backtest.user_id == user_id)
            )
        ).first()
        return Listed(*row) if row is not None else None

    async def has_spec(self, backtest_id: str) -> bool:
        """Whether the backtest was made in the app, so it has a result document (the seed
        command's have none)."""
        found = await self._db.scalar(
            select(Backtest.spec.is_not(None)).where(Backtest.id == backtest_id)
        )
        return bool(found)

    # --- The names --------------------------------------------------------------------------------

    async def names(self, user_id: str) -> set[str]:
        """Every name of the user's backtests, generated and custom, lowercased by Postgres as
        the unique index of the generated names compares them."""
        rows = await self._db.execute(
            select(func.lower(Backtest.generated_name), func.lower(Backtest.name)).where(
                Backtest.user_id == user_id
            )
        )
        return {name for row in rows for name in row if name is not None}

    async def namesakes(self, user_id: str, name: str) -> list[Namesake]:
        """The user's backtests whose custom or generated name is `name`, in any case, the
        oldest first."""
        lowered = func.lower(name)
        rows = await self._db.execute(
            select(
                Backtest.id,
                Backtest.number,
                SHOWN_NAME,
                func.lower(Backtest.generated_name) == lowered,
            )
            .where(
                Backtest.user_id == user_id,
                (func.lower(Backtest.name) == lowered)
                | (func.lower(Backtest.generated_name) == lowered),
            )
            .order_by(Backtest.number)
        )
        return [Namesake(*row) for row in rows]

    # --- What Experiments needs ------------------------------------------------------------------

    async def experiments(
        self,
        conditions: Sequence[ColumnElement[bool]],
        order_by: Sequence[UnaryExpression[Any]],
        *,
        limit: int,
        offset: int = 0,
    ) -> list[Authored]:
        """The backtests of any account that meet the conditions (``experiment_scope``), with
        their author's display name, in that order, from `offset` on."""
        rows = await self._db.execute(
            _authored().where(*conditions).order_by(*order_by).limit(limit).offset(offset)
        )
        return [Authored(backtest, author) for backtest, author in rows]

    async def experiment_total(self, conditions: Sequence[ColumnElement[bool]]) -> int:
        """The backtests of any account that meet the conditions."""
        total = await self._db.scalar(
            select(func.count())
            .select_from(Backtest)
            .join(User, User.id == Backtest.user_id)
            .where(*conditions)
        )
        return int(total or 0)

    async def experiment_counts(self, viewer: str) -> ExperimentCounts:
        """Every published backtest, the viewer's and the accounts that published one."""
        row = (
            await self._db.execute(
                select(
                    func.count(),
                    func.count().filter(Backtest.user_id == viewer),
                    func.count(Backtest.user_id.distinct()),
                ).where(PUBLISHED)
            )
        ).one()
        return ExperimentCounts(*row)

    async def facets(self, conditions: Sequence[ColumnElement[bool]]) -> Facets:
        """The markets, instruments, representations and timeframes of the backtests that meet
        the conditions, each with how many have it: the instruments the most published first,
        then by symbol."""
        # Named apart from the instruments a search looks through.
        instrument = _instruments("each_instrument").lateral()
        market = instrument.c.value["market"].astext
        symbol = instrument.c.value["symbol"].astext
        count = func.count(Backtest.id.distinct()).label("count")
        timeframe = Backtest.setup["timeframe"].astext

        def counted(*keys: SortKey, each: bool = False) -> Select[*tuple[Any, ...]]:
            """The backtests of each value of ``keys``; ``each`` counts them over their
            instruments, once each."""
            query = select(*keys, count if each else func.count()).select_from(Backtest)
            query = query.join(User, User.id == Backtest.user_id)
            if each:
                query = query.join(instrument, true())
            return query.where(*conditions).group_by(*keys)

        async def by_value(query: Select[*tuple[Any, ...]]) -> dict[str, int]:
            return {row[0]: row[1] for row in await self._db.execute(query)}

        instruments = await self._db.execute(
            counted(market, symbol, each=True).order_by(count.desc(), symbol, market)
        )
        return Facets(
            markets=await by_value(counted(market, each=True)),
            instruments=[(row[0], row[1], row[2]) for row in instruments],
            representations=await by_value(counted(Backtest.representation)),
            timeframes=await by_value(counted(timeframe)),
        )

    # --- What the result document needs --------------------------------------------------------

    async def author_name(self, user_id: str) -> str:
        """The user's display name, what others see of the author of a backtest."""
        return (
            await self._db.execute(select(User.display_name).where(User.id == user_id))
        ).scalar_one()

    # --- What starting and running a backtest needs --------------------------------------------

    async def add_named(self, backtest: Backtest) -> None:
        """Adds the backtest with a generated name none of the user's backtests has. Taken under
        ``lock_user``, the name is free; should the unique index still refuse it (a writer that
        did not take the lock), another is drawn, ``NAME_ATTEMPTS`` times at most.

        Raises:
            IntegrityError: The row breaks another constraint, or no draw found a free name.
        """
        attempt = 1
        while True:
            backtest.generated_name = generated_name(await self.names(backtest.user_id))
            try:
                async with self._db.begin_nested():
                    self._db.add(backtest)
            except IntegrityError as error:
                if attempt == NAME_ATTEMPTS or _NAME_INDEX not in str(error.orig):
                    raise
            else:
                return
            attempt += 1

    async def running(self, user_id: str) -> bool:
        """Whether a backtest of the user is running."""
        found = await self._db.scalar(
            select(Backtest.id).where(Backtest.user_id == user_id, _RUNNING).limit(1)
        )
        return found is not None

    async def queued(self, user_id: str) -> int:
        """How many backtests of the user wait in their queue."""
        total = await self._db.scalar(
            select(func.count()).select_from(Backtest).where(Backtest.user_id == user_id, _QUEUED)
        )
        return int(total or 0)

    async def queue_position(self, backtest_id: str) -> int | None:
        """Where the backtest is in its user's queue (1 starts next); None unless it is
        queued."""
        return await self._db.scalar(select(QUEUE_POSITION).where(Backtest.id == backtest_id))

    async def take_next(self, user_id: str, now: datetime) -> Backtest | None:
        """Marks the user's oldest queued backtest running from ``now`` and returns it, while
        none of theirs runs; else None. Called under ``lock_user``, so two callers never start
        two."""
        if await self.running(user_id):
            return None
        backtest = await self._db.scalar(
            select(Backtest)
            .where(Backtest.user_id == user_id, _QUEUED)
            .order_by(Backtest.number)
            .limit(1)
            .with_for_update()
        )
        if backtest is not None:
            backtest.status = BacktestStatus.running.value
            backtest.started_at = now
        return backtest

    async def requeue(self, backtest_id: str) -> None:
        """Puts the running backtest that never began back in its queue, where its number
        keeps it first."""
        await self._db.execute(
            update(Backtest)
            .where(Backtest.id == backtest_id, _RUNNING)
            .values(status=BacktestStatus.queued.value, started_at=None)
        )

    async def waiting(self) -> list[str]:
        """The users with a queued backtest and none running: whose queue should move on."""
        running = select(literal(1)).where(
            _AHEAD.user_id == Backtest.user_id, _AHEAD.status == BacktestStatus.running.value
        )
        rows = await self._db.scalars(
            select(Backtest.user_id).where(_QUEUED, ~exists(running)).distinct()
        )
        return list(rows)

    async def user_of(self, backtest_id: str) -> str | None:
        """The ID of the backtest's user; None once it is gone (the account was deleted)."""
        return await self._db.scalar(select(Backtest.user_id).where(Backtest.id == backtest_id))

    async def cancel(self, backtest: Backtest, error: str, now: datetime) -> None:
        """Fails the queued backtest with ``error``: it never ran, so its steps stay pending
        and it took no time."""
        backtest.status = BacktestStatus.failed.value
        backtest.error = error
        backtest.finished_at = now

    async def run_model(self, user_id: str, model_id: str) -> Model | None:
        """The user's model with the ID and its manifest; None also when it is another user's."""
        return await self._db.scalar(
            select(Model)
            .options(undefer(Model.manifest))
            .where(Model.id == model_id, Model.user_id == user_id)
        )

    async def hold_model(self, user_id: str, model_id: str) -> bool:
        """Whether the user still has the model; it cannot be deleted until the transaction
        ends (a backtest stored with it would fail its foreign key)."""
        found = await self._db.scalar(
            select(Model.id)
            .where(Model.id == model_id, Model.user_id == user_id)
            .with_for_update(read=True, key_share=True)
        )
        return found is not None

    async def model_of(self, backtest_id: str) -> str | None:
        """The ID of the backtest's model; None once the model is deleted."""
        return await self._db.scalar(select(Backtest.model_id).where(Backtest.id == backtest_id))

    async def with_run(
        self, user_id: str, backtest_id: str, *, curves: bool = False
    ) -> Authored | None:
        """The user's backtest, or another account's while it is published, with what its run
        stored, its predictions aside (and its curves unless ``curves``), and its author's
        display name; None for any other."""
        stored = [Backtest.spec, Backtest.report, Backtest.stats, Backtest.steps]
        if curves:
            stored.append(Backtest.curves)
        row = (
            await self._db.execute(
                _authored()
                .options(*(undefer(column) for column in stored))
                .where(Backtest.id == backtest_id, (Backtest.user_id == user_id) | PUBLISHED)
            )
        ).first()
        return Authored(*row) if row is not None else None

    async def run_of(self, backtest_id: str) -> Backtest | None:
        """The backtest with its setup, whoever it is of: what the run reads when it starts."""
        return await self._db.scalar(
            select(Backtest).options(undefer(Backtest.spec)).where(Backtest.id == backtest_id)
        )

    async def predictions(self, backtest_id: str) -> bytes | None:
        return await self._db.scalar(select(Backtest.predictions).where(Backtest.id == backtest_id))

    async def set_steps(self, backtest_id: str, steps: Steps) -> None:
        """Stores the steps of the backtest while it is running (one that has ended keeps
        those it ended with)."""
        await self._db.execute(
            update(Backtest).where(Backtest.id == backtest_id, _RUNNING).values(steps=steps)
        )

    async def fail(
        self,
        error: str,
        now: datetime,
        backtest_id: str | None = None,
        *,
        before: datetime | None = None,
    ) -> int:
        """Marks the running backtest with the ID (every running one without an ID, or every
        one that began to run before ``before``) failed with ``error``, and the step it was at;
        how many. One that has ended keeps what it has."""
        query = select(Backtest.id, Backtest.steps).where(_RUNNING).with_for_update()
        if backtest_id is not None:
            query = query.where(Backtest.id == backtest_id)
        if before is not None:
            began = func.coalesce(Backtest.started_at, Backtest.created_at)
            query = query.where(began < before)
        running = (await self._db.execute(query)).all()
        for found, steps in running:
            values: dict[str, Any] = {
                "status": BacktestStatus.failed.value,
                "error": error,
                "finished_at": now,
                "duration_s": func.extract(
                    "epoch",
                    literal(now, DateTime(timezone=True))
                    - func.coalesce(Backtest.started_at, Backtest.created_at),
                ),
            }
            # Without steps (stored before they were recorded) the column stays null.
            if (marked := failed(steps, now)) is not None:
                values["steps"] = marked
            await self._db.execute(update(Backtest).where(Backtest.id == found).values(values))
        return len(running)

    # --- What the seed command needs ----------------------------------------------------------

    async def user_id(self, email: str) -> str | None:
        """The ID of the user with the email, in any case."""
        return await self._db.scalar(select(User.id).where(User.email == email.strip().lower()))

    async def models(self, user_id: str) -> list[UserModel]:
        rows = await self._db.execute(
            select(Model.id, Model.name, Model.framework).where(Model.user_id == user_id)
        )
        return [UserModel(*row) for row in rows]

    async def delete_seeded(self, user_id: str) -> int:
        """Deletes the backtests the seed command made for the user; how many."""
        deleted = await self._db.scalars(
            delete(Backtest)
            .where(Backtest.user_id == user_id, Backtest.seeded.is_(True))
            .returning(Backtest.id)
        )
        return len(deleted.all())

    async def last_number(self, user_id: str) -> int:
        """The highest number of the user's backtests; 0 without any."""
        last = await self._db.scalar(
            select(func.coalesce(func.max(Backtest.number), 0)).where(Backtest.user_id == user_id)
        )
        return int(last or 0)

    def add_all(self, backtests: Sequence[Backtest]) -> None:
        self._db.add_all(backtests)
