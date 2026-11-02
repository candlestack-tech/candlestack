"""How the search, filters, sorts and pages of the list and of Experiments turn into SQL,
without a database; the results are integration tests (tests/integration/experiments)."""

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from candlestack.accounts import User
from candlestack.experiments.models import Backtest, new_backtest_id
from candlestack.experiments.schemas import (
    EXPERIMENTS_PAGE_SIZE,
    BacktestShow,
    BacktestSort,
    ExperimentSort,
    InstrumentMarket,
    Representation,
    SetupTimeframe,
    SortOrder,
)
from candlestack.experiments.service import facets_out, pages
from candlestack.experiments.store import (
    Facets,
    _reads_models,
    escape_like,
    experiment_filters,
    experiment_ordering,
    experiment_scope,
    experiment_search,
    filters,
    ordering,
    search,
)


def _after_from(query) -> str:
    compiled = query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled).split("FROM backtests", 1)[1].strip()


def where_sql(*conditions) -> str:
    """The WHERE of a query over the backtests, as Postgres gets it."""
    return _after_from(select(Backtest.id).where(*conditions))


def order_sql(*clauses) -> str:
    """The ORDER BY of a query over the backtests, as Postgres gets it."""
    return _after_from(select(Backtest.id).order_by(*clauses))


@pytest.mark.parametrize(
    ("value", "escaped"),
    [("aapl", "aapl"), ("50%", "50\\%"), ("a_b", "a\\_b"), ("c:\\x", "c:\\\\x")],
)
def test_escape_like(value: str, escaped: str) -> None:
    assert escape_like(value) == escaped


@pytest.mark.parametrize("query", ["", "   "])
def test_a_blank_search_matches_everything(query: str) -> None:
    assert search(query) is None
    assert filters(query=query) == []


def test_search_looks_at_the_number_the_symbols_and_the_models_name() -> None:
    condition = search(" Aapl ")
    assert condition is not None

    sql = where_sql(condition)

    assert "('#' || CAST(backtests.number AS VARCHAR)) ILIKE '%%Aapl%%' ESCAPE" in sql
    assert "jsonb_array_elements(backtests.instruments)" in sql
    assert "coalesce(models.name, backtests.model_name) ILIKE '%%Aapl%%'" in sql


def test_the_filters_add_up() -> None:
    conditions = filters(query="12", show=BacktestShow.starred, model="Lstm.keras")

    sql = where_sql(*conditions)

    assert len(conditions) == 3
    assert "backtests.starred IS true" in sql
    assert "lower(coalesce(models.name, backtests.model_name)) = lower('Lstm.keras')" in sql


def test_show_failed() -> None:
    assert where_sql(*filters(show=BacktestShow.failed)) == "WHERE backtests.status = 'failed'"


def test_show_all_filters_nothing() -> None:
    assert filters(show=BacktestShow.all) == []


@pytest.mark.parametrize("order", list(SortOrder))
def test_the_default_sort_is_newest_first_in_any_order(order: SortOrder) -> None:
    assert order_sql(*ordering(BacktestSort.default, order)) == (
        "ORDER BY backtests.created_at DESC, backtests.number DESC"
    )


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        (BacktestSort.sharpe, SortOrder.desc, "backtests.sharpe DESC NULLS LAST"),
        (BacktestSort.sharpe, SortOrder.asc, "backtests.sharpe ASC NULLS LAST"),
        (BacktestSort.total_return, SortOrder.asc, "backtests.total_return ASC NULLS LAST"),
        (BacktestSort.max_drawdown, SortOrder.desc, "backtests.max_drawdown DESC NULLS LAST"),
        (BacktestSort.trades, SortOrder.desc, "backtests.num_trades DESC NULLS LAST"),
        (BacktestSort.number, SortOrder.asc, "backtests.number ASC NULLS LAST"),
        (BacktestSort.when, SortOrder.asc, "backtests.number ASC NULLS LAST"),
        (
            BacktestSort.model,
            SortOrder.asc,
            "lower(coalesce(models.name, backtests.model_name)) ASC NULLS LAST",
        ),
    ],
)
def test_a_sort_puts_nulls_last_and_ties_newest_first(
    sort: BacktestSort, order: SortOrder, expected: str
):
    assert order_sql(*ordering(sort, order)) == f"ORDER BY {expected}, backtests.number DESC"


def test_only_the_search_and_the_model_filter_read_the_models() -> None:
    assert _reads_models(filters(query="aapl")) is True
    assert _reads_models(filters(model="lstm.keras")) is True
    assert _reads_models(filters(show=BacktestShow.starred)) is False
    assert _reads_models(filters(show=BacktestShow.failed)) is False
    assert _reads_models([]) is False


@pytest.mark.parametrize(("total", "expected"), [(0, 1), (1, 1), (10, 1), (11, 2), (23, 3)])
def test_pages(total: int, expected: int) -> None:
    assert pages(total) == expected


@pytest.mark.parametrize(("total", "expected"), [(0, 1), (12, 1), (13, 2)])
def test_pages_of_experiments(total: int, expected: int) -> None:
    assert pages(total, EXPERIMENTS_PAGE_SIZE) == expected


def test_show_public_is_the_published_finished_ones() -> None:
    assert where_sql(*filters(show=BacktestShow.public)) == (
        "WHERE backtests.published_at IS NOT NULL AND backtests.status = 'finished'"
    )


