"""The tools that list, manage and publish backtests, through the SDK's client over HTTP, with
the API keys and the backtests services faked: the arguments they pass to the services, the
compact answers, the scopes and the problems of the services' errors. On Postgres:
tests/integration/mcp/test_manage.py."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client

from candlestack.accounts import Caller, Scope, User
from candlestack.core import ProblemError
from candlestack.experiments.errors import (
    BacktestNotFinished,
    BacktestNotFound,
    BacktestNotQueued,
    NameTaken,
    NoDocument,
)
from candlestack.experiments.schemas import (
    BacktestCounts,
    BacktestOut,
    BacktestShow,
    BacktestSort,
    BacktestStatus,
    BacktestUpdate,
    BestBacktests,
    ExperimentCounts,
    ExperimentFacets,
    ExperimentOut,
    ExperimentSort,
    InstrumentMarket,
    ModelCount,
    Representation,
    SetupTimeframe,
    SortOrder,
)
from candlestack.experiments.service import (
    BacktestFacets,
    ExperimentFacetsFound,
    ExperimentsQuery,
    FoundBacktests,
    FoundExperiments,
    ListQuery,
    OwnDashboard,
)
from candlestack.experiments.store import BacktestFilters
from candlestack.mcp import http, manage

PROBLEMS = "https://candlestack.tech/problems/"
ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")
KEYS = {
    "cs_read" + "a" * 36: frozenset({Scope.read}),
    "cs_back" + "a" * 36: frozenset({Scope.backtests}),
    "cs_publ" + "a" * 36: frozenset({Scope.publish}),
}
READ, BACKTESTS, PUBLISH = KEYS

SETUP = {
    "timeframe": "1h",
    "period_start": "2023-01-02",
    "period_end": "2025-12-31",
    "direction": "long",
    "threshold": 0.05,
    "stop_loss_pct": 7,
    "fees_pct": 0.1,
    "slippage_pct": 0.02,
    "benchmark": "bh",
}
CREATED = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)


def backtest(number: int = 12, **changes: Any) -> BacktestOut:
    return BacktestOut.model_validate(
        {
            "id": f"bt_{number:010d}",
            "name": "Silent Orion",
            "generated_name": "Silent Orion",
            "number": number,
            "status": "finished",
            "queue_position": None,
            "sweep_id": None,
            "seeded": False,
            "instruments": [{"market": "stock", "symbol": "AAPL"}],
            "representation": "renko",
            "model": {"id": "mdl_k2m9qx47f3", "name": "blesk.keras", "framework": "keras"},
            "setup": SETUP,
            "total_return": 0.246,
            "buy_and_hold_return": 0.682,
            "sharpe": 0.79,
            "max_drawdown": -0.197,
            "win_rate": 0.512,
            "num_trades": 1712,
            "equity": {"strategy": [0.0, 0.246], "benchmark": [0.0, 0.682]},
            "duration_s": 162.0,
            "error": None,
            "starred": False,
            "public": False,
            "published_at": None,
            "created_at": CREATED,
            "finished_at": CREATED,
            **changes,
        }
    )


EXPERIMENT = ExperimentOut.model_validate(
    {
        "id": "bt_t9n8v7d6a5",
        "name": "Quiet Vega",
        "number": 3,
        "instruments": [{"market": "crypto", "symbol": "BTCUSDT"}],
        "representation": "kagi",
        "model": {"name": "kagi.onnx", "framework": "onnx", "input_shape": [25, 23]},
        "setup": SETUP,
        "total_return": 0.1,
        "benchmark_return": 0.2,
        "buy_and_hold_return": 0.2,
        "sharpe": 1.1,
        "max_drawdown": -0.05,
        "win_rate": 0.6,
        "num_trades": 40,
        "equity": {"strategy": [0.0, 0.1], "benchmark": [0.0, 0.2]},
        "author": {"id": "usr_bobbbbbbbb", "name": "Bob", "mine": False},
        "published_at": CREATED,
    }
)
COUNTS = BacktestCounts(all=15, starred=5, failed=2, public=3)
FACETS = ExperimentFacets(markets=[], instruments=[], representations=[], timeframes=[])
# What a cancelled backtest's document starts with (the rest does not matter to the tool).
CANCELLED = {
    "backtest": {
        "id": "bt_0000000014",
        "name": "Quiet Vega",
        "generated_name": "Quiet Vega",
        "number": 14,
        "status": "failed",
        "error": "You cancelled the backtest before it ran.",
        "steps": [],
    },
    "results": None,
}


class FakeService:
    """Stands in for ``BacktestsService``: records what it was asked, answers canned pages, and
    ``refuse`` makes ``update`` raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self.refuse: Exception | None = None

    async def find(self, user_id: str, query: ListQuery) -> FoundBacktests:
        self.calls.append(("backtests", user_id, query))
        return FoundBacktests(backtests=[backtest()], total=1, page=query.page, pages=1)

    async def facets(self, user_id: str) -> BacktestFacets:
        self.calls.append(("facets", user_id, None))
        return BacktestFacets(
            counts=COUNTS, models=[ModelCount(name="blesk.keras", framework="keras", count=1)]
        )

    async def update(self, user_id: str, backtest_id: str, changes: BacktestUpdate) -> BacktestOut:
        self.calls.append(("update", user_id, (backtest_id, changes)))
        if self.refuse is not None:
            raise self.refuse
        fields = changes.model_dump(include=changes.model_fields_set, exclude={"name"})
        return backtest(**fields, name=changes.name or "Silent Orion")

    async def find_experiments(self, user_id: str, query: ExperimentsQuery) -> FoundExperiments:
        self.calls.append(("experiments", user_id, query))
        return FoundExperiments(experiments=[EXPERIMENT], total=1, page=query.page, pages=1)

    async def experiment_facets(
        self, user_id: str, query: ExperimentsQuery
    ) -> ExperimentFacetsFound:
        self.calls.append(("experiment_facets", user_id, query))
        return ExperimentFacetsFound(
            counts=ExperimentCounts(all=1, mine=0, people=1), facets=FACETS
        )

    async def own_dashboard(self, user_id: str) -> OwnDashboard:
        self.calls.append(("own_dashboard", user_id, None))
        # The best by Sharpe ratio is also the best by return and starred: listed once.
        best = backtest(3, sharpe=1.4, starred=True)
        return OwnDashboard(
            counts=COUNTS,
            last=[backtest(1), backtest(2)],
            best=BestBacktests(
                sharpe=[best, backtest(4, sharpe=0.9)],
                total_return=[best],
                max_drawdown=[],
                win_rate=[backtest(5, win_rate=0.7)],
            ),
            starred=[best, backtest(6, starred=True, sharpe=None)],
        )


