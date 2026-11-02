"""The backtests of a user: the list of the Backtests page, starring, renaming and publishing
one, the backtests everyone published (Experiments), and the Dashboard."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil

from candlestack.experiments.errors import BacktestNotFinished, BacktestNotFound, NoDocument
from candlestack.experiments.models import Backtest
from candlestack.experiments.schemas import (
    DASHBOARD_EXPERIMENTS,
    EXPERIMENTS_PAGE_SIZE,
    PAGE_SIZE,
    Author,
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
    ExperimentCounts,
    ExperimentFacets,
    ExperimentModel,
    ExperimentOut,
    ExperimentSort,
    ExperimentsPage,
    FacetCount,
    InstrumentCount,
    InstrumentMarket,
    ModelCount,
    NewExperiments,
    Representation,
    SetupTimeframe,
    SortOrder,
    StarredBacktests,
    UsedModel,
)
from candlestack.experiments.store import (
    Authored,
    BacktestStore,
    Facets,
    Listed,
    experiment_filters,
    experiment_ordering,
    experiment_scope,
    filters,
    ordering,
)

# What the Dashboard shows: the newest backtests, the best by each result, the best starred.
DASHBOARD_LAST = 6
DASHBOARD_BEST = 5
DASHBOARD_STARRED = 4
# The markets of the filter in the order Experiments shows them.
_MARKETS = (InstrumentMarket.stock, InstrumentMarket.crypto)


@dataclass(frozen=True)
class ListQuery:
    """The search, filters, sort and page of the Backtests page."""

    query: str = ""
    show: BacktestShow = BacktestShow.all
    model: str | None = None
    sort: BacktestSort = BacktestSort.default
    order: SortOrder = SortOrder.desc
    page: int = 1


@dataclass(frozen=True)
class ExperimentsQuery:
    """The search, scope, filters, sort and page of Experiments."""

    query: str = ""
    mine: bool = False
    author: str | None = None
    market: InstrumentMarket | None = None
    symbol: str | None = None
    representation: Representation | None = None
    timeframe: SetupTimeframe | None = None
    sort: ExperimentSort = ExperimentSort.newest
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
        public=backtest.published_at is not None,
        published_at=backtest.published_at,
        created_at=backtest.created_at,
        finished_at=backtest.finished_at,
    )


def experiment_out(authored: Authored, viewer: str) -> ExperimentOut:
    """A published backtest as others see it: no model ID, the author's display name."""
    backtest = authored.backtest
    # Only published backtests are read for Experiments.
    assert backtest.published_at is not None
    return ExperimentOut(
        id=backtest.id,
        name=backtest.name,
        number=backtest.number,
        instruments=[BacktestInstrument.model_validate(item) for item in backtest.instruments],
        representation=Representation(backtest.representation),
        model=ExperimentModel(
            name=backtest.model_name,
            framework=backtest.model_framework,
            input_shape=backtest.input_shape,
        ),
        setup=BacktestSetup.model_validate(backtest.setup),
        total_return=backtest.total_return,
        benchmark_return=backtest.benchmark_return,
        buy_and_hold_return=backtest.buy_and_hold_return,
        sharpe=backtest.sharpe,
        max_drawdown=backtest.max_drawdown,
        win_rate=backtest.win_rate,
        num_trades=backtest.num_trades,
        equity=_equity(backtest),
        author=Author(id=backtest.user_id, name=authored.author, mine=backtest.user_id == viewer),
        published_at=backtest.published_at,
    )


def facets_out(facets: Facets) -> ExperimentFacets:
    """The choices of each filter that occur, the markets, representations and timeframes in
    their fixed order, the instruments the most published first."""
    return ExperimentFacets(
        markets=_in_order(facets.markets, _MARKETS),
        instruments=[
            InstrumentCount(market=InstrumentMarket(market), symbol=symbol, count=count)
            for market, symbol, count in facets.instruments
        ],
        representations=_in_order(facets.representations, list(Representation)),
        timeframes=_in_order(facets.timeframes, list(SetupTimeframe)),
    )