# --- Experiments --------------------------------------------------------------------------------


def experiments_where_sql(*conditions) -> str:
    """The WHERE of a query over the published backtests and their authors."""
    query = select(Backtest.id).join(User, User.id == Backtest.user_id).where(*conditions)
    compiled = query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    return str(compiled).split("WHERE", 1)[1].strip()


def test_experiments_are_the_published_finished_backtests_of_everyone() -> None:
    assert experiments_where_sql(*experiment_scope("usr_adaaaaaaaa")) == (
        "backtests.published_at IS NOT NULL AND backtests.status = 'finished'"
    )


def test_the_scopes_of_experiments() -> None:
    mine = experiments_where_sql(*experiment_scope("usr_adaaaaaaaa", mine=True))
    authors = experiments_where_sql(*experiment_scope("usr_adaaaaaaaa", author="usr_bobbbbbbbb"))

    assert mine.endswith("AND backtests.user_id = 'usr_adaaaaaaaa'")
    assert authors.endswith("AND backtests.user_id = 'usr_bobbbbbbbb'")


@pytest.mark.parametrize("query", ["", "  "])
def test_a_blank_search_of_experiments_matches_everything(query: str) -> None:
    assert experiment_search(query) is None
    assert len(experiment_scope("usr_adaaaaaaaa", query=query)) == 1


def test_the_search_of_experiments_looks_at_symbols_the_model_and_the_author() -> None:
    sql = experiments_where_sql(*experiment_scope("usr_adaaaaaaaa", query=" Nv_ "))

    assert "jsonb_array_elements(backtests.instruments)" in sql
    assert "->> 'symbol') ILIKE '%%Nv\\_%%' ESCAPE" in sql
    # The name the model had when the backtest ran, never its current one.
    assert "backtests.model_name ILIKE '%%Nv\\_%%'" in sql
    assert "users.display_name ILIKE '%%Nv\\_%%'" in sql
    assert "email" not in sql
    assert "models" not in sql


def test_no_filter_of_experiments_filters_nothing() -> None:
    assert experiment_filters() == []


def test_the_filters_of_experiments_add_up() -> None:
    conditions = experiment_filters(
        market=InstrumentMarket.crypto,
        symbol=" btcusdt ",
        representation=Representation.kagi,
        timeframe=SetupTimeframe.h1,
    )

    sql = experiments_where_sql(*conditions)

    assert len(conditions) == 4
    assert "->> 'market') = 'crypto'" in sql
    assert "upper((instrument.value ->> 'symbol')) = 'BTCUSDT'" in sql
    assert "backtests.representation = 'kagi'" in sql
    assert "(backtests.setup ->> 'timeframe') = '1h'" in sql


@pytest.mark.parametrize("order", list(SortOrder))
def test_experiments_are_newest_published_first_in_any_order(order: SortOrder) -> None:
    assert order_sql(*experiment_ordering(ExperimentSort.newest, order)) == (
        "ORDER BY backtests.published_at DESC, backtests.id DESC"
    )


@pytest.mark.parametrize(
    ("sort", "order", "expected"),
    [
        (ExperimentSort.total_return, SortOrder.desc, "backtests.total_return DESC NULLS LAST"),
        (ExperimentSort.sharpe, SortOrder.asc, "backtests.sharpe ASC NULLS LAST"),
        (ExperimentSort.max_drawdown, SortOrder.desc, "backtests.max_drawdown DESC NULLS LAST"),
        (ExperimentSort.trades, SortOrder.asc, "backtests.num_trades ASC NULLS LAST"),
    ],
)
def test_a_sort_of_experiments_puts_nulls_last_and_ties_newest_published_first(
    sort: ExperimentSort, order: SortOrder, expected: str
) -> None:
    assert order_sql(*experiment_ordering(sort, order)) == (
        f"ORDER BY {expected}, backtests.published_at DESC, backtests.id DESC"
    )


def test_the_facets_keep_their_order_and_only_what_occurs() -> None:
    facets = facets_out(
        Facets(
            markets={"crypto": 1, "stock": 3},
            instruments=[("stock", "AAPL", 2), ("crypto", "BTCUSDT", 1)],
            representations={"windows": 1, "renko": 2},
            timeframes={"1d": 1, "5m": 2, "1h": 4},
        )
    )

    assert facets.model_dump(mode="json") == {
        "markets": [{"value": "stock", "count": 3}, {"value": "crypto", "count": 1}],
        "instruments": [
            {"market": "stock", "symbol": "AAPL", "count": 2},
            {"market": "crypto", "symbol": "BTCUSDT", "count": 1},
        ],
        "representations": [{"value": "renko", "count": 2}, {"value": "windows", "count": 1}],
        "timeframes": [
            {"value": "5m", "count": 2},
            {"value": "1h", "count": 4},
            {"value": "1d", "count": 1},
        ],
    }


def test_backtest_ids() -> None:
    ids = {new_backtest_id() for _ in range(100)}

    assert len(ids) == 100
    for backtest_id in ids:
        assert backtest_id.startswith("bt_")
        assert len(backtest_id) == 13
        assert set(backtest_id[3:]) <= set("abcdefghijklmnopqrstuvwxyz234567")