class FakeRuns:
    """Stands in for ``BacktestRuns``: ``cancel`` answers the cancelled document or raises
    ``refuse``."""

    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []
        self.refuse: Exception | None = None

    async def cancel(self, user_id: str, backtest_id: str) -> bytes:
        self.cancelled.append((user_id, backtest_id))
        if self.refuse is not None:
            raise self.refuse
        return json.dumps(CANCELLED).encode()


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """The keys of ``KEYS`` sign in as Ada with their scopes (replaces conftest.py's)."""

    async def key_caller(token: str, **_clients: Any) -> Caller:
        if token not in KEYS:
            raise ProblemError(401, "invalid-api-key", "Invalid API key", "Unknown key.")
        return Caller(ADA, KEYS[token])

    monkeypatch.setattr(http, "key_caller", key_caller)


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(manage, "get_backtests_service", lambda _db: fake)
    return fake


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> FakeRuns:
    fake = FakeRuns()

    def backtest_runs(_state: Any, _db: Any) -> FakeRuns:
        return fake

    monkeypatch.setattr(manage, "backtest_runs", backtest_runs)
    return fake


def call(mcp, name: str, arguments: dict[str, Any], key: str) -> Any:
    return mcp(lambda session: session.call_tool(name, arguments), key=key)


