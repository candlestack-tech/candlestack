"""The API and the runner in processes of their own (``APP_ROLE`` ``api`` and ``runner``), as
prod and stage run them, against a real Postgres and the real Redis (conftest.py): each process
is an app of its own in a TestClient, on an event loop of its own, and they meet only in
Postgres and in the runner's channel (``experiments.channel``). A start still answers
``running`` when a slot was free, a user's backtests still run one at a time, a delete stops
the run in the runner, the waits hear the runner, and what pub/sub loses the runner makes up
for."""

import secrets
import time
from collections.abc import Callable, Iterator
from typing import Any

import anyio
import pytest
import redis
from fastapi.testclient import TestClient
from redis.asyncio import Redis

from candlestack import main
from candlestack.core import Settings
from candlestack.experiments import BacktestRunner, RemoteRunner, RunnerChannel
from candlestack.main import create_app

pytestmark = pytest.mark.integration

DAY = 86_400
START = 1_704_067_200 + 10 * DAY  # 2024-01-11, ten days after the fake market's first candle
END = START + 5 * DAY
BH = {"id": "bh", "kind": "buy_and_hold", "instrument": "stock:AAPL"}


def body(model_id: str) -> dict[str, Any]:
    """A new backtest of the model on stock:AAPL over five days."""
    return {
        "model": {"id": model_id},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": START, "end": END}},
        "decision": {"rule": {"name": "threshold", "limit": 0.1}},
        "benchmarks": [BH],
    }


