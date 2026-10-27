"""The backtests table in Postgres: the queries the service and the seed command need, in the
given session. Nothing here commits; the caller does, once a change is complete.

A backtest shows the current name of its model while the model exists, else the name it had
when the backtest ran: ``MODEL_NAME``, over a left join of ``models``.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple

from sqlalchemy import (
    ColumnElement,
    DateTime,
    Select,
    String,
    UnaryExpression,
    cast,
    column,
    delete,
    exists,
    func,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB, aggregate_order_by, array_agg
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, undefer
from sqlalchemy.sql import visitors

from candlestack.accounts import User
from candlestack.experiments.models import Backtest
from candlestack.experiments.schemas import BacktestShow, BacktestSort, BacktestStatus, SortOrder
from candlestack.experiments.steps import Steps, failed
from candlestack.ml import Model, ModelUsageStats

# A column of the table, or an expression over the columns.
type SortKey = InstrumentedAttribute[Any] | ColumnElement[Any]

MODEL_NAME = func.coalesce(Model.name, Backtest.model_name)

_FINISHED = Backtest.status == BacktestStatus.finished.value
_FAILED = Backtest.status == BacktestStatus.failed.value
_RUNNING = Backtest.status == BacktestStatus.running.value

# The value each sort of the list orders by; `when` is the order they were made in, as `number`.
_SORT_KEYS: dict[BacktestSort, SortKey] = {
    BacktestSort.number: Backtest.number,
    BacktestSort.when: Backtest.number,
    BacktestSort.model: func.lower(MODEL_NAME),
    BacktestSort.total_return: Backtest.total_return,
    BacktestSort.sharpe: Backtest.sharpe,
    BacktestSort.max_drawdown: Backtest.max_drawdown,
    BacktestSort.trades: Backtest.num_trades,
}


class Listed(NamedTuple):
    """A backtest and the name its model shows (``MODEL_NAME``)."""

    backtest: Backtest
    model_name: str


class Counts(NamedTuple):
    all: int
    starred: int
    failed: int


class ModelCount(NamedTuple):
    name: str
    framework: str
    count: int


class UserModel(NamedTuple):
    id: str
    name: str
    framework: str


def escape_like(value: str) -> str:
    r"""`value` matched literally by LIKE with the escape character ``\``."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(query: str) -> ColumnElement[bool] | None:
    """Backtests whose ``#number``, an instrument's symbol or the model's name contains `query`,
    in any case; None for a blank query, which every backtest matches."""
    query = query.strip()
    if not query:
        return None
    pattern = f"%{escape_like(query)}%"
    instrument = (
        func.jsonb_array_elements(Backtest.instruments)
        .table_valued(column("value", JSONB))
        .alias("instrument")
    )
    symbol = exists(
        select(literal(1))
        .select_from(instrument)
        .where(instrument.c.value["symbol"].astext.ilike(pattern, escape="\\"))
    )
    number = (literal("#") + cast(Backtest.number, String)).ilike(pattern, escape="\\")
    return number | symbol | MODEL_NAME.ilike(pattern, escape="\\")


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
    if model is not None:
        conditions.append(func.lower(MODEL_NAME) == func.lower(model))
    return conditions


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


def _listed() -> Select[Backtest, str]:
    return select(Backtest, MODEL_NAME).outerjoin(Model, Model.id == Backtest.model_id)


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
        """The user's backtests in all, starred and failed."""
        row = (
            await self._db.execute(
                select(
                    func.count(),
                    func.count().filter(Backtest.starred.is_(True)),
                    func.count().filter(_FAILED),
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
        return [Listed(backtest, name) for backtest, name in rows]

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

    # --- What starting and running a backtest needs --------------------------------------------

    def add(self, backtest: Backtest) -> None:
        self._db.add(backtest)

    async def running(self, user_id: str) -> bool:
        """Whether a backtest of the user is running."""
        found = await self._db.scalar(
            select(Backtest.id).where(Backtest.user_id == user_id, _RUNNING).limit(1)
        )
        return found is not None

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

    async def with_run(self, user_id: str, backtest_id: str) -> Backtest | None:
        """The user's backtest with what its run stored, its predictions aside."""
        return await self._db.scalar(
            select(Backtest)
            .options(
                undefer(Backtest.spec),
                undefer(Backtest.report),
                undefer(Backtest.stats),
                undefer(Backtest.steps),
            )
            .where(Backtest.id == backtest_id, Backtest.user_id == user_id)
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

    async def fail(self, error: str, now: datetime, backtest_id: str | None = None) -> int:
        """Marks the running backtest with the ID (every running one without an ID) failed
        with ``error``, and the step it was at; how many. One that has ended keeps what it
        has."""
        query = select(Backtest.id, Backtest.steps).where(_RUNNING).with_for_update()
        if backtest_id is not None:
            query = query.where(Backtest.id == backtest_id)
        running = (await self._db.execute(query)).all()
        for found, steps in running:
            values: dict[str, Any] = {
                "status": BacktestStatus.failed.value,
                "error": error,
                "finished_at": now,
                "duration_s": func.extract(
                    "epoch", literal(now, DateTime(timezone=True)) - Backtest.created_at
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
