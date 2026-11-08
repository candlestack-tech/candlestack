"""The backtests of a user: the list of the Backtests page, starring, renaming and publishing
one, the backtests everyone published (Experiments), and the Dashboard."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from typing import NamedTuple

from sqlalchemy import ColumnElement

from candlestack.experiments.errors import (
    BacktestNotFinished,
    BacktestNotFound,
    NameTaken,
    NoDocument,
)
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
    BacktestFilters,
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
    """The search, filters, sort and page of the Backtests page, and the filters and page size
    only the API and the agents ask for (``more``, ``per_page``)."""

    query: str = ""
    show: BacktestShow = BacktestShow.all
    model: str | None = None
    sort: BacktestSort = BacktestSort.default
    order: SortOrder = SortOrder.desc
    page: int = 1
    more: BacktestFilters = field(default_factory=BacktestFilters)
    per_page: int = PAGE_SIZE


class FoundBacktests(NamedTuple):
    """A page of the list without what the filter panel shows (``BacktestFacets``)."""

    backtests: list[BacktestOut]
    total: int
    page: int
    pages: int


class BacktestFacets(NamedTuple):
    """What the filter panel shows, whatever the filters: two queries."""

    counts: BacktestCounts
    models: list[ModelCount]


class FoundExperiments(NamedTuple):
    """A page of Experiments without the counts and what the filters offer."""

    experiments: list[ExperimentOut]
    total: int
    page: int
    pages: int


class ExperimentFacetsFound(NamedTuple):
    """The counts of Experiments and what its filters offer: five queries."""

    counts: ExperimentCounts
    facets: ExperimentFacets


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
        name=backtest.shown_name,
        generated_name=backtest.generated_name,
        number=backtest.number,
        status=BacktestStatus(backtest.status),
        queue_position=listed.queue_position,
        sweep_id=backtest.sweep_id,
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
        name=backtest.shown_name,
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


def _experiment_scope(user_id: str, query: ExperimentsQuery) -> list[ColumnElement[bool]]:
    return experiment_scope(user_id, query=query.query, mine=query.mine, author=query.author)


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
        found = await self.find(user_id, query)
        facets = await self.facets(user_id)
        return BacktestsPage(**found._asdict(), **facets._asdict())

    async def find(self, user_id: str, query: ListQuery) -> FoundBacktests:
        """A page of the user's backtests that match the query: two queries."""
        conditions = filters(query=query.query, show=query.show, model=query.model, more=query.more)
        total = await self._store.count(user_id, conditions)
        rows = await self._store.backtests(
            user_id,
            conditions,
            ordering(query.sort, query.order),
            limit=query.per_page,
            offset=(query.page - 1) * query.per_page,
        )
        return FoundBacktests(
            backtests=[backtest_out(row) for row in rows],
            total=total,
            page=query.page,
            pages=pages(total, query.per_page),
        )

    async def facets(self, user_id: str) -> BacktestFacets:
        """The counts of all, starred, failed and published backtests and the models used."""
        models = await self._store.model_counts(user_id)
        return BacktestFacets(
            counts=await self._counts(user_id),
            models=[
                ModelCount(name=model.name, framework=model.framework, count=model.count)
                for model in models
            ],
        )

    async def update(self, user_id: str, backtest_id: str, changes: BacktestUpdate) -> BacktestOut:
        """Stars, unstars, renames, publishes or unpublishes the user's backtest: the fields of
        ``changes`` that were sent. A name of null, or the backtest's generated name in any
        case, goes back to the generated name. Publishing one that is published already keeps
        when it was published; publishing it again after it was taken back makes it new in
        Experiments. Nothing changes when a field cannot.

        Raises:
            BacktestNotFound: The user has no backtest with the ID.
            NameTaken: Another backtest of the user has the name, as its own or its generated
                name, in any case.
            NoDocument: Publishing test data, which has no result document.
            BacktestNotFinished: Publishing a backtest that is queued, running or failed.
        """
        renamed = "name" in changes.model_fields_set
        if renamed:
            # No other rename or new backtest takes the name between the check and the commit.
            await self._store.lock_user(user_id)
        listed = await self._store.backtest(user_id, backtest_id)
        if listed is None:
            raise BacktestNotFound
        backtest = listed.backtest
        name = backtest.name
        if renamed:
            name = await self._custom_name(user_id, backtest, changes.name)
        await self._publish(backtest, public=changes.public)
        if changes.starred is not None:
            backtest.starred = changes.starred
        backtest.name = name
        await self._store.commit()
        return backtest_out(listed)

    async def _publish(self, backtest: Backtest, *, public: bool | None) -> None:
        """Publishes the backtest, or takes it back, as `public` asks; null leaves it as it is.

        Raises:
            NoDocument: Publishing test data, which has no result document.
            BacktestNotFinished: Publishing a backtest that is queued, running or failed.
        """
        if public and backtest.published_at is None:
            if not await self._store.has_spec(backtest.id):
                raise NoDocument
            if backtest.status != BacktestStatus.finished.value:
                raise BacktestNotFinished(backtest.status)
            backtest.published_at = datetime.now(UTC)
        elif public is False:
            backtest.published_at = None

    async def _custom_name(self, user_id: str, backtest: Backtest, name: str | None) -> str | None:
        """What to store as the backtest's own name for a rename to `name`: null to go back to
        the generated name, for null and for the generated name in any case.

        Raises:
            NameTaken: Another backtest of the user has `name`, the oldest of them named.
        """
        if name is None:
            return None
        namesakes = await self._store.namesakes(user_id, name)
        for namesake in namesakes:
            if namesake.id != backtest.id:
                raise NameTaken(namesake.name, namesake.number)
        if any(namesake.generated for namesake in namesakes):
            return None
        return name

    async def experiments(self, user_id: str, query: ExperimentsQuery) -> ExperimentsPage:
        """A page of the published backtests that match the query, with the counts and what
        the filters offer (over the scope and the search, whatever the filters)."""
        found = await self.find_experiments(user_id, query)
        facets = await self.experiment_facets(user_id, query)
        return ExperimentsPage(**found._asdict(), **facets._asdict())

    async def find_experiments(self, user_id: str, query: ExperimentsQuery) -> FoundExperiments:
        """A page of the published backtests that match the query: two queries."""
        conditions = [
            *_experiment_scope(user_id, query),
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
        return FoundExperiments(
            experiments=[experiment_out(row, user_id) for row in rows],
            total=total,
            page=query.page,
            pages=pages(total, EXPERIMENTS_PAGE_SIZE),
        )

    async def experiment_facets(
        self, user_id: str, query: ExperimentsQuery
    ) -> ExperimentFacetsFound:
        """The counts of Experiments and what the filters offer over the query's scope and
        search, whatever its filters."""
        counts = await self._store.experiment_counts(user_id)
        return ExperimentFacetsFound(
            counts=ExperimentCounts(all=counts.all, mine=counts.mine, people=counts.people),
            facets=facets_out(await self._store.facets(_experiment_scope(user_id, query))),
        )

    async def dashboard(self, user_id: str) -> DashboardOut:
        """What the Dashboard shows of the user's backtests, and the newest published ones of
        anyone."""
        counts = await self._counts(user_id)
        # The newest that ended: a queued or running one has nothing to show there yet.
        last = await self._store.backtests(
            user_id,
            [Backtest.status.not_in([BacktestStatus.queued.value, BacktestStatus.running.value])],
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