def soon(condition: Callable[[], object], timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "it did not happen in time"
        time.sleep(0.02)


@pytest.fixture
def prefix(monkeypatch: pytest.MonkeyPatch) -> str:
    """Channels of this test alone: other tests on the same Redis do not hear them."""
    prefix = f"candlestack:test-{secrets.token_hex(4)}:runner"
    monkeypatch.setattr(main, "channel_prefix", lambda _env: prefix)
    return prefix


@pytest.fixture
def settings(settings: Settings, prefix: str) -> Settings:
    """The app of the tests (``app``, ``client``, the users) is an API process."""
    return settings.model_copy(update={"app_role": "api"})


async def _closed() -> None:
    pass


class RunnerProcess:
    """The runner's process: an app of the role ``runner`` in a TestClient of its own, with
    the fake market data."""

    def __init__(self, settings: Settings, market: Any) -> None:
        self._settings = settings.model_copy(update={"app_role": "runner"})
        self._market = market
        # What the app's shutdown asks of its data service.
        market.close = _closed
        self._client: TestClient | None = None

    def start(self) -> None:
        app = create_app(self._settings)
        self._client = TestClient(app)
        # The runs its start fills already run on the fake market data.
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(main, "build_data_service", lambda *_: self._market)
            self._client.__enter__()

    def stop(self) -> None:
        if self._client is not None:
            self._client.__exit__(None, None, None)
            self._client = None

    @property
    def client(self) -> TestClient:
        assert self._client is not None
        return self._client

    @property
    def runner(self) -> BacktestRunner:
        return self.client.app.state.backtest_runner  # ty: ignore[unresolved-attribute]

    def running(self) -> set[str]:
        """The backtests whose runs this process has."""
        return set(self.runner._tasks.values())


@pytest.fixture
def runner_process(settings: Settings, market: Any) -> Iterator[RunnerProcess]:
    process = RunnerProcess(settings, market)
    process.start()
    yield process
    process.stop()


def test_a_start_answers_running_once_the_runner_gave_it_a_slot(
    ada, app, runner_process, worker
) -> None:
    remote = app.state.backtest_runner
    assert isinstance(remote, RemoteRunner)
    # The answer comes from the runner's word, not from a wait that ran out.
    remote._fill_wait = 10

    began = time.monotonic()
    started = ada.start(body(ada.runnable_model()))
    took = time.monotonic() - began
    backtest = started.json()["backtest"]
    ended = ada.ended(backtest["id"])["backtest"]

    assert started.status_code == 201, started.text
    assert backtest["status"] == "running"
    assert backtest["queue_position"] is None
    assert took < 5
    assert ended["status"] == "finished"


def test_a_users_backtests_still_run_one_at_a_time_in_order(ada, runner_process, worker) -> None:
    worker.gate.clear()  # the first holds its slot until the worker answers
    model_id = ada.runnable_model()
    first = ada.start(body(model_id)).json()["backtest"]
    second = ada.start(body(model_id)).json()["backtest"]
    assert (first["status"], second["status"]) == ("running", "queued")
    assert second["queue_position"] == 1
    worker.gate.set()

    done_first = ada.ended(first["id"])["backtest"]
    done_second = ada.ended(second["id"])["backtest"]

    assert (done_first["status"], done_second["status"]) == ("finished", "finished")
    assert done_second["started_at"] >= done_first["finished_at"]


def test_a_check_counts_the_slots_taken_in_postgres(ada, bob, app, runner_process) -> None:
    app.state.backtest_runner._slots = 1
    runner_process.runner._slots = 1
    model_id = ada.runnable_model()
    free = ada.request("POST", "/api/v1/backtests/check", json=body(model_id)).json()
    bobs = bob.start(body(bob.runnable_model())).json()["backtest"]["id"]  # no worker: it runs

    taken = ada.request("POST", "/api/v1/backtests/check", json=body(model_id)).json()

    assert runner_process.running() == {bobs}
    assert (free["starts"], taken["starts"]) == ("now", "queued")


def test_a_cancelled_backtest_never_runs(ada, runner_process) -> None:
    model_id = ada.runnable_model()
    running = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    queued = ada.start(body(model_id)).json()["backtest"]["id"]

    cancelled = ada.cancel(queued)

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["backtest"]["status"] == "failed"
    assert runner_process.running() == {running}


def test_a_deleted_running_backtest_is_stopped_in_the_runner(ada, runner_process) -> None:
    model_id = ada.runnable_model()
    running = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    queued = ada.start(body(model_id)).json()["backtest"]["id"]
    assert runner_process.running() == {running}

    deleted = ada.request("DELETE", f"/api/v1/backtests/{running}")

    assert deleted.status_code == 204
    soon(lambda: runner_process.running() == {queued})
    assert ada.document(queued).json()["backtest"]["status"] == "running"


def test_a_stop_the_runner_did_not_hear_is_made_at_its_next_check(ada, runner_process) -> None:
    running = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]  # no worker
    # Deleted as by an API process whose stop was lost.
    ada.sql("DELETE FROM backtests WHERE id = :id", id=running)
    assert runner_process.running() == {running}

    runner_process.client.portal.call(runner_process.runner._check)

    soon(lambda: runner_process.running() == set())


def test_without_the_runner_a_start_is_queued_and_runs_once_the_runner_is_back(
    ada, settings, market, worker
) -> None:
    started = ada.start(body(ada.runnable_model())).json()["backtest"]
    assert started["status"] == "queued"
    # Checked, so the runner's start checks no model: the fake worker runs predictions only.
    ada.sql("UPDATE models SET checked_at = now()")

    process = RunnerProcess(settings, market)
    process.start()
    try:
        ended = ada.ended(started["id"])["backtest"]
    finally:
        process.stop()

    assert ended["status"] == "finished"


def test_the_api_process_leaves_the_runs_to_the_runner_process(ada, settings, market) -> None:
    running = ada.start(body(ada.runnable_model())).json()["backtest"]["id"]  # queued: no runner
    ada.sql(
        "UPDATE backtests SET status = 'running', started_at = now() WHERE id = :id", id=running
    )

    with TestClient(create_app(settings)):  # another API process starts
        pass
    after_api = ada.document(running).json()["backtest"]
    process = RunnerProcess(settings, market)
    process.start()  # the runner fails what a stopped runner left running
    process.stop()
    after_runner = ada.document(running).json()["backtest"]

    assert after_api["status"] == "running"
    assert after_runner["status"] == "failed"
    assert "restarted" in after_runner["error"]


