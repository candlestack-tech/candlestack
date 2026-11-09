"""The tools that run and read backtests through the SDK's client over HTTP, with the API keys
(conftest.py) and the backtests service faked (``FakeRuns``): what each answers, the problems
of the service as tool errors and the scopes. Postgres, the queue and the worker are
integration tests (tests/integration/experiments/test_mcp_run.py)."""

import copy
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import anyio
import pytest
from mcp import Client

from candlestack import experiments
from candlestack.data import InstrumentId, InstrumentNotFound
from candlestack.experiments import BacktestCheck, BacktestHeader
from candlestack.experiments.compare import CompareMetric, CompareOut, compared
from candlestack.experiments.errors import (
    BacktestNotFound,
    NotComparable,
    QueueFull,
)
from candlestack.experiments.runs import Standing
from candlestack.mcp import convention, run

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
        "sweep_id": None,
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
    """Stands in for ``experiments.BacktestRuns``: ``sequence`` has the statuses each poll of a
    backtest's header answers in turn (the last one stays), ``documents`` the light documents
    (the comparison of ``fake_compare`` reads them too); ``refuse`` makes every call raise it.
    A backtest that has not ended is 30 s from its end. ``asked`` has the series and the trades
    each read of a light document asked for, and ``compares`` the curve points and the setup
    mode of each comparison."""

    def __init__(self) -> None:
        self.sequence: dict[str, list[str]] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.refuse: Exception | None = None
        self.users: list[str] = []
        self.bodies: list[Any] = []
        self.polls = 0
        self.asked: list[tuple[bool, bool]] = []
        self.compares: list[tuple[int, str]] = []

    def _call(self, user_id: str) -> None:
        self.users.append(user_id)
        if self.refuse is not None:
            raise self.refuse

    async def header(self, user_id: str, backtest_id: str) -> BacktestHeader:
        self._call(user_id)
        self.polls += 1
        if backtest_id not in self.sequence:
            raise BacktestNotFound
        statuses = self.sequence[backtest_id]
        status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
        return BacktestHeader.model_validate(header(backtest_id, status))

    async def statuses(self, user_id: str, backtest_ids: list[str]) -> list[Standing]:
        headers = [await self.header(user_id, backtest_id) for backtest_id in backtest_ids]
        return [
            Standing(header, None if header.status.value in {"finished", "failed"} else 30.0)
            for header in headers
        ]

    async def check(self, user_id: str, body: Any) -> BacktestCheck:
        self._call(user_id)
        self.bodies.append(body)
        return BacktestCheck.model_validate(run.TOOLS[0].example.result.model_dump())

    async def start(self, user_id: str, body: Any, _committed: Any = None) -> tuple[None, bytes]:
        self._call(user_id)
        self.bodies.append(body)
        self.sequence.setdefault("bt_newnewnewn", ["queued"])
        return None, json.dumps({"backtest": header("bt_newnewnewn", "queued")}).encode()

    async def light(
        self, user_id: str, backtest_id: str, *, series: bool = True, trades: bool = True
    ) -> tuple[int, dict[str, Any]]:
        self._call(user_id)
        self.asked.append((series, trades))
        if backtest_id not in self.documents:
            raise BacktestNotFound
        shown = copy.deepcopy(self.documents[backtest_id])
        if shown["results"] is not None:
            # As the service: only what was asked for.
            if not series:
                del shown["results"]["series"]
            if not trades:
                del shown["results"]["trades"]
        return 12, shown


async def fake_compare(
    runs: FakeRuns,
    user_id: str,
    ids: Sequence[str],
    *,
    curve_points: int,
    setup_mode: experiments.SetupMode,
) -> CompareOut:
    """``experiments.compare_backtests`` over the fake's documents."""
    runs._call(user_id)
    runs.compares.append((curve_points, setup_mode))
    for backtest_id in ids:
        if backtest_id == "bt_seedseedse":
            raise NotComparable(backtest_id, found=True)
        if backtest_id not in runs.documents:
            raise NotComparable(backtest_id, found=False)
    documents = [runs.documents[backtest_id] for backtest_id in ids]
    return compared(documents, points=curve_points, setup_mode=setup_mode)


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> FakeRuns:
    fake = FakeRuns()
    monkeypatch.setattr(run, "backtest_runs", lambda _state, _db: fake)
    monkeypatch.setattr(run, "POLL_S", 0.01)
    monkeypatch.setattr(experiments, "compare_backtests", fake_compare)
    monkeypatch.setattr(convention, "POLL_S", 0.01)
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
    assert status["eta_s"] == 30.0
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
    runs.refuse = QueueFull(240)

    body = problem(mcp(call("start_backtest", SETUP), key=keys.backtests_key))

    assert body["type"] == PROBLEMS + "queue-full"
    assert body["limit"] == 20
    assert body["retry_after_s"] == 240
    assert "in about 240 s" in body["detail"]


