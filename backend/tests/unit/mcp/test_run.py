"""The tools that run and read backtests through the SDK's client over HTTP, with the API keys
(conftest.py) and the backtests service faked (``FakeRuns``): what each answers, the problems
of the service as tool errors and the scopes. Postgres, the queue and the worker are
integration tests (tests/integration/experiments/test_mcp_run.py)."""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from mcp import Client

from candlestack.data import InstrumentId, InstrumentNotFound
from candlestack.experiments import BacktestCheck, BacktestHeader
from candlestack.experiments.errors import BacktestNotFound, NoDocument, QueueFull
from candlestack.mcp import run

PROBLEMS = "https://candlestack.tech/problems/"
ADA = "usr_adaaaaaaaa"
CREATED = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
PERIOD = {"start": 1_717_372_800, "end": 1_717_459_200}
SETUP: dict[str, Any] = {
    "model": {"id": "mdl_k2m9qx47f3"},
    "data": {"instrument": {"id": "stock:AAPL"}, "period": PERIOD},
}
STEPS = ["candles", "queue", "model", "trades"]


def header(
    backtest_id: str = "bt_7f3k2m9qx4", status: str = "running", **fields: Any
) -> dict[str, Any]:
    return {
        "id": backtest_id,
        "name": "Silent Orion",
        "generated_name": "Silent Orion",
        "number": 12,
        "author": {"id": ADA, "name": "Ada", "mine": True},
        "public": False,
        "published_at": None,
        "status": status,
        "queue_position": 2 if status == "queued" else None,
        "created_at": CREATED.isoformat(),
        "started_at": None if status == "queued" else CREATED.isoformat(),
        "finished_at": None,
        "duration_s": None,
        "error": "It failed." if status == "failed" else None,
        "steps": [{"id": step, "state": "pending"} for step in STEPS],
        **fields,
    }


def document(
    backtest_id: str = "bt_7f3k2m9qx4", *, rows: int = 500, trades: int = 120, **fields: Any
) -> dict[str, Any]:
    """A finished backtest's light document: `rows` rows of the series, `trades` trades."""
    ts = [1_717_372_800 + 1800 * row for row in range(rows)]
    equity = [1.0 + row / 1000 for row in range(rows)]
    drawdown = [0.0] * rows
    drawdown[rows // 3 + 1] = -0.25  # off the evenly spaced rows
    finished = header(backtest_id, "finished", **fields)
    stats = {
        "total_return": 0.5,
        "max_drawdown": -0.25,
        "sharpe": 1.4,
        "sortino": 2.0,
        "num_trades": trades,
        "win_rate": 0.5,
        "avg_trade_return": 0.01,
        "best_trade_return": 0.1,
        "worst_trade_return": -0.05,
        "exposure": 0.4,
        "fees_paid": 0.01,
        "buy_and_hold_return": 0.2,
    }
    return {
        "version": 1,
        "backtest": finished,
        "model": {
            "id": "mdl_k2m9qx47f3",
            "name": "raw-candles.keras",
            "framework": "keras",
            "sha256": "0" * 64,
            "size_bytes": 1000,
            "manifest": {},
        },
        "data": {
            "instrument": {"id": "stock:AAPL", "name": "Apple Inc.", "currency": "USD"},
            "timeframe": "30m",
            "session": "regular",
            "period": PERIOD,
            "rows": {"first_ts": ts[0], "last_ts": ts[-1], "count": rows},
            "changed": False,
        },
        "decision": {"rule": {"name": "threshold", "limit": 0.1}, "long_only": True},
        "execution": {"fill": "next_open", "stop_loss": 0.05, "fee_bps": 5.0},
        "benchmarks": [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}],
        "results": {
            "stats": stats,
            "benchmarks": {"bh": {"total_return": 0.2}},
            "series": {
                "resolution": "candle",
                "count": rows,
                "columns": {"ts": ts, "close": equity, "equity": equity, "drawdown": drawdown},
                "benchmarks": {"bh": [1.0 + row / 3000 for row in range(rows)]},
            },
            "trades": {
                "count": trades,
                "total": trades + 5,
                "complete": False,
                "columns": {
                    "signal_ts": list(range(trades)),
                    "open_ts": list(range(trades)),
                    "close_ts": [row + 1 for row in range(trades)],
                    "side": [1] * trades,
                    "open_price": [100.0] * trades,
                    "close_price": [101.0] * trades,
                    "stop_price": [None] * trades,
                    "exit_reason": ["signal"] * trades,
                    "return": [0.01] * trades,
                    "fees": [0.001] * trades,
                    "slippage_cost": [0.0] * trades,
                    "candles_held": [3] * trades,
                },
            },
        },
    }


