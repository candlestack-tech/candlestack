"""The tools that list, manage and publish backtests end to end: the SDK's client over HTTP,
signed in with a real API key, on a real Postgres (conftest.py), the backtests made straight in
the table."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from candlestack.experiments.document import Decision, Execution, InstrumentOut, MetricsArgs
from candlestack.experiments.spec import RunSpec, SpecData, SpecModel, SpecWarmUp
from candlestack.experiments.steps import pending

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
BOB = "usr_bobbbbbbbb"
EPOCH = datetime(2026, 10, 1, 12, tzinfo=UTC)
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
# What a backtest made in the app stores of its setup; the seed command's test data has none.
SPEC = RunSpec(
    model=SpecModel(
        name="lstm.keras", framework="keras", sha256="0" * 64, size_bytes=1000, manifest={}
    ),
    data=SpecData(
        instrument=InstrumentOut(
            id="stock:AAPL",
            market="stock",
            symbol="AAPL",
            name="Apple Inc.",
            exchange="NASDAQ",
            source="alpaca",
            feed="sip",
            currency="USD",
        ),
        timeframe="1h",
        timeframe_seconds=3600,
        session="regular",
        period={"start": 1_672_617_600, "end": 1_767_225_600},
        load_start=1_672_000_000,
        warm_up=SpecWarmUp(candles=25, kind="exact"),
    ),
    decision=Decision(),
    execution=Execution(),
    benchmarks=[],
    metrics=MetricsArgs(periods_per_year=252, risk_free_rate=0.0),
    app_version="test",
).model_dump(mode="json")


def add_backtest(sql, user_id: str, number: int, **columns: Any) -> str:
    """A backtest of the user straight in the table, #N started N hours after ``EPOCH``,
    finished unless ``status`` says otherwise; its ID."""
    backtest_id = f"bt_{user_id[4:8]}{number:06d}"
    row: dict[str, Any] = {
        "status": "finished",
        "symbol": "AAPL",
        "name": None,
        "sharpe": 1.0,
        "starred": False,
        "spec": SPEC,
        "published": None,
        "started": None,
        "steps": None,
        **columns,
    }
    created = EPOCH + timedelta(hours=number)
    ended = row["status"] in {"finished", "failed"}
    sql(
        "INSERT INTO backtests (id, user_id, number, status, instruments, representation, "
        "model_name, model_framework, setup, total_return, buy_and_hold_return, sharpe, "
        "max_drawdown, win_rate, num_trades, equity, starred, spec, steps, created_at, "
        "started_at, finished_at, published_at, name, generated_name) VALUES (:id, :user_id, "
        ":number, :status, CAST(:instruments AS jsonb), 'renko', 'lstm.keras', 'keras', "
        "CAST(:setup AS jsonb), :total_return, 0.5, :sharpe, -0.1, 0.5, 100, "
        "CAST(:equity AS jsonb), :starred, CAST(:spec AS jsonb), CAST(:steps AS jsonb), "
        ":created, :started, :finished, :published, :name, :generated_name)",
        id=backtest_id,
        user_id=user_id,
        number=number,
        status=row["status"],
        instruments=json.dumps([{"market": "stock", "symbol": row["symbol"]}]),
        setup=json.dumps(SETUP),
        total_return=0.1 * number if ended else None,
        sharpe=row["sharpe"] if row["status"] == "finished" else None,
        equity=json.dumps({"strategy": [0.0, 0.1], "benchmark": [0.0, 0.2]}),
        starred=row["starred"],
        spec=None if row["spec"] is None else json.dumps(row["spec"]),
        steps=None if row["steps"] is None else json.dumps(row["steps"]),
        created=created,
        started=row["started"],
        finished=created + timedelta(seconds=20) if ended else None,
        published=row["published"],
        name=row["name"],
        generated_name=f"Backtest {number}",
    )
    return backtest_id


def tool(tool_name: str, **arguments: Any):
    async def call(session):
        return await session.call_tool(tool_name, arguments)

    return call


def problem(result) -> dict[str, Any]:
    assert result.is_error
    return json.loads(result.content[0].text)


def test_list_backtests_searches_sorts_and_pages_the_users_own(mcp, keys, ada, account, sql):
    account(BOB)
    for number in range(1, 13):
        add_backtest(sql, ada.id, number, symbol="AAPL" if number % 2 else "NVDA")
    add_backtest(sql, BOB, 1, symbol="AAPL")
    key = keys("read")

    first = mcp(tool("list_backtests"), key).structured_content
    second = mcp(tool("list_backtests", page=2), key).structured_content
    nvda = mcp(tool("list_backtests", query="nvda", sort="return", order="asc"), key)

    assert [item["number"] for item in first["backtests"]] == list(range(12, 2, -1))
    assert [item["number"] for item in second["backtests"]] == [2, 1]
    assert (first["total"], first["pages"]) == (12, 2)
    assert first["counts"] == {"all": 12, "starred": 0, "failed": 0, "public": 0}
    assert first["models"] == [{"name": "lstm.keras", "framework": "keras", "count": 12}]
    assert [item["number"] for item in nvda.structured_content["backtests"]] == [2, 4, 6, 8, 10, 12]
    assert "equity" not in first["backtests"][0]


def test_rename_star_and_their_problems(mcp, keys, ada, account, sql) -> None:
    account(BOB)
    first = add_backtest(sql, ada.id, 1)
    add_backtest(sql, ada.id, 2, name="Taken")
    of_bob = add_backtest(sql, BOB, 1)
    key = keys("backtests")

    renamed = mcp(tool("rename_backtest", backtest_id=first, name="  AAPL renko "), key)
    starred = mcp(tool("star_backtest", backtest_id=first), key)
    taken = mcp(tool("rename_backtest", backtest_id=first, name="taken"), key)
    others = mcp(tool("star_backtest", backtest_id=of_bob), key)
    back = mcp(tool("rename_backtest", backtest_id=first, name=None), key)

    assert renamed.structured_content["name"] == "AAPL renko"
    assert starred.structured_content["starred"] is True
    assert problem(taken)["type"] == PROBLEMS + "name-taken"
    assert "Taken (#2)" in problem(taken)["detail"]
    assert problem(others)["type"] == PROBLEMS + "not-found"
    assert back.structured_content["name"] == "Backtest 1"
    assert sql("SELECT name, starred FROM backtests WHERE id = :id", id=first) == [(None, True)]
    assert sql("SELECT starred FROM backtests WHERE id = :id", id=of_bob) == [(False,)]


def test_cancel_backtest_cancels_a_queued_one_only(mcp, keys, ada, sql) -> None:
    # One that runs keeps the queued one waiting (the runner starts the first of a queue).
    running = add_backtest(sql, ada.id, 1, status="running", started=datetime.now(UTC))
    queued = add_backtest(sql, ada.id, 2, status="queued", steps=pending())
    key = keys("backtests")

    cancelled = mcp(tool("cancel_backtest", backtest_id=queued), key)
    again = mcp(tool("cancel_backtest", backtest_id=queued), key)
    of_running = mcp(tool("cancel_backtest", backtest_id=running), key)

    assert not cancelled.is_error, cancelled.content
    assert cancelled.structured_content == {
        "id": queued,
        "number": 2,
        "name": "Backtest 2",
        "status": "failed",
        "error": "You cancelled the backtest before it ran.",
    }
    assert sql("SELECT status FROM backtests WHERE id = :id", id=queued) == [("failed",)]
    assert "has ended" in problem(again)["detail"]
    assert problem(of_running)["type"] == PROBLEMS + "backtest-not-queued"
    assert "running already" in problem(of_running)["detail"]


def test_publish_and_unpublish(mcp, keys, ada, account, sql) -> None:
    bob = account(BOB)
    finished = add_backtest(sql, ada.id, 1)
    failed = add_backtest(sql, ada.id, 2, status="failed")
    seeded = add_backtest(sql, ada.id, 3, spec=None)
    key = keys("publish")

    published = mcp(tool("publish_backtest", backtest_id=finished), key)
    not_finished = mcp(tool("publish_backtest", backtest_id=failed), key)
    no_document = mcp(tool("publish_backtest", backtest_id=seeded), key)
    seen_by_bob = bob.request("GET", "/api/v1/experiments").json()["total"]
    unpublished = mcp(tool("unpublish_backtest", backtest_id=finished), key)

    assert published.structured_content["public"] is True
    assert seen_by_bob == 1
    assert problem(not_finished)["type"] == PROBLEMS + "backtest-not-finished"
    assert problem(no_document)["type"] == PROBLEMS + "no-document"
    assert unpublished.structured_content["public"] is False
    assert bob.request("GET", "/api/v1/experiments").json()["total"] == 0


def test_publishing_needs_the_scope_publish(mcp, keys, ada, sql) -> None:
    finished = add_backtest(sql, ada.id, 1)

    result = mcp(tool("publish_backtest", backtest_id=finished), keys("backtests", "read"))

    assert problem(result)["scope"] == "publish"
    assert sql("SELECT published_at FROM backtests") == [(None,)]


def test_list_experiments_filters_everyones_published(mcp, keys, ada, account, sql) -> None:
    account(BOB)
    add_backtest(sql, ada.id, 1, published=EPOCH)
    add_backtest(sql, BOB, 1, symbol="NVDA", published=EPOCH + timedelta(hours=1))
    add_backtest(sql, BOB, 2)
    key = keys("read")

    everyone = mcp(tool("list_experiments"), key).structured_content
    nvda = mcp(tool("list_experiments", symbol="nvda"), key).structured_content
    mine = mcp(tool("list_experiments", mine=True), key).structured_content

    assert [(item["author"]["id"], item["number"]) for item in everyone["experiments"]] == [
        (BOB, 1),
        (ada.id, 1),
    ]
    assert everyone["counts"] == {"all": 2, "mine": 1, "people": 2}
    assert everyone["facets"]["instruments"] == [
        {"market": "stock", "symbol": "AAPL", "count": 1},
        {"market": "stock", "symbol": "NVDA", "count": 1},
    ]
    assert [item["author"]["mine"] for item in mine["experiments"]] == [True]
    assert [item["instruments"][0]["symbol"] for item in nvda["experiments"]] == ["NVDA"]


def test_get_dashboard(mcp, keys, ada, sql) -> None:
    for number in range(1, 9):
        add_backtest(sql, ada.id, number, sharpe=float(number), starred=number in {2, 5})
    add_backtest(sql, ada.id, 9, status="running", started=datetime.now(UTC))

    dashboard = mcp(tool("get_dashboard"), keys("read")).structured_content

    assert dashboard["counts"] == {"all": 9, "starred": 2, "failed": 0, "public": 0}
    assert [item["number"] for item in dashboard["newest"]] == [8, 7, 6, 5, 4, 3]
    assert [item["number"] for item in dashboard["best"]["sharpe"]] == [8, 7, 6, 5, 4]
    assert [item["number"] for item in dashboard["starred"]] == [5, 2]