def problem(result: Any) -> dict[str, Any]:
    assert result.is_error
    return json.loads(result.content[0].text)


def test_the_tools_and_their_annotations(mcp) -> None:
    async def list_tools(session: Client) -> dict[str, Any]:
        return {tool.name: tool for tool in (await session.list_tools()).tools}

    tools = mcp(list_tools, key=READ)

    for name in ("list_backtests", "list_experiments", "get_dashboard"):
        assert tools[name].annotations.read_only_hint is True
    for name in ("rename_backtest", "star_backtest"):
        assert tools[name].annotations.read_only_hint is False
        assert tools[name].annotations.destructive_hint is False
    for name in ("publish_backtest", "unpublish_backtest", "cancel_backtest"):
        assert tools[name].annotations.destructive_hint is True
    assert "visible to every account of the beta" in tools["publish_backtest"].description
    assert tools["rename_backtest"].input_schema["required"] == ["backtest_id", "name"]


def test_list_backtests_passes_the_search_filters_sort_and_page(mcp, service) -> None:
    arguments = {"query": "aapl", "show": "starred", "model": "blesk.keras", "sort": "return"}

    more = {"order": "asc", "page": 2, "include_facets": True}

    result = call(mcp, "list_backtests", {**arguments, **more}, READ)

    assert not result.is_error, result.content
    assert service.calls == [
        (
            "backtests",
            ADA.id,
            ListQuery(
                query="aapl",
                show=BacktestShow.starred,
                model="blesk.keras",
                sort=BacktestSort.total_return,
                order=SortOrder.asc,
                page=2,
            ),
        ),
        ("facets", ADA.id, None),
    ]
    page = result.structured_content
    assert page["total"] == 1
    assert page["page"] == 2
    assert page["counts"] == {"all": 15, "starred": 5, "failed": 2, "public": 3}
    assert page["models"] == [{"name": "blesk.keras", "framework": "keras", "count": 1}]
    [listed] = page["backtests"]
    assert listed["id"] == "bt_0000000012"
    assert listed["model"] == "blesk.keras"
    assert listed["model_id"] == "mdl_k2m9qx47f3"
    assert listed["setup"]["timeframe"] == "1h"
    assert listed["url"] == "http://testserver/backtests/bt_0000000012"
    assert listed["sweep_id"] is None
    for left_out in ("equity", "generated_name", "seeded", "duration_s", "finished_at"):
        assert left_out not in listed


def test_list_backtests_defaults_to_the_first_page_newest_first(mcp, service) -> None:
    assert not call(mcp, "list_backtests", {}, READ).is_error

    assert service.calls == [("backtests", ADA.id, ListQuery()), ("facets", ADA.id, None)]


def test_list_backtests_passes_the_filters_of_the_api(mcp, service) -> None:
    arguments = {
        "status": ["finished", "failed"],
        "sweep_id": "sw_7f3k2m9qx4",
        "model_id": "mdl_k2m9qx47f3",
        "instrument": " Stock:aapl ",
        "timeframe": "1h",
        "representation": "kagi",
        "created_after": "2026-10-01T00:00:00",
        "created_before": "2026-11-01T00:00:00+01:00",
        "min_sharpe": 1,
        "min_total_return": 0.1,
        "min_max_drawdown": -0.2,
        "starred": False,
        "public": True,
        "sort": "sortino",
        "per_page": 100,
    }

    result = call(mcp, "list_backtests", arguments, READ)

    assert not result.is_error, result.content
    [(_, _, query), facets] = service.calls
    assert facets == ("facets", ADA.id, None)
    assert (query.sort, query.per_page) == (BacktestSort.sortino, 100)
    assert query.more == BacktestFilters(
        statuses=(BacktestStatus.finished, BacktestStatus.failed),
        sweep_id="sw_7f3k2m9qx4",
        model_id="mdl_k2m9qx47f3",
        instrument="stock:AAPL",
        timeframe=SetupTimeframe.h1,
        representation=Representation.kagi,
        created_after=datetime(2026, 10, 1, tzinfo=UTC),
        created_before=datetime(2026, 10, 31, 23, tzinfo=UTC),
        min_sharpe=1,
        min_total_return=0.1,
        min_max_drawdown=-0.2,
        starred=False,
        public=True,
    )