class FakeRuns:
    """Stands in for ``experiments.BacktestRuns``: ``statuses`` are the statuses each poll of a
    backtest's header answers in turn (the last one stays), ``documents`` the light documents;
    ``refuse`` makes every call raise it."""

    def __init__(self) -> None:
        self.statuses: dict[str, list[str]] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.refuse: Exception | None = None
        self.users: list[str] = []
        self.bodies: list[Any] = []
        self.polls = 0

    def _call(self, user_id: str) -> None:
        self.users.append(user_id)
        if self.refuse is not None:
            raise self.refuse

    async def header(self, user_id: str, backtest_id: str) -> BacktestHeader:
        self._call(user_id)
        self.polls += 1
        if backtest_id not in self.statuses:
            raise BacktestNotFound
        statuses = self.statuses[backtest_id]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return BacktestHeader.model_validate(header(backtest_id, status))

    async def check(self, user_id: str, body: Any) -> BacktestCheck:
        self._call(user_id)
        self.bodies.append(body)
        return BacktestCheck.model_validate(run.TOOLS[0].example.result.model_dump())

    async def start(self, user_id: str, body: Any) -> tuple[None, bytes]:
        self._call(user_id)
        self.bodies.append(body)
        return None, json.dumps({"backtest": header("bt_newnewnewn", "queued")}).encode()

    async def document(self, user_id: str, backtest_id: str, *, light: bool) -> tuple[int, bytes]:
        self._call(user_id)
        assert light
        if backtest_id not in self.documents:
            raise BacktestNotFound
        return 12, json.dumps(self.documents[backtest_id]).encode()

    async def stored(self, user_id: str, backtest_id: str) -> dict[str, Any]:
        self._call(user_id)
        if backtest_id == "bt_seedseedse":
            raise NoDocument
        if backtest_id not in self.documents:
            raise BacktestNotFound
        return self.documents[backtest_id]


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> FakeRuns:
    fake = FakeRuns()
    monkeypatch.setattr(run, "backtest_runs", lambda _state, _db: fake)
    monkeypatch.setattr(run, "POLL_S", 0.01)
    return fake


def call(name: str, arguments: dict[str, Any]):
    async def send(session: Client):
        return await session.call_tool(name, arguments)

    return send


def problem(result) -> dict[str, Any]:
    assert result.is_error
    return json.loads(result.content[0].text)


# --- validate_backtest and start_backtest ---------------------------------------------------


def test_validate_backtest_checks_the_setup_with_a_read_key(mcp, runs) -> None:
    result = mcp(call("validate_backtest", SETUP))

    assert not result.is_error, result.content
    assert result.structured_content["starts"] == "now"
    assert result.structured_content["instrument"]["id"] == "stock:AAPL"
    [body] = runs.bodies
    assert body.model.id == "mdl_k2m9qx47f3"
    assert body.decision.rule.name == "threshold"  # the API's defaults
    assert body.benchmarks == []
    assert runs.users == [ADA]


def test_a_setup_that_fails_a_check_is_the_api_s_problem(mcp, runs) -> None:
    runs.refuse = InstrumentNotFound(InstrumentId.parse("stock:ZZZZ"))

    body = problem(mcp(call("validate_backtest", SETUP)))

    assert body["type"] == PROBLEMS + "instrument-not-found"
    assert body["status"] == 404


def test_a_setup_that_does_not_fit_the_schema_is_a_validation_problem(mcp, runs) -> None:
    period = {"start": 9, "end": 1}
    setup = {**SETUP, "data": {"instrument": {"id": "stock:AAPL"}, "period": period}}

    body = problem(mcp(call("validate_backtest", setup)))

    assert body["type"] == PROBLEMS + "validation"
    assert body["errors"][0]["loc"][:2] == ["data", "period"]
    assert runs.bodies == []


