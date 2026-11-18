"""The backtests table in Postgres: the queries the service and the seed command need, in the
given session. Nothing here commits; the caller does, once a change is complete.

A backtest shows the current name of its model while the model exists, else the name it had
when the backtest ran: ``MODEL_NAME``, over a left join of ``models``.

Experiments reads the published backtests of every account (``PUBLISHED``) with the display
name of their author, joined from ``users`` (``accounts.User``, through the package root).
It never reads the author's email, and shows the model's name of when the backtest ran.
"""

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
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
    or_,
    select,
    text,
    true,
    type_coerce,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, array_agg
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, aliased, load_only, undefer
from sqlalchemy.sql import visitors

from candlestack.accounts import User
from candlestack.experiments.models import SEARCH_TEXT, Backtest, Sweep
from candlestack.experiments.names import candidates, generated_name
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
# How many random names a new backtest looks up at once for a free one (``free_name``).
NAME_CANDIDATES = 10
_NAME_INDEX = "uq_backtests_user_id_generated_name"

_FINISHED = Backtest.status == BacktestStatus.finished.value
_FAILED = Backtest.status == BacktestStatus.failed.value
_RUNNING = Backtest.status == BacktestStatus.running.value
_QUEUED = Backtest.status == BacktestStatus.queued.value
# The most backtests a user can have waiting in their queue, besides the one running.
QUEUE_MAX = 20
# How many of a user's last finished runs an estimate of a wait counts with (``run_seconds``).
RECENT_RUNS = 20
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
    BacktestSort.win_rate: Backtest.win_rate,
    # Read from the small statistics document: a user's backtests are a few thousand at most,
    # so a column of its own would buy nothing.
    BacktestSort.sortino: Backtest.stats["sortino"].as_float(),
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


class Compared(NamedTuple):
    """What the comparison reads of a backtest (``BacktestStore.compared``): the backtest, its
    author's display name, the parts of its setup the comparison shows (``setup``: the model's
    name as ``model``, the representation it reads as ``reads``, its input's shape as
    ``input``, and the ``data``, ``decision``, ``execution`` and ``benchmarks`` groups as
    stored; None for test data without a spec), the rows its run found (``report.rows``), its
    statistics and its curves (None unless asked for)."""

    backtest: Backtest
    author: str
    setup: dict[str, Any] | None
    rows: dict[str, Any] | None
    stats: dict[str, Any] | None
    curves: bytes | None


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


class Doomed(NamedTuple):
    """A backtest about to be deleted (``BacktestStore.to_delete``)."""

    id: str
    status: str
    sweep_id: str | None


class Marked(NamedTuple):
    """A backtest about to be starred or published (``BacktestStore.to_mark``)."""

    id: str
    status: str
    has_spec: bool


class StatusRow(NamedTuple):
    """Where a backtest stands (``BacktestStore.statuses``): its row without what its run
    stored, its author's display name, its place in the queue while queued, and the timings
    of its report only when it has no steps."""

    id: str
    number: int
    name: str
    generated_name: str
    user_id: str
    author: str
    published_at: datetime | None
    sweep_id: str | None
    status: str
    queue_position: int | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_s: float | None
    error: str | None
    steps: list[dict[str, Any]] | None
    timings: dict[str, float | None] | None
    has_spec: bool


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


# Three letters or digits in a row: what pg_trgm needs of a query to look it up by its index.
_TRIGRAM = re.compile(r"[^\W_]{3}")


def indexed(query: str) -> bool:
    """Whether the trigram index of ``SEARCH_TEXT`` can look `query` up. One without three
    letters or digits in a row has no trigram, and nearly every backtest contains it: the
    search then reads the user's backtests, as working out ``SEARCH_TEXT`` for each would only
    cost time."""
    return _TRIGRAM.search(query) is not None