@pytest.mark.parametrize(
    ("arguments", "facets"),
    [
        ({"page": 2}, False),
        ({"include_facets": False}, False),
        ({"page": 3, "include_facets": True}, True),
    ],
)
def test_list_backtests_answers_the_counts_on_page_1_or_when_asked(
    mcp, service, arguments: dict[str, Any], facets: bool
) -> None:
    page = call(mcp, "list_backtests", arguments, READ).structured_content

    assert (page["counts"] is not None, page["models"] is not None) == (facets, facets)
    assert ("facets", ADA.id, None) in service.calls if facets else len(service.calls) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        {"page": 0},
        {"show": "deleted"},
        {"query": "x" * 101},
        {"sort": "luck"},
        {"per_page": 101},
        {"per_page": 0},
        {"instrument": "AAPL"},
        {"instrument": "bonds:AAPL"},
        {"min_max_drawdown": 0.1},
        {"min_sharpe": "nan"},
        {"min_total_return": "inf"},
        {"min_max_drawdown": "-inf"},
        {"status": ["lost"]},
    ],
)
def test_list_backtests_refuses_arguments_the_page_would(mcp, service, arguments) -> None:
    body = problem(call(mcp, "list_backtests", arguments, READ))

    assert body["type"] == PROBLEMS + "validation"
    assert service.calls == []


def test_rename_backtest(mcp, service) -> None:
    result = call(
        mcp,
        "rename_backtest",
        {"backtest_id": "bt_0000000012", "name": "  AAPL renko  "},
        BACKTESTS,
    )

    assert not result.is_error, result.content
    [(_, user_id, (backtest_id, changes))] = service.calls
    assert (user_id, backtest_id) == (ADA.id, "bt_0000000012")
    assert changes.model_fields_set == {"name"}
    assert changes.name == "AAPL renko"
    assert result.structured_content == {
        "id": "bt_0000000012",
        "number": 12,
        "name": "AAPL renko",
        "status": "finished",
        "starred": False,
        "public": False,
    }


def test_renaming_to_null_goes_back_to_the_generated_name(mcp, service) -> None:
    result = call(mcp, "rename_backtest", {"backtest_id": "bt_1", "name": None}, BACKTESTS)

    assert not result.is_error
    [(_, _, (_, changes))] = service.calls
    assert changes.model_fields_set == {"name"}
    assert changes.name is None


@pytest.mark.parametrize("name", ["   ", "x" * 81, "tab\there"])
def test_a_name_the_app_refuses_is_invalid(mcp, service, name: str) -> None:
    body = problem(call(mcp, "rename_backtest", {"backtest_id": "bt_1", "name": name}, BACKTESTS))

    assert body["type"] == PROBLEMS + "validation"
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "status", "slug"),
    [
        (BacktestNotFound(), 404, "not-found"),
        (NameTaken("Quiet Vega", 3), 409, "name-taken"),
    ],
)
def test_rename_backtest_answers_the_routes_problems(mcp, service, error, status, slug) -> None:
    service.refuse = error

    body = problem(call(mcp, "rename_backtest", {"backtest_id": "bt_1", "name": "x"}, BACKTESTS))

    assert (body["status"], body["type"]) == (status, PROBLEMS + slug)
    if slug == "name-taken":
        assert "Quiet Vega (#3)" in body["detail"]