def test_start_backtest_answers_its_status_and_place(mcp, runs, keys) -> None:
    benchmarks = [{"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}]
    arguments = {**SETUP, "execution": {"fee_bps": 5}, "benchmarks": benchmarks}

    result = mcp(call("start_backtest", arguments), key=keys.backtests_key)

    assert not result.is_error, result.content
    status = result.structured_content
    assert status["id"] == "bt_newnewnewn"
    assert status["status"] == "queued"
    assert status["queue_position"] == 2
    assert status["ended"] is False
    assert [step["id"] for step in status["steps"]] == STEPS
    assert status["url"] == "http://testserver/backtests/bt_newnewnewn"
    [body] = runs.bodies
    assert body.execution.fee_bps == 5
    assert body.benchmarks[0].instrument == "stock:AAPL"


def test_start_backtest_needs_backtests(mcp, runs) -> None:
    body = problem(mcp(call("start_backtest", SETUP)))

    assert body["type"] == PROBLEMS + "missing-scope"
    assert body["scope"] == "backtests"
    assert runs.bodies == []


def test_a_full_queue_is_the_api_s_problem(mcp, runs, keys) -> None:
    runs.refuse = QueueFull()

    body = problem(mcp(call("start_backtest", SETUP), key=keys.backtests_key))

    assert body["type"] == PROBLEMS + "queue-full"
    assert body["limit"] == 20


# --- get_backtest_status and wait_for_backtest ----------------------------------------------


def test_get_backtest_status_answers_at_once(mcp, runs) -> None:
    runs.statuses["bt_7f3k2m9qx4"] = ["queued", "finished"]

    status = mcp(call("get_backtest_status", {"backtest_id": "bt_7f3k2m9qx4"}))

    assert status.structured_content["status"] == "queued"
    assert status.structured_content["queue_position"] == 2
    assert runs.polls == 1


def test_an_unknown_backtest_is_not_found(mcp, runs) -> None:
    body = problem(mcp(call("get_backtest_status", {"backtest_id": "bt_unknownunk"})))

    assert body["type"] == PROBLEMS + "not-found"


def test_wait_for_backtest_polls_until_it_ended(mcp, runs) -> None:
    runs.statuses["bt_7f3k2m9qx4"] = ["queued", "running", "running", "failed"]

    status = mcp(call("wait_for_backtest", {"backtest_id": "bt_7f3k2m9qx4"}))

    assert status.structured_content["status"] == "failed"
    assert status.structured_content["ended"] is True
    assert status.structured_content["error"] == "It failed."
    assert runs.polls == 4


def test_wait_for_backtest_answers_the_status_at_its_timeout(mcp, runs) -> None:
    runs.statuses["bt_7f3k2m9qx4"] = ["running"]

    status = mcp(call("wait_for_backtest", {"backtest_id": "bt_7f3k2m9qx4", "timeout_s": 1}))

    assert status.structured_content["status"] == "running"
    assert status.structured_content["ended"] is False
    assert runs.polls > 2


def test_a_wait_is_a_minute_at_most(mcp, runs) -> None:
    arguments = {"backtest_id": "bt_7f3k2m9qx4", "timeout_s": 61}

    body = problem(mcp(call("wait_for_backtest", arguments)))

    assert body["type"] == PROBLEMS + "validation"
    assert runs.polls == 0


# --- get_backtest ---------------------------------------------------------------------------


def test_get_backtest_answers_the_results_in_short(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document()

    result = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4"}))

    assert not result.is_error, result.content
    answer = result.structured_content
    assert answer["backtest"]["url"] == "http://testserver/backtests/bt_7f3k2m9qx4"
    assert answer["backtest"]["author"] == {"id": ADA, "name": "Ada", "mine": True}
    assert answer["metrics"]["sharpe"] == 1.4
    assert answer["benchmark_returns"] == {"bh": 0.2}
    assert answer["candles"] == 500
    setup = answer["setup"]
    assert setup["model"]["id"] == "mdl_k2m9qx47f3"
    assert setup["data"]["instrument"] == {
        "id": "stock:AAPL",
        "name": "Apple Inc.",
        "currency": "USD",
    }
    assert setup["decision"]["rule"] == {"name": "threshold", "limit": 0.1}
    trades = answer["trades"]
    assert (trades["page"], trades["per_page"], trades["pages"]) == (1, 50, 3)
    assert (trades["count"], trades["total"], trades["complete"]) == (120, 125, False)
    assert len(trades["items"]) == 50
    assert trades["items"][0] == {
        "open_ts": 0,
        "close_ts": 1,
        "side": 1,
        "open_price": 100.0,
        "close_price": 101.0,
        "trade_return": 0.01,
        "fees": 0.001,
        "exit_reason": "signal",
        "candles_held": 3,
    }
    equity = answer["equity"]
    assert equity["points"] == len(equity["ts"]) == len(equity["equity"]) <= 200
    assert len(equity["benchmarks"]["bh"]) == equity["points"]
    assert equity["ts"][0] == 1_717_372_800
    assert equity["ts"][-1] == 1_717_372_800 + 1800 * 499
    assert min(equity["drawdown"]) == -0.25
    assert equity["rows"] == 500


def test_the_setup_of_get_backtest_starts_it_again(mcp, runs, keys) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document()
    read = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4"}))

    started = mcp(call("start_backtest", read.structured_content["setup"]), key=keys.backtests_key)

    assert not started.is_error, started.content
    [body] = runs.bodies
    assert body.data.period.start == PERIOD["start"]
    assert body.decision.long_only is True


def test_get_backtest_pages_through_the_trades(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document()

    last = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", "trades_page": 3}))
    past = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", "trades_page": 4}))

    items = last.structured_content["trades"]["items"]
    assert [item["open_ts"] for item in items] == list(range(100, 120))
    assert past.structured_content["trades"]["items"] == []


def test_a_short_series_comes_whole(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document(rows=30, trades=0)

    answer = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4"})).structured_content

    assert answer["equity"]["points"] == 30
    assert answer["trades"]["pages"] == 0


def test_a_running_backtest_has_no_results(mcp, runs) -> None:
    running = document()
    running["backtest"] = header(status="running")
    running["results"] = None
    running["data"]["rows"] = None
    runs.documents["bt_7f3k2m9qx4"] = running

    answer = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4"})).structured_content

    assert answer["backtest"]["status"] == "running"
    assert answer["metrics"] is None
    assert answer["trades"] is None
    assert answer["equity"] is None
    assert answer["candles"] is None
    assert answer["benchmark_returns"] == {}


def test_equity_rows_are_spaced_with_the_deepest_fall() -> None:
    drawdown = [0.0] * 1000
    drawdown[501] = -0.3

    rows = run.equity_rows(drawdown)

    assert len(rows) <= 200
    assert rows[0] == 0
    assert rows[-1] == 999
    assert 501 in rows
    assert rows == sorted(set(rows))


# --- compare_backtests ----------------------------------------------------------------------


def test_compare_backtests_answers_the_comparison(mcp, runs) -> None:
    runs.documents["bt_aaaaaaaaaa"] = document("bt_aaaaaaaaaa")
    runs.documents["bt_bbbbbbbbbb"] = document("bt_bbbbbbbbbb")

    result = mcp(call("compare_backtests", {"ids": ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"]}))

    assert not result.is_error, result.content
    answer = result.structured_content
    assert [item["id"] for item in answer["backtests"]] == ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"]
    assert answer["metrics"][0]["key"] == "total_return"
    assert len(answer["times"]) <= 200


@pytest.mark.parametrize(
    ("ids", "slug", "status"),
    [
        (["bt_aaaaaaaaaa", "bt_unknownunk"], "not-found", 404),
        (["bt_seedseedse"], "no-document", 409),
    ],
)
def test_a_backtest_that_cannot_be_compared_is_named(mcp, runs, ids, slug, status) -> None:
    runs.documents["bt_aaaaaaaaaa"] = document("bt_aaaaaaaaaa")

    body = problem(mcp(call("compare_backtests", {"ids": ids})))

    assert body["type"] == PROBLEMS + slug
    assert body["status"] == status
    assert body["id"] == ids[-1]


@pytest.mark.parametrize(
    ("ids", "says"),
    [
        (["bt_aaaaaaaaaa", "bt_aaaaaaaaaa"], "once"),
        (["bt_aaaaaaaaaa", ""], "empty ID"),
    ],
)
def test_ids_that_cannot_be_compared_are_a_validation_problem(mcp, runs, ids, says) -> None:
    body = problem(mcp(call("compare_backtests", {"ids": ids})))

    assert body["type"] == PROBLEMS + "validation"
    assert says in body["detail"]


def test_more_than_twenty_ids_do_not_fit(mcp, runs) -> None:
    ids = [f"bt_{n:010d}" for n in range(21)]

    body = problem(mcp(call("compare_backtests", {"ids": ids})))

    assert body["type"] == PROBLEMS + "validation"
    assert runs.users == []


@pytest.mark.parametrize(
    "name", ["get_backtest_status", "wait_for_backtest", "get_backtest", "validate_backtest"]
)
def test_the_reading_tools_need_read(mcp, runs, keys, name: str) -> None:
    arguments = SETUP if name == "validate_backtest" else {"backtest_id": "bt_7f3k2m9qx4"}

    body = problem(mcp(call(name, arguments), key=keys.backtests_key))

    assert body["scope"] == "read"