def test_a_runner_that_stops_fails_its_runs_and_leaves_the_queued_ones(
    ada, settings, market
) -> None:
    process = RunnerProcess(settings, market)
    process.start()
    model_id = ada.runnable_model()
    running = ada.start(body(model_id)).json()["backtest"]["id"]  # no worker answers
    queued = ada.start(body(model_id)).json()["backtest"]["id"]

    process.stop()

    stopped = ada.document(running).json()["backtest"]
    assert stopped["status"] == "failed"
    assert "restarted" in stopped["error"]
    assert ada.document(queued).json()["backtest"]["status"] == "queued"


def test_the_health_of_an_api_process_says_whether_the_runner_is_alive(
    client, settings, market, redis_url
) -> None:
    key = f"candlestack:{settings.app_env}:runner:alive"
    with redis.Redis.from_url(redis_url) as direct:
        direct.delete(key)
    without = client.get("/api/health")

    process = RunnerProcess(settings, market)
    process.start()
    try:
        soon(lambda: client.get("/api/health").json()["runner"] == "ok")
        with redis.Redis.from_url(redis_url) as direct:
            ttl = direct.ttl(key)
    finally:
        process.stop()

    assert without.status_code == 200
    assert (without.json()["status"], without.json()["runner"]) == ("ok", "error")
    assert 0 < ttl <= 30


# --- The channel itself ---------------------------------------------------------------------


async def _subscribers(redis: Redis, channel: str, count: int) -> None:
    with anyio.fail_after(5):
        # No event tells of a subscription.
        while dict(await redis.pubsub_numsub(channel)).get(channel.encode(), 0) < count:  # noqa: ASYNC110
            await anyio.sleep(0.01)


@pytest.mark.anyio
async def test_every_api_process_hears_what_the_runner_says_changed(
    redis_url: str, prefix: str
) -> None:
    redis = Redis.from_url(redis_url)
    runner = BacktestRunner(
        None,  # ty: ignore[invalid-argument-type]
        data=None,  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
        channel=RunnerChannel(redis, prefix),
    )
    apis = [
        RemoteRunner(None, None, RunnerChannel(redis, prefix), slots=2)  # ty: ignore[invalid-argument-type]
        for _ in range(2)
    ]
    for api in apis:
        api.start()
    try:
        await _subscribers(redis, f"{prefix}:changed", 2)
        with (
            apis[0].watch(["bt_aaaaaaaaaa"]) as first,
            apis[1].watch(["bt_aaaaaaaaaa"]) as second,
            apis[1].watch(["bt_bbbbbbbbbb"]) as other,
        ):
            runner.changed("bt_aaaaaaaaaa")
            with anyio.fail_after(5):
                await first.wait()
                await second.wait()
            assert not other.is_set()
    finally:
        for api in apis:
            await api.close()
        await runner.close()
        await redis.aclose()


@pytest.mark.anyio
async def test_a_wake_is_answered_once_the_runners_fill_ended(redis_url: str, prefix: str) -> None:
    redis = Redis.from_url(redis_url)
    runner = BacktestRunner(
        None,  # ty: ignore[invalid-argument-type]
        data=None,  # ty: ignore[invalid-argument-type]
        jobs=None,  # ty: ignore[invalid-argument-type]
        channel=RunnerChannel(redis, prefix),
    )
    filled = anyio.Event()

    async def fill() -> None:  # nothing waits in Postgres
        filled.set()

    runner._fill = fill  # ty: ignore[invalid-assignment]
    api = RemoteRunner(None, None, RunnerChannel(redis, prefix), slots=2, fill_wait=10)  # ty: ignore[invalid-argument-type]
    api.start()
    runner._listen()
    try:
        await _subscribers(redis, f"{prefix}:wake", 1)
        await _subscribers(redis, f"{prefix}:filled", 1)
        began = anyio.current_time()
        await api.fill()
        took = anyio.current_time() - began
    finally:
        await api.close()
        await runner.close()
        await redis.aclose()

    assert filled.is_set()
    assert took < 5