@pytest.mark.parametrize(("arguments", "starred"), [({}, True), ({"starred": False}, False)])
def test_star_backtest(mcp, service, arguments, starred: bool) -> None:
    result = call(mcp, "star_backtest", {"backtest_id": "bt_1", **arguments}, BACKTESTS)

    assert result.structured_content["starred"] is starred
    [(_, _, (_, changes))] = service.calls
    assert changes.model_fields_set == {"starred"}


@pytest.mark.parametrize("tool", ["rename_backtest", "star_backtest", "cancel_backtest"])
@pytest.mark.parametrize("key", [READ, PUBLISH])
def test_changing_a_backtest_needs_the_scope_backtests(mcp, service, runs, tool, key) -> None:
    arguments = {"backtest_id": "bt_1"} | ({"name": "x"} if tool == "rename_backtest" else {})

    body = problem(call(mcp, tool, arguments, key))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["scope"] == "backtests"
    assert service.calls == []
    assert runs.cancelled == []


def test_cancel_backtest_answers_the_cancelled_backtest(mcp, runs) -> None:
    result = call(mcp, "cancel_backtest", {"backtest_id": "bt_0000000014"}, BACKTESTS)

    assert not result.is_error, result.content
    assert runs.cancelled == [(ADA.id, "bt_0000000014")]
    assert result.structured_content == {
        "id": "bt_0000000014",
        "number": 14,
        "name": "Quiet Vega",
        "status": "failed",
        "error": "You cancelled the backtest before it ran.",
    }


@pytest.mark.parametrize(
    ("error", "status", "slug", "detail"),
    [
        (BacktestNotFound(), 404, "not-found", "no backtest"),
        (BacktestNotQueued("running"), 409, "backtest-not-queued", "running already"),
        (BacktestNotQueued("finished"), 409, "backtest-not-queued", "has ended"),
    ],
)
def test_cancel_backtest_answers_the_routes_problems(mcp, runs, error, status, slug, detail):
    runs.refuse = error

    body = problem(call(mcp, "cancel_backtest", {"backtest_id": "bt_1"}, BACKTESTS))

    assert (body["status"], body["type"]) == (status, PROBLEMS + slug)
    assert detail in body["detail"]


def test_list_experiments_passes_the_search_scope_filters_sort_and_page(mcp, service) -> None:
    arguments = {
        "query": "btc",
        "mine": True,
        "author": "usr_bobbbbbbbb",
        "market": "crypto",
        "symbol": "BTCUSDT",
        "representation": "kagi",
        "timeframe": "1h",
        "sort": "sharpe",
        "order": "asc",
        "page": 3,
    }

    result = call(mcp, "list_experiments", {**arguments, "include_facets": True}, READ)

    assert not result.is_error, result.content
    query = ExperimentsQuery(
        query="btc",
        mine=True,
        author="usr_bobbbbbbbb",
        market=InstrumentMarket.crypto,
        symbol="BTCUSDT",
        representation=Representation.kagi,
        timeframe=SetupTimeframe.h1,
        sort=ExperimentSort.sharpe,
        order=SortOrder.asc,
        page=3,
    )
    assert service.calls == [
        ("experiments", ADA.id, query),
        ("experiment_facets", ADA.id, query),
    ]
    page = result.structured_content
    assert page["counts"] == {"all": 1, "mine": 0, "people": 1}
    assert page["facets"] == FACETS.model_dump()
    [listed] = page["experiments"]
    assert listed["author"] == {"id": "usr_bobbbbbbbb", "name": "Bob", "mine": False}
    assert listed["model"] == "kagi.onnx"
    assert listed["benchmark_return"] == 0.2
    assert "equity" not in listed


def test_list_experiments_gives_each_backtest_s_page(mcp, service) -> None:
    [listed] = call(mcp, "list_experiments", {}, READ).structured_content["experiments"]

    # The page of a published backtest, which every signed-in account can open.
    assert listed["url"] == f"http://testserver/backtests/{EXPERIMENT.id}"


