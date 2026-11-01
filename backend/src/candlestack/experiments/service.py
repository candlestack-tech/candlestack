"""The backtests of a user: the list of the Backtests page, starring one, and the Dashboard."""

from dataclasses import dataclass
from math import ceil

from candlestack.experiments.errors import BacktestNotFound
from candlestack.experiments.models import Backtest
from candlestack.experiments.schemas import (
    PAGE_SIZE,
    BacktestCounts,
    BacktestInstrument,
    BacktestOut,
    BacktestSetup,
    BacktestShow,
    BacktestSort,
    BacktestsPage,
    BacktestStatus,
    BacktestUpdate,
    BestBacktests,
    DashboardOut,
    EquitySeries,
    ModelCount,
    Representation,
    SortOrder,
    StarredBacktests,
    UsedModel,
)
from candlestack.experiments.store import BacktestStore, Listed, filters, ordering

# What the Dashboard shows: the newest backtests, the best by each result, the best starred.
DASHBOARD_LAST = 6
DASHBOARD_BEST = 5
DASHBOARD_STARRED = 4


@dataclass(frozen=True)
class ListQuery:
    """The search, filters, sort and page of the Backtests page."""

    query: str = ""
    show: BacktestShow = BacktestShow.all
    model: str | None = None
    sort: BacktestSort = BacktestSort.default
    order: SortOrder = SortOrder.desc
    page: int = 1


def _equity(backtest: Backtest) -> EquitySeries | None:
    """The backtest's equity series; only a finished backtest shows one."""
    if backtest.equity is None or backtest.status != BacktestStatus.finished.value:
        return None
    return EquitySeries.model_validate(backtest.equity)


def backtest_out(listed: Listed) -> BacktestOut:
    backtest = listed.backtest
    return BacktestOut(
        id=backtest.id,
        name=backtest.name,
        number=backtest.number,
        status=BacktestStatus(backtest.status),
        seeded=backtest.seeded,
        instruments=[BacktestInstrument.model_validate(item) for item in backtest.instruments],
        representation=Representation(backtest.representation),
        model=UsedModel(
            id=backtest.model_id, name=listed.model_name, framework=backtest.model_framework
        ),
        setup=BacktestSetup.model_validate(backtest.setup),
        total_return=backtest.total_return,
        buy_and_hold_return=backtest.buy_and_hold_return,
        sharpe=backtest.sharpe,
        max_drawdown=backtest.max_drawdown,
        win_rate=backtest.win_rate,
        num_trades=backtest.num_trades,
        equity=_equity(backtest),
        duration_s=backtest.duration_s,
        error=backtest.error,
        starred=backtest.starred,
        created_at=backtest.created_at,
        finished_at=backtest.finished_at,
    )


def pages(total: int) -> int:
    """The pages `total` backtests fill; 1 when there are none."""
    return max(1, ceil(total / PAGE_SIZE))


class BacktestsService:
    def __init__(self, store: BacktestStore) -> None:
        self._store = store

    async def _counts(self, user_id: str) -> BacktestCounts:
        counts = await self._store.counts(user_id)
        return BacktestCounts(all=counts.all, starred=counts.starred, failed=counts.failed)

    async def backtests(self, user_id: str, query: ListQuery) -> BacktestsPage:
        """A page of the user's backtests that match the query, with what the filter panel
        shows: the counts of all, starred and failed ones and the models used."""
        conditions = filters(query=query.query, show=query.show, model=query.model)
        total = await self._store.count(user_id, conditions)
        rows = await self._store.backtests(
            user_id,
            conditions,
            ordering(query.sort, query.order),
            limit=PAGE_SIZE,
            offset=(query.page - 1) * PAGE_SIZE,
        )
        models = await self._store.model_counts(user_id)
        return BacktestsPage(
            backtests=[backtest_out(row) for row in rows],
            total=total,
            page=query.page,
            pages=pages(total),
            counts=await self._counts(user_id),
            models=[
                ModelCount(name=model.name, framework=model.framework, count=model.count)
                for model in models
            ],
        )

    async def update(self, user_id: str, backtest_id: str, changes: BacktestUpdate) -> BacktestOut:
        """Stars, unstars or renames the user's backtest: the fields of ``changes`` that were
        sent; a name of null takes the name away.

        Raises:
            BacktestNotFound: The user has no backtest with the ID.
        """
        listed = await self._store.backtest(user_id, backtest_id)
        if listed is None:
            raise BacktestNotFound
        if changes.starred is not None:
            listed.backtest.starred = changes.starred
        if "name" in changes.model_fields_set:
            listed.backtest.name = changes.name
        await self._store.commit()
        return backtest_out(listed)

    async def dashboard(self, user_id: str) -> DashboardOut:
        """What the Dashboard shows of the user's backtests."""
        counts = await self._counts(user_id)
        # The newest that ended: a running one has nothing to show there yet.
        last = await self._store.backtests(
            user_id,
            [Backtest.status != BacktestStatus.running.value],
            ordering(BacktestSort.default, SortOrder.desc),
            limit=DASHBOARD_LAST,
        )
        best = {
            name: [
                backtest_out(row)
                for row in await self._store.best(user_id, result, limit=DASHBOARD_BEST)
            ]
            for name, result in (
                ("sharpe", Backtest.sharpe),
                ("total_return", Backtest.total_return),
                ("max_drawdown", Backtest.max_drawdown),
                ("win_rate", Backtest.win_rate),
            )
        }
        starred = await self._store.backtests(
            user_id,
            filters(show=BacktestShow.starred),
            ordering(BacktestSort.sharpe, SortOrder.desc),
            limit=DASHBOARD_STARRED,
        )
        return DashboardOut(
            counts=counts,
            last=[backtest_out(row) for row in last],
            best=BestBacktests(**best),
            starred=StarredBacktests(
                backtests=[backtest_out(row) for row in starred], total=counts.starred
            ),
        )