# --- get_backtest_status and wait_for_backtest ----------------------------------------------


def test_get_backtest_status_answers_at_once(mcp, runs) -> None:
    runs.sequence["bt_7f3k2m9qx4"] = ["queued", "finished"]

    status = mcp(call("get_backtest_status", {"backtest_id": "bt_7f3k2m9qx4"}))

    assert status.structured_content["status"] == "queued"
    assert status.structured_content["queue_position"] == 2
    assert runs.polls == 1


def test_an_unknown_backtest_is_not_found(mcp, runs) -> None:
    body = problem(mcp(call("get_backtest_status", {"backtest_id": "bt_unknownunk"})))

    assert body["type"] == PROBLEMS + "not-found"


def test_wait_for_backtest_polls_until_it_ended(mcp, runs) -> None:
    runs.sequence["bt_7f3k2m9qx4"] = ["queued", "running", "running", "failed"]

    status = mcp(call("wait_for_backtest", {"backtest_id": "bt_7f3k2m9qx4"}))

    assert status.structured_content["status"] == "failed"
    assert status.structured_content["ended"] is True
    assert status.structured_content["done"] is True
    assert status.structured_content["backtests"] is None
    assert status.structured_content["error"] == "It failed."
    assert runs.polls == 4


def test_wait_for_backtest_answers_the_status_at_its_timeout(mcp, runs) -> None:
    runs.sequence["bt_7f3k2m9qx4"] = ["running"]

    status = mcp(call("wait_for_backtest", {"backtest_id": "bt_7f3k2m9qx4", "timeout_s": 1}))

    assert status.structured_content["status"] == "running"
    assert status.structured_content["ended"] is False
    assert status.structured_content["done"] is False
    assert status.structured_content["eta_s"] == 30.0
    assert runs.polls > 2


def test_a_wait_reads_again_when_the_runner_says_the_backtest_changed(
    app, mcp, runs, monkeypatch
) -> None:
    # Without the runner's word, the next read would come only after the call's timeout.
    monkeypatch.setattr(run, "POLL_S", 30)
    runs.sequence["bt_7f3k2m9qx4"] = ["running", "finished"]
    runner = app.state.backtest_runner

    async def wait_and_change(session: Client) -> Any:
        async def change_soon() -> None:
            while not runner._watchers:  # noqa: ASYNC110 (no event tells of a new wait)
                await anyio.sleep(0.01)
            runner.changed("bt_7f3k2m9qx4")

        async with anyio.create_task_group() as group:
            group.start_soon(change_soon)
            started = anyio.current_time()
            result = await session.call_tool(
                "wait_for_backtest", {"backtest_id": "bt_7f3k2m9qx4", "timeout_s": 20}
            )
        return result, anyio.current_time() - started

    result, took = mcp(wait_and_change)

    assert result.structured_content["status"] == "finished"
    assert took < 10
    assert runs.polls == 2
    assert runner._watchers == {}