def search(query: str, models: Sequence[str] | None = None) -> ColumnElement[bool] | None:
    """Backtests whose shown name, ``#number``, an instrument's symbol or the model's name
    contains `query`, in any case; None for a blank query, which every backtest matches.

    With `models`, the IDs of the user's models whose name contains `query`
    (``BacktestStore.models_named``), Postgres looks the backtests that may match up first: by
    the trigram index of ``SEARCH_TEXT``, or as a backtest of one of those models, whose name
    shows from the models table, which no index of the backtests holds. The exact condition then
    decides among them, so what the search finds is the same either way.
    """
    query = query.strip()
    if not query:
        return None
    pattern = f"%{escape_like(query)}%"
    symbol = _any_instrument(lambda value: value["symbol"].astext.ilike(pattern, escape="\\"))
    number = (literal("#") + cast(Backtest.number, String)).ilike(pattern, escape="\\")
    name = SHOWN_NAME.ilike(pattern, escape="\\")
    matches = number | name | symbol | MODEL_NAME.ilike(pattern, escape="\\")
    if models is None:
        return matches
    # The IDs as values rather than a subquery: Postgres knows how many backtests they have.
    found = SEARCH_TEXT.ilike(pattern, escape="\\") | Backtest.model_id.in_(models)
    return found & matches


@dataclass(frozen=True)
class BacktestFilters:
    """The filters of the list beyond the Backtests page's own (search, show, model), for the
    API and the agents; a value left None filters nothing. Every condition also holds the
    user's ID, so Postgres reads only the user's rows (an index of each kind leads with it),
    a few thousand at most: no filter needs an index of its own."""

    statuses: tuple[BacktestStatus, ...] = ()
    sweep_id: str | None = None
    model_id: str | None = None
    # ``<market>:<symbol>`` as ``InstrumentId`` writes it: lowercase market, uppercase symbol.
    instrument: str | None = None
    timeframe: SetupTimeframe | None = None
    representation: Representation | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    min_sharpe: float | None = None
    min_total_return: float | None = None
    # The deepest fall allowed: max_drawdown is 0 or below, so -0.2 keeps falls up to 20 %.
    min_max_drawdown: float | None = None
    starred: bool | None = None
    public: bool | None = None

    def conditions(self) -> list[ColumnElement[bool]]:
        """The condition of each filter that is set."""
        market, _, symbol = (self.instrument or "").partition(":")
        timeframe = Backtest.setup["timeframe"].astext
        each: list[tuple[object, Callable[[], ColumnElement[bool]]]] = [
            (self.statuses or None, lambda: Backtest.status.in_(self.statuses)),
            (self.sweep_id, lambda: Backtest.sweep_id == self.sweep_id),
            (self.model_id, lambda: Backtest.model_id == self.model_id),
            (
                self.instrument,
                lambda: Backtest.instruments.contains([{"market": market, "symbol": symbol}]),
            ),
            (self.timeframe, lambda: timeframe == self.timeframe),
            (self.representation, lambda: Backtest.representation == self.representation),
            (self.created_after, lambda: Backtest.created_at >= self.created_after),
            (self.created_before, lambda: Backtest.created_at < self.created_before),
            (self.min_sharpe, lambda: Backtest.sharpe >= self.min_sharpe),
            (self.min_total_return, lambda: Backtest.total_return >= self.min_total_return),
            (self.min_max_drawdown, lambda: Backtest.max_drawdown >= self.min_max_drawdown),
            (self.starred, lambda: Backtest.starred.is_(self.starred)),
            (
                self.public,
                lambda: PUBLISHED if self.public else Backtest.published_at.is_(None),
            ),
        ]
        return [condition() for value, condition in each if value is not None]