def _in_order(counts: Mapping[str, int], order: Sequence[StrEnum]) -> list[FacetCount]:
    return [
        FacetCount(value=value.value, count=counts[value.value])
        for value in order
        if value.value in counts
    ]


def pages(total: int, size: int = PAGE_SIZE) -> int:
    """The pages `total` backtests fill, `size` a page; 1 when there are none."""
    return max(1, ceil(total / size))


class BacktestsService:
    def __init__(self, store: BacktestStore) -> None:
        self._store = store

    async def _counts(self, user_id: str) -> BacktestCounts:
        counts = await self._store.counts(user_id)
        return BacktestCounts(
            all=counts.all, starred=counts.starred, failed=counts.failed, public=counts.public
        )

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
        """Stars, unstars, renames, publishes or unpublishes the user's backtest: the fields of
        ``changes`` that were sent; a name of null takes the name away. Publishing one that is
        published already keeps when it was published; publishing it again after it was taken
        back makes it new in Experiments. Nothing changes when a field cannot.

        Raises:
            BacktestNotFound: The user has no backtest with the ID.
            NoDocument: Publishing test data, which has no result document.
            BacktestNotFinished: Publishing a backtest that is running or failed.
        """
        listed = await self._store.backtest(user_id, backtest_id)
        if listed is None:
            raise BacktestNotFound
        backtest = listed.backtest
        if changes.public and backtest.published_at is None:
            if not await self._store.has_spec(backtest.id):
                raise NoDocument
            if backtest.status != BacktestStatus.finished.value:
                raise BacktestNotFinished(backtest.status)
            backtest.published_at = datetime.now(UTC)
        elif changes.public is False:
            backtest.published_at = None
        if changes.starred is not None:
            backtest.starred = changes.starred
        if "name" in changes.model_fields_set:
            backtest.name = changes.name
        await self._store.commit()
        return backtest_out(listed)

    async def experiments(self, user_id: str, query: ExperimentsQuery) -> ExperimentsPage:
        """A page of the published backtests that match the query, with the counts and what
        the filters offer (over the scope and the search, whatever the filters)."""
        scope = experiment_scope(user_id, query=query.query, mine=query.mine, author=query.author)
        conditions = [
            *scope,
            *experiment_filters(
                market=query.market,
                symbol=query.symbol,
                representation=query.representation,
                timeframe=query.timeframe,
            ),
        ]
        total = await self._store.experiment_total(conditions)
        rows = await self._store.experiments(
            conditions,
            experiment_ordering(query.sort, query.order),
            limit=EXPERIMENTS_PAGE_SIZE,
            offset=(query.page - 1) * EXPERIMENTS_PAGE_SIZE,
        )
        counts = await self._store.experiment_counts(user_id)
        return ExperimentsPage(
            experiments=[experiment_out(row, user_id) for row in rows],
            total=total,
            page=query.page,
            pages=pages(total, EXPERIMENTS_PAGE_SIZE),
            counts=ExperimentCounts(all=counts.all, mine=counts.mine, people=counts.people),
            facets=facets_out(await self._store.facets(scope)),
        )

    async def dashboard(self, user_id: str) -> DashboardOut:
        """What the Dashboard shows of the user's backtests, and the newest published ones of
        anyone."""
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
        published = await self._store.experiments(
            experiment_scope(user_id),
            experiment_ordering(ExperimentSort.newest, SortOrder.desc),
            limit=DASHBOARD_EXPERIMENTS,
        )
        everyone = await self._store.experiment_counts(user_id)
        return DashboardOut(
            counts=counts,
            last=[backtest_out(row) for row in last],
            best=BestBacktests(**best),
            starred=StarredBacktests(
                backtests=[backtest_out(row) for row in starred], total=counts.starred
            ),
            experiments=NewExperiments(
                backtests=[experiment_out(row, user_id) for row in published], total=everyone.all
            ),
        )