def test_wait_for_backtest_waits_for_all_of_ids_by_default(mcp, runs) -> None:
    runs.sequence["bt_aaaaaaaaaa"] = ["running", "finished"]
    runs.sequence["bt_bbbbbbbbbb"] = ["queued", "running", "running", "failed"]
    arguments = {"ids": ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb", "bt_aaaaaaaaaa"]}

    answer = mcp(call("wait_for_backtest", arguments)).structured_content

    assert answer["done"] is True
    assert [b["status"] for b in answer["backtests"]] == ["finished", "failed"]
    assert answer["id"] == "bt_aaaaaaaaaa"


def test_wait_for_backtest_with_until_any_answers_the_first_that_ended(mcp, runs) -> None:
    runs.sequence["bt_aaaaaaaaaa"] = ["running"]
    runs.sequence["bt_bbbbbbbbbb"] = ["queued", "running", "finished"]
    arguments = {"ids": ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"], "until": "any"}

    answer = mcp(call("wait_for_backtest", arguments)).structured_content

    assert answer["done"] is True
    assert (answer["id"], answer["status"]) == ("bt_bbbbbbbbbb", "finished")
    assert [b["status"] for b in answer["backtests"]] == ["running", "finished"]


def test_wait_for_backtest_answers_the_first_still_going_at_its_timeout(mcp, runs) -> None:
    runs.sequence["bt_aaaaaaaaaa"] = ["finished"]
    runs.sequence["bt_bbbbbbbbbb"] = ["running"]
    arguments = {"ids": ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"], "timeout_s": 1}

    answer = mcp(call("wait_for_backtest", arguments)).structured_content

    assert answer["done"] is False
    assert (answer["id"], answer["status"], answer["eta_s"]) == ("bt_bbbbbbbbbb", "running", 30.0)


@pytest.mark.parametrize(
    "arguments",
    [{}, {"backtest_id": "bt_aaaaaaaaaa", "ids": ["bt_bbbbbbbbbb"]}],
    ids=["neither", "both"],
)
def test_a_wait_needs_either_an_id_or_ids(mcp, runs, arguments: dict[str, Any]) -> None:
    body = problem(mcp(call("wait_for_backtest", arguments)))

    assert body["type"] == PROBLEMS + "validation"
    assert "either backtest_id or ids" in body["detail"]
    assert runs.polls == 0


def test_a_wait_follows_twenty_backtests_at_most(mcp, runs) -> None:
    ids = [f"bt_{index:010d}" for index in range(21)]

    body = problem(mcp(call("wait_for_backtest", {"ids": ids})))

    assert body["type"] == PROBLEMS + "validation"
    assert runs.polls == 0


def test_a_wait_is_a_minute_at_most(mcp, runs) -> None:
    arguments = {"backtest_id": "bt_7f3k2m9qx4", "timeout_s": 61}

    body = problem(mcp(call("wait_for_backtest", arguments)))

    assert body["type"] == PROBLEMS + "validation"
    assert runs.polls == 0


# --- get_backtest ---------------------------------------------------------------------------


# A page of 50 trades and the equity in at most 200 points.
MORE = {"trades_per_page": 50, "equity_points": 200}


def test_get_backtest_answers_the_setup_and_the_statistics_by_default(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document()

    result = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4"}))

    assert not result.is_error, result.content
    answer = result.structured_content
    assert answer["metrics"]["sharpe"] == 1.4
    assert answer["benchmark_returns"] == {"bh": 0.2}
    assert answer["setup"]["data"]["instrument"]["id"] == "stock:AAPL"
    assert (answer["trades"], answer["equity"]) == (None, None)
    # Neither the series nor the trades are read.
    assert runs.asked == [(False, False)]


def test_get_backtest_reads_only_what_it_answers(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document()

    trades = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", "trades_per_page": 5}))
    equity = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", "equity_points": 2}))

    assert len(trades.structured_content["trades"]["items"]) == 5
    assert trades.structured_content["equity"] is None
    assert equity.structured_content["trades"] is None
    assert equity.structured_content["equity"]["ts"] == [1_717_372_800, 1_717_372_800 + 1800 * 499]
    assert runs.asked == [(False, True), (True, False)]


def test_get_backtest_answers_the_results_in_short(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document()

    result = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", **MORE}))

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

    last = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", "trades_page": 3, **MORE}))
    past = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", "trades_page": 4, **MORE}))

    items = last.structured_content["trades"]["items"]
    assert [item["open_ts"] for item in items] == list(range(100, 120))
    assert past.structured_content["trades"]["items"] == []


def test_a_short_series_comes_whole(mcp, runs) -> None:
    runs.documents["bt_7f3k2m9qx4"] = document(rows=30, trades=0)

    answer = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", **MORE}))
    answer = answer.structured_content

    assert answer["equity"]["points"] == 30
    assert answer["trades"]["pages"] == 0


def test_a_running_backtest_has_no_results(mcp, runs) -> None:
    running = document()
    running["backtest"] = header(status="running")
    running["results"] = None
    running["data"]["rows"] = None
    runs.documents["bt_7f3k2m9qx4"] = running

    answer = mcp(call("get_backtest", {"backtest_id": "bt_7f3k2m9qx4", **MORE}))
    answer = answer.structured_content

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


@pytest.mark.parametrize(("points", "rows"), [(1, [999]), (2, [0, 999]), (3, [0, 501, 999])])
def test_a_few_equity_points_keep_the_end(points: int, rows: list[int]) -> None:
    drawdown = [0.0] * 1000
    drawdown[501] = -0.3

    assert run.equity_rows(drawdown, points) == rows


# --- compare_backtests ----------------------------------------------------------------------


def test_compare_backtests_answers_the_comparison(mcp, runs) -> None:
    runs.documents["bt_aaaaaaaaaa"] = document("bt_aaaaaaaaaa")
    runs.documents["bt_bbbbbbbbbb"] = document("bt_bbbbbbbbbb")

    result = mcp(call("compare_backtests", {"ids": ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"]}))

    assert not result.is_error, result.content
    answer = result.structured_content
    assert [item["id"] for item in answer["backtests"]] == ["bt_aaaaaaaaaa", "bt_bbbbbbbbbb"]
    # Each backtest's page, as get_backtest answers it, so the agent can show the user.
    assert [item["url"] for item in answer["backtests"]] == [
        "http://testserver/backtests/bt_aaaaaaaaaa",
        "http://testserver/backtests/bt_bbbbbbbbbb",
    ]
    # And the app's Compare page of both, in the order asked for.
    assert answer["url"] == "http://testserver/backtests/compare?ids=bt_aaaaaaaaaa,bt_bbbbbbbbbb"
    assert answer["metrics"][0]["key"] == "total_return"
    # Without curve points no curves: an agent asks for them.
    assert answer["times"] == []
    assert answer["backtests"][0]["equity"] is None
    assert answer["backtests"][0]["setup"]["instrument"] == "stock:AAPL"
    assert answer["shared_setup"] is None
    assert runs.compares == [(0, "full")]


def test_compare_backtests_draws_curves_and_shows_the_setup_once_when_asked(mcp, runs) -> None:
    runs.documents["bt_aaaaaaaaaa"] = document("bt_aaaaaaaaaa")
    other = document("bt_bbbbbbbbbb")
    other["decision"] = {"rule": {"name": "threshold", "limit": 0.3}, "long_only": True}
    runs.documents["bt_bbbbbbbbbb"] = other
    arguments = {"curve_points": 20, "setup_mode": "diff"}

    result = mcp(call("compare_backtests", {"ids": list(runs.documents), **arguments}))

    answer = result.structured_content
    assert 2 <= len(answer["times"]) <= 20
    assert len(answer["backtests"][0]["equity"]) == len(answer["times"])
    assert answer["differs"] == ["decision.rule.limit"]
    assert [item["setup"] for item in answer["backtests"]] == [
        {"decision": {"rule": {"limit": 0.1}}},
        {"decision": {"rule": {"limit": 0.3}}},
    ]
    assert answer["shared_setup"]["decision"] == {"rule": {"name": "threshold"}, "long_only": True}
    assert answer["shared_setup"]["instrument"] == "stock:AAPL"
    assert runs.compares == [(20, "diff")]


@pytest.mark.parametrize("count", [1, 5])
def test_the_compare_page_is_for_two_to_four_backtests(mcp, runs, count: int) -> None:
    """The page shows 2 to 4 side by side: one backtest has its own page, more have none."""
    ids = [f"bt_{index:010d}" for index in range(count)]
    for backtest_id in ids:
        runs.documents[backtest_id] = document(backtest_id)

    result = mcp(call("compare_backtests", {"ids": ids}))

    assert not result.is_error, result.content
    assert result.structured_content["url"] is None
    # Left out of the text, as every null.
    assert "url" not in json.loads(result.content[0].text)


def test_compare_says_how_to_read_the_max_drawdown() -> None:
    """Its values are 0 or below and `better` says lower: the description says the smaller fall
    is the better one, as the marks count it."""
    [spec] = [spec for spec in run.TOOLS if spec.name == "compare_backtests"]
    better = CompareMetric.model_fields["better"].description or ""

    assert "a smaller fall is better: -0.05 is better than -0.20" in spec.description
    assert "-0.05 beats -0.20 and its best is the highest value" in better


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