def filters(
    *,
    query: str = "",
    models: Sequence[str] | None = None,
    show: BacktestShow = BacktestShow.all,
    model: str | None = None,
    more: BacktestFilters | None = None,
) -> list[ColumnElement[bool]]:
    """The conditions of the list's search (with `models` as ``search`` takes them) and filters
    (all of them must hold)."""
    conditions: list[ColumnElement[bool]] = []
    if (matches := search(query, models)) is not None:
        conditions.append(matches)
    if show is BacktestShow.starred:
        conditions.append(Backtest.starred.is_(True))
    elif show is BacktestShow.failed:
        conditions.append(_FAILED)
    elif show is BacktestShow.public:
        conditions.append(PUBLISHED)
    if model is not None:
        conditions.append(func.lower(MODEL_NAME) == func.lower(model))
    if more is not None:
        conditions.extend(more.conditions())
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


# What the comparison shows of a backtest's setup, cut from its spec in Postgres: the copy of
# the model's manifest stays in the database.
_SPEC = Backtest.spec
_COMPARED_SETUP = case(
    (_SPEC.is_(None), None),
    else_=type_coerce(
        func.jsonb_build_object(
            "model",
            _SPEC[("model", "name")],
            "reads",
            _SPEC[("model", "manifest", "input", "representation", "id")],
            "input",
            _SPEC[("model", "manifest", "signature", "input", "shape")],
            "data",
            _SPEC["data"],
            "decision",
            _SPEC["decision"],
            "execution",
            _SPEC["execution"],
            "benchmarks",
            _SPEC["benchmarks"],
        ),
        JSONB,
    ),
)


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

    async def models_named(self, user_id: str, query: str) -> list[str]:
        """The IDs of the user's models whose name contains `query` in any case, as the search
        compares it (``search``)."""
        pattern = f"%{escape_like(query.strip())}%"
        found = await self._db.scalars(
            select(Model.id).where(Model.user_id == user_id, Model.name.ilike(pattern, escape="\\"))
        )
        return list(found)

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

    async def to_mark(self, user_id: str, backtest_ids: Sequence[str]) -> list[Marked]:
        """The user's backtests with one of the IDs, locked until the transaction ends (a run
        that is finishing stores its status first), without what their runs stored. They are
        locked by number, as ``to_delete`` locks them, so the two never wait on each other."""
        rows = await self._db.execute(
            select(Backtest.id, Backtest.status, Backtest.spec.is_not(None))
            .where(Backtest.user_id == user_id, Backtest.id.in_(backtest_ids))
            .order_by(Backtest.number)
            .with_for_update()
        )
        return [Marked(*row) for row in rows]

    async def mark(
        self,
        user_id: str,
        backtest_ids: Sequence[str],
        *,
        starred: bool | None,
        public: bool | None,
    ) -> None:
        """Stars or unstars the user's backtests, and publishes or takes them back, as each
        field that is not None asks; one published already keeps when it was published, and a
        row already as asked is not written again."""
        values: dict[str, Any] = {}
        differs: list[ColumnElement[bool]] = []
        if starred is not None:
            values["starred"] = starred
            differs.append(Backtest.starred.is_distinct_from(starred))
        if public is not None:
            values["published_at"] = (
                func.coalesce(Backtest.published_at, func.now()) if public else None
            )
            published = Backtest.published_at.is_not(None)
            differs.append(~published if public else published)
        await self._db.execute(
            update(Backtest)
            .where(Backtest.user_id == user_id, Backtest.id.in_(backtest_ids), or_(*differs))
            .values(values)
        )

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

    async def free_name(self, user_id: str) -> str:
        """A generated name none of the user's backtests has, as its own or its generated name:
        the first of ``NAME_CANDIDATES`` random names that is free, looked up by the unique
        index of the generated names and ``ix_backtests_user_id_name``, so a new backtest never
        reads every name of the account
        (a sweep names 20); only when all of them are taken, from every name (``names``)."""
        tried = candidates(NAME_CANDIDATES)
        lowered = [name.lower() for name in tried]
        rows = await self._db.execute(
            select(func.lower(Backtest.generated_name), func.lower(Backtest.name)).where(
                Backtest.user_id == user_id,
                func.lower(Backtest.generated_name).in_(lowered)
                | func.lower(Backtest.name).in_(lowered),
            )
        )
        taken = {name for row in rows for name in row if name is not None}
        free = [name for name, low in zip(tried, lowered, strict=True) if low not in taken]
        if free:
            return free[0]
        return generated_name(await self.names(user_id))

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
            backtest.generated_name = await self.free_name(backtest.user_id)
            try:
                async with self._db.begin_nested():
                    self._db.add(backtest)
            except IntegrityError as error:
                if attempt == NAME_ATTEMPTS or _NAME_INDEX not in str(error.orig):
                    raise
            else:
                return
            attempt += 1

    async def add_sweep(self, sweep: Sweep) -> None:
        """Adds the sweep, written at once so its backtests can name it."""
        self._db.add(sweep)
        await self._db.flush()

    async def sweep(self, user_id: str, sweep_id: str) -> Sweep | None:
        """The user's sweep with the ID; None also when it is another user's."""
        return await self._db.scalar(
            select(Sweep).where(Sweep.id == sweep_id, Sweep.user_id == user_id)
        )

    async def sweep_backtests(self, sweep: Sweep) -> list[Listed]:
        """The backtests of the sweep in the order they were made, the grid's, with their
        statistics (``stats``, null until one finished). Read again even when this session
        holds them: a start that just added them answers what the runner started since."""
        rows = await self._db.execute(
            _listed()
            .options(undefer(Backtest.stats))
            .where(Backtest.user_id == sweep.user_id, Backtest.sweep_id == sweep.id)
            .order_by(Backtest.number)
            .execution_options(populate_existing=True)
        )
        return [Listed(*row) for row in rows]

    async def sweep_total(self, user_id: str) -> int:
        """How many sweeps the user started."""
        total = await self._db.scalar(
            select(func.count()).select_from(Sweep).where(Sweep.user_id == user_id)
        )
        return int(total or 0)

    async def sweeps(self, user_id: str, *, limit: int, offset: int = 0) -> list[Sweep]:
        """The user's sweeps, newest first, from `offset` on, without their grids."""
        rows = await self._db.scalars(
            select(Sweep)
            .options(load_only(Sweep.id, Sweep.user_id, Sweep.metric, Sweep.created_at))
            .where(Sweep.user_id == user_id)
            .order_by(Sweep.created_at.desc(), Sweep.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(rows)

    async def backtests_of_sweeps(self, user_id: str, sweep_ids: Sequence[str]) -> list[Listed]:
        """The backtests of the user's sweeps in one query, in the order they were made, with
        their statistics (``stats``, null until one finished)."""
        if not sweep_ids:
            return []
        rows = await self._db.execute(
            _listed()
            .options(undefer(Backtest.stats))
            .where(Backtest.user_id == user_id, Backtest.sweep_id.in_(sweep_ids))
            .order_by(Backtest.number)
        )
        return [Listed(*row) for row in rows]

    async def running(self, user_id: str) -> bool:
        """Whether a backtest of the user is running (the partial index
        ``ix_backtests_running``)."""
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

    async def running_since(self, before: datetime) -> list[str]:
        """The IDs of the backtests of any user that began to run before ``before`` and
        still run (``ix_backtests_running``)."""
        began = func.coalesce(Backtest.started_at, Backtest.created_at)
        rows = await self._db.scalars(select(Backtest.id).where(_RUNNING, began < before))
        return list(rows)

    async def running_total(self) -> int:
        """How many backtests of any user run: the run slots taken (``ix_backtests_running``)."""
        total = await self._db.scalar(select(func.count()).select_from(Backtest).where(_RUNNING))
        return int(total or 0)

    async def existing(self, backtest_ids: Collection[str]) -> set[str]:
        """Those of the IDs whose backtest is still there."""
        if not backtest_ids:
            return set()
        rows = await self._db.scalars(select(Backtest.id).where(Backtest.id.in_(backtest_ids)))
        return set(rows)

    async def requeue(self, backtest_id: str) -> None:
        """Puts the running backtest that never began back in its queue, where its number
        keeps it first."""
        await self._db.execute(
            update(Backtest)
            .where(Backtest.id == backtest_id, _RUNNING)
            .values(status=BacktestStatus.queued.value, started_at=None)
        )

    async def waiting(self) -> list[str]:
        """The users with a queued backtest and none running, whose queue may take a run slot:
        the one whose oldest queued backtest waits longest first."""
        running = select(literal(1)).where(
            _AHEAD.user_id == Backtest.user_id, _AHEAD.status == BacktestStatus.running.value
        )
        rows = await self._db.scalars(
            select(Backtest.user_id)
            .where(_QUEUED, ~exists(running))
            .group_by(Backtest.user_id)
            .order_by(func.min(Backtest.created_at), Backtest.user_id)
        )
        return list(rows)

    async def statuses(self, user_id: str, backtest_ids: Sequence[str]) -> list[StatusRow]:
        """Where each of the backtests stands, the user's own or another account's published
        ones, from their rows alone: no setup, report, statistics or curves are read (the
        report's timings only for a backtest stored before its steps were). What a wait polls;
        in no particular order, and without the IDs nobody can read."""
        mine = Backtest.user_id == user_id
        rows = await self._db.execute(
            select(
                Backtest.id,
                Backtest.number,
                SHOWN_NAME,
                Backtest.generated_name,
                Backtest.user_id,
                User.display_name,
                Backtest.published_at,
                Backtest.sweep_id,
                Backtest.status,
                QUEUE_POSITION,
                Backtest.created_at,
                Backtest.started_at,
                Backtest.finished_at,
                Backtest.duration_s,
                Backtest.error,
                Backtest.steps,
                case((Backtest.steps.is_(None), Backtest.report["timings"]), else_=None),
                Backtest.spec.is_not(None),
            )
            .join(User, User.id == Backtest.user_id)
            .where(Backtest.id.in_(backtest_ids), mine | PUBLISHED)
        )
        return [StatusRow(*row) for row in rows]

    async def run_seconds(self, user_id: str) -> float | None:
        """The median of how long the user's last ``RECENT_RUNS`` finished backtests ran, in
        seconds: what an estimate of a wait counts with; None before any finished."""
        recent = (
            select(Backtest.duration_s.label("seconds"))
            .where(Backtest.user_id == user_id, _FINISHED, Backtest.duration_s.is_not(None))
            .order_by(Backtest.number.desc())
            .limit(RECENT_RUNS)
            .subquery()
        )
        median = await self._db.scalar(
            select(func.percentile_cont(0.5).within_group(recent.c.seconds))
        )
        return None if median is None else float(median)

    async def sweep_statuses(
        self, user_id: str, sweep_id: str
    ) -> list[tuple[str, str, Steps | None]] | None:
        """The ID and status of each backtest of the user's sweep, from their rows alone, and
        the steps of those running; None when the user has no such sweep."""
        found = await self._db.scalar(
            select(Sweep.id).where(Sweep.id == sweep_id, Sweep.user_id == user_id)
        )
        if found is None:
            return None
        running = Backtest.status == BacktestStatus.running.value
        rows = await self._db.execute(
            select(Backtest.id, Backtest.status, case((running, Backtest.steps)))
            .where(Backtest.user_id == user_id, Backtest.sweep_id == sweep_id)
            .order_by(Backtest.number)
        )
        return [(backtest_id, status, steps) for backtest_id, status, steps in rows]

    async def refresh_status(self, backtest: Backtest) -> None:
        """Reads the backtest's status and start again: the runner may have started it in a
        session of its own."""
        await self._db.refresh(backtest, ["status", "started_at"])

    async def cancel(self, backtest: Backtest, error: str, now: datetime) -> None:
        """Fails the queued backtest with ``error``: it never ran, so its steps stay pending
        and it took no time."""
        backtest.status = BacktestStatus.failed.value
        backtest.error = error
        backtest.finished_at = now

    # --- What deleting backtests needs -----------------------------------------------------------

    async def to_delete(
        self, user_id: str, *, ids: Sequence[str] = (), sweep_id: str | None = None
    ) -> list[Doomed]:
        """The user's backtests with one of the IDs, or of the sweep, locked until the
        transaction ends (a run that is finishing stores its results first), by number."""
        query = select(Backtest.id, Backtest.status, Backtest.sweep_id).where(
            Backtest.user_id == user_id
        )
        if sweep_id is not None:
            query = query.where(Backtest.sweep_id == sweep_id)
        else:
            query = query.where(Backtest.id.in_(ids))
        rows = await self._db.execute(query.order_by(Backtest.number).with_for_update())
        return [Doomed(*row) for row in rows]

    async def delete(self, backtest_ids: Sequence[str]) -> None:
        """Deletes the backtests with what their runs stored: one row each holds it all."""
        await self._db.execute(delete(Backtest).where(Backtest.id.in_(backtest_ids)))

    async def delete_empty_sweeps(self, user_id: str, sweep_ids: Sequence[str]) -> None:
        """Deletes the user's sweeps of the IDs that have no backtest left."""
        left = select(literal(1)).where(Backtest.sweep_id == Sweep.id)
        await self._db.execute(
            delete(Sweep).where(Sweep.user_id == user_id, Sweep.id.in_(sweep_ids), ~exists(left))
        )

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
        self, user_id: str, backtest_id: str, *, series: bool = False, trades: bool = False
    ) -> Authored | None:
        """The user's backtest, or another account's while it is published, with what its run
        stored, its predictions and curves aside (and its light series and trades unless
        ``series`` and ``trades``), and its author's display name; None for any other."""
        stored = [Backtest.spec, Backtest.report, Backtest.stats, Backtest.steps]
        if series:
            stored.append(Backtest.series)
        if trades:
            stored.append(Backtest.trades)
        row = (
            await self._db.execute(
                _authored()
                .options(*(undefer(column) for column in stored))
                .where(Backtest.id == backtest_id, (Backtest.user_id == user_id) | PUBLISHED)
            )
        ).first()
        return Authored(*row) if row is not None else None

    async def compared(
        self, user_id: str, backtest_ids: Sequence[str], *, curves: bool
    ) -> dict[str, Compared]:
        """What the comparison reads of each of the backtests that is the user's or another
        account's published one, by its ID, in one query; the others are left out. The curves
        only with ``curves``."""
        query = (
            select(
                Backtest,
                User.display_name,
                _COMPARED_SETUP,
                Backtest.report["rows"],
                Backtest.stats,
                Backtest.curves if curves else literal(None, Backtest.curves.type),
            )
            .join(User, User.id == Backtest.user_id)
            .where(Backtest.id.in_(backtest_ids), (Backtest.user_id == user_id) | PUBLISHED)
        )
        rows = (await self._db.execute(query)).all()
        return {row[0].id: Compared(*row) for row in rows}

    async def keep_results(
        self,
        backtest_id: str,
        *,
        series: bytes,
        trades: bytes,
        curves: bytes,
        report: dict[str, Any],
    ) -> None:
        """Stores the light results of a finished backtest that finished before they were
        stored (``report`` with the benchmarks' returns); it keeps curves it has."""
        await self._db.execute(
            update(Backtest)
            .where(Backtest.id == backtest_id, _FINISHED)
            .values(
                series=series,
                trades=trades,
                curves=func.coalesce(Backtest.curves, curves),
                report=report,
            )
        )

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