def test_list_experiments_defaults_to_everyones_newest(mcp, service) -> None:
    assert not call(mcp, "list_experiments", {}, READ).is_error

    assert service.calls == [
        ("experiments", ADA.id, ExperimentsQuery()),
        ("experiment_facets", ADA.id, ExperimentsQuery()),
    ]


@pytest.mark.parametrize("arguments", [{"page": 2}, {"include_facets": False}])
def test_list_experiments_leaves_the_facets_out_past_page_1_or_when_asked(
    mcp, service, arguments: dict[str, Any]
) -> None:
    page = call(mcp, "list_experiments", arguments, READ).structured_content

    assert (page["counts"], page["facets"]) == (None, None)
    assert [name for name, _, _ in service.calls] == ["experiments"]


@pytest.mark.parametrize(
    ("tool", "public"), [("publish_backtest", True), ("unpublish_backtest", False)]
)
def test_publish_and_unpublish(mcp, service, tool: str, public: bool) -> None:
    result = call(mcp, tool, {"backtest_id": "bt_0000000012"}, PUBLISH)

    assert not result.is_error, result.content
    assert result.structured_content["public"] is public
    [(_, user_id, (backtest_id, changes))] = service.calls
    assert (user_id, backtest_id) == (ADA.id, "bt_0000000012")
    assert changes.model_fields_set == {"public"}
    assert changes.public is public


@pytest.mark.parametrize("tool", ["publish_backtest", "unpublish_backtest"])
@pytest.mark.parametrize("key", [READ, BACKTESTS])
def test_publishing_needs_the_scope_publish(mcp, service, tool: str, key: str) -> None:
    body = problem(call(mcp, tool, {"backtest_id": "bt_1"}, key))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["scope"] == "publish"
    assert service.calls == []


@pytest.mark.parametrize(
    ("error", "slug"),
    [(BacktestNotFinished("running"), "backtest-not-finished"), (NoDocument(), "no-document")],
)
def test_publish_backtest_answers_the_routes_problems(mcp, service, error, slug: str) -> None:
    service.refuse = error

    body = problem(call(mcp, "publish_backtest", {"backtest_id": "bt_1"}, PUBLISH))

    assert (body["status"], body["type"]) == (409, PROBLEMS + slug)


@pytest.mark.parametrize("tool", ["list_backtests", "list_experiments", "get_dashboard"])
def test_reading_needs_the_scope_read(mcp, service, tool: str) -> None:
    body = problem(call(mcp, tool, {}, BACKTESTS))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["scope"] == "read"
    assert service.calls == []


def test_get_dashboard_lists_each_backtest_once(mcp, service) -> None:
    result = call(mcp, "get_dashboard", {}, READ)

    assert not result.is_error, result.content
    # The user's own backtests only: not the queries of the published ones of anyone.
    assert service.calls == [("own_dashboard", ADA.id, None)]
    dashboard = result.structured_content
    assert set(dashboard) == {"counts", "backtests", "newest", "best", "starred"}
    assert dashboard["counts"]["all"] == 15
    assert dashboard["newest"] == ["bt_0000000001", "bt_0000000002"]
    assert dashboard["best"] == {
        "sharpe": [{"id": "bt_0000000003", "value": 1.4}, {"id": "bt_0000000004", "value": 0.9}],
        "total_return": [{"id": "bt_0000000003", "value": 0.246}],
        "max_drawdown": [],
        "win_rate": [{"id": "bt_0000000005", "value": 0.7}],
    }
    assert dashboard["starred"] == ["bt_0000000003", "bt_0000000006"]
    backtests = dashboard["backtests"]
    assert sorted(backtests) == [f"bt_{number:010d}" for number in range(1, 7)]
    best = backtests["bt_0000000003"]
    assert (best["id"], best["starred"], best["sharpe"]) == ("bt_0000000003", True, 1.4)
    assert "equity" not in best
    # Every backtest it names has its page, to show the user.
    assert [item["url"] for item in backtests.values()] == [
        f"http://testserver/backtests/{backtest_id}" for backtest_id in backtests
    ]
