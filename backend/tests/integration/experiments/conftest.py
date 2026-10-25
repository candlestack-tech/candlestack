"""Fixtures of the experiments integration tests: the app on a real Postgres, emptied before each
test (tests/integration/conftest.py), and signed-in users with their models and backtests made
straight in the database. Backtests run on the real job queue (Redis at REDIS_URL), with the
market data faked (``FakeMarket``, on ``market``) and the worker either faked (``FakeWorker``,
``worker``: it answers the queue as the worker does, without Keras) or real (``real_worker``).

Test modules cannot import this file, so the helpers they need hang on the ``BacktestsUser``
the ``ada`` and ``bob`` fixtures return and on the fixtures above.
"""

import hashlib
import json
import secrets
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import httpx2
import numpy as np
import polars as pl
import pytest
import redis
from fastapi import FastAPI

from candlestack.core import Settings, create_redis
from candlestack.data import (
    CandleSet,
    DataError,
    Instrument,
    InstrumentId,
    InstrumentInfo,
    InstrumentNotFound,
    Timeframe,
    fingerprint,
)
from candlestack.jobs import Job, JobFiles, JobQueue, JobResult, Reason, Runner
from candlestack.worker.service import JobProcess, Paths, Worker

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
RAW_CANDLES = FIXTURES / "raw_candles.keras"
MANIFEST: dict[str, Any] = json.loads((FIXTURES / "raw_candles.manifest.json").read_text())
# The market the fake has: 30-minute candles from FIRST on, around the clock (sessions do not
# matter to the backtests).
FIRST = 1_704_067_200  # 2024-01-01

# When the backtests of the tests start: #N an hour after #N-1.
EPOCH = datetime(2026, 10, 1, 12, tzinfo=UTC)
SETUP = {
    "timeframe": "30m",
    "period_start": "2021-01-04",
    "period_end": "2024-12-31",
    "direction": "long",
    "threshold": 0.05,
    "stop_loss_pct": 7,
    "fees_pct": 0.1,
    "slippage_pct": 0.02,
    "benchmark": "bh",
}


@pytest.fixture(autouse=True)
def _empty_tables(empty_tables: None) -> None:
    """Every test starts without users."""


@pytest.fixture
def settings(settings: Settings, redis_url: str, tmp_path: Path) -> Settings:
    """The app's job queue is the test Redis, emptied first."""
    with redis.Redis.from_url(redis_url) as queue:
        queue.flushdb()
    return settings.model_copy(
        update={"jobs_redis_url": redis_url, "job_files_dir": tmp_path / "jobs"}
    )


def _walk(ts: np.ndarray, scale: float) -> np.ndarray:
    return scale * (100 + 5 * np.sin(ts / 40_000) + 2 * np.sin(ts / 7_000))


class FakeMarket:
    """Stands in for the ``DataService`` of the backtests: stock:AAPL and crypto:BTCUSDT, a
    smooth walk of candles from ``FIRST`` on, each value a function of its ``ts`` (so every load
    of a period gives the same candles). ``scale`` changes the prices, as a source that adjusted
    them would; ``refuse`` makes the checks of a new backtest raise, ``refuse_candles`` the
    loads. ``slow`` seconds pass in each check of a new backtest, and ``held`` has how many
    connections of the app's pool (``pool``) were in use at each check and load."""

    def __init__(self) -> None:
        self.scale = 1.0
        self.refuse: DataError | None = None
        self.refuse_candles: DataError | None = None
        self.loads: list[tuple[int, int]] = []
        self.slow = 0.0
        self.pool: Any = None
        self.held: list[int] = []

    def _hold(self) -> None:
        if self.pool is not None:
            self.held.append(self.pool.checkedout())

    async def instrument(self, instrument_id: InstrumentId) -> InstrumentInfo:
        if self.refuse is not None:
            raise self.refuse
        if instrument_id.symbol not in {"AAPL", "BTCUSDT"}:
            raise InstrumentNotFound(instrument_id)
        stock = instrument_id.symbol == "AAPL"
        return InstrumentInfo(
            instrument=Instrument(
                id=instrument_id,
                name="Apple Inc." if stock else "BTC/USDT",
                source="alpaca" if stock else "binance",
                feed="sip" if stock else "spot",
                exchange="NASDAQ" if stock else "BINANCE",
                quote=None if stock else "USDT",
            ),
            timeframes=tuple(Timeframe),
            available_from=FIRST,
            available_to=FIRST + 10**8,
            max_candles=50_000,
        )

    async def warm_up_start(
        self,
        _instrument_id: InstrumentId,
        timeframe: Timeframe,
        start: int,
        _end: int,
        candles: int,
    ) -> int:
        self._hold()
        await anyio.sleep(self.slow)
        if self.refuse is not None:
            raise self.refuse
        return start - candles * timeframe.seconds

    async def candles(
        self, instrument_id: InstrumentId, timeframe: Timeframe, start: int, end: int
    ) -> CandleSet:
        self._hold()
        if self.refuse_candles is not None:
            raise self.refuse_candles
        self.loads.append((start, end))
        step = timeframe.seconds
        ts = np.arange(-(-start // step) * step, end, step, dtype=np.int64)
        close, opens = _walk(ts, self.scale), _walk(ts - step, self.scale)
        frame = pl.DataFrame(
            {
                "ts": ts,
                "open": opens,
                "high": np.maximum(opens, close) * 1.001,
                "low": np.minimum(opens, close) * 0.999,
                "close": close,
                "volume": np.full(ts.size, 1000.0),
            }
        )
        stock = instrument_id.market == "stock"
        source, feed = ("alpaca", "sip") if stock else ("binance", "spot")
        return CandleSet(
            instrument=instrument_id,
            timeframe=timeframe,
            start=start,
            end=end,
            source=source,
            feed=feed,
            frame=frame,
            gaps=[],
            gaps_total=0,
            fingerprint=fingerprint(source, feed, instrument_id, timeframe, start, end, frame),
        )


@pytest.fixture
def market(app: FastAPI, client: Any) -> FakeMarket:
    """The fake market data, in the app and in its backtest runner."""
    fake = FakeMarket()
    fake.pool = app.state.db.engine.pool
    app.state.data_service = fake
    app.state.backtest_runner._data = fake
    return fake


class FakeWorker:
    """Answers the jobs of the queue as the worker would, without a model: one prediction per
    candle after the window's first rows (the warm-up), the candle's return over the one before
    it in percent; or, with ``failure``, that failure (its reason and the user's sentences).
    A job claimed waits to be answered while ``gate`` is clear."""

    def __init__(self, redis_url: str, files: Path) -> None:
        self._redis_url = redis_url
        self._files = JobFiles(files)
        self.failure: tuple[Reason, str] | None = None
        self.jobs: list[Job] = []
        self.gate = threading.Event()
        self.gate.set()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=anyio.run, args=(self._serve,), daemon=True)

    def __enter__(self) -> "FakeWorker":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self.gate.set()
        self._thread.join(timeout=10)

    async def _serve(self) -> None:
        client = create_redis(self._redis_url)
        queue = JobQueue(client)
        try:
            while not self._stop.is_set():
                claimed = await queue.claim("fake-worker", 30)
                if claimed is None or claimed.job is None:
                    await anyio.sleep(0.02)
                    continue
                self.jobs.append(claimed.job)
                await anyio.to_thread.run_sync(self.gate.wait)
                await queue.finish(self._answer(claimed.job))
        finally:
            await client.aclose()

    def _answer(self, job: Job) -> JobResult:
        if self.failure is not None:
            return JobResult.failed(job.id, job.kind, *self.failure)
        candles = self._files.read_candles(job.id)
        dropped = job.manifest["input"]["window"]["length"] - 1
        close = candles["close"]
        predictions = pl.DataFrame(
            {"ts": candles["ts"], "prediction": (close / close.shift(1) - 1) * 100}
        ).slice(dropped)
        self._files.write_predictions(job.id, predictions)
        return JobResult(
            id=job.id,
            kind=job.kind,
            status="done",
            samples=predictions.height,
            dropped=dropped,
            runner=Runner(keras="3.15.1", backend="jax", backend_version="0.7.2"),
        )


@pytest.fixture
def worker(settings: Settings, market: FakeMarket) -> Iterator[FakeWorker]:
    with FakeWorker(settings.jobs_redis_url, settings.job_files_dir) as fake:
        yield fake


@pytest.fixture
def real_worker(settings: Settings, market: FakeMarket) -> Iterator[None]:
    """The worker's loop with a real process (Keras) for each job, in a thread of its own."""
    stop = threading.Event()

    async def serve() -> None:
        client = create_redis(settings.jobs_redis_url)
        loop = Worker(
            JobQueue(client),
            JobFiles(settings.job_files_dir),
            JobProcess(Paths(settings.model_storage_dir, settings.job_files_dir), 60),
            name="test-worker",
        )
        async with anyio.create_task_group() as group:
            group.start_soon(loop.serve)
            await anyio.to_thread.run_sync(stop.wait)
            group.cancel_scope.cancel()
        await client.aclose()

    thread = threading.Thread(target=anyio.run, args=(serve,), daemon=True)
    thread.start()
    yield
    stop.set()
    thread.join(timeout=30)


class BacktestsUser:
    """A signed-in user (the shared ``Account``: ``id``, ``email``, ``sql``, ``request``), the
    backtests API and rows made straight in the tables."""

    def __init__(self, account: Any, storage: Path) -> None:
        self._account = account
        self.storage = storage

    def __getattr__(self, name: str) -> Any:
        return getattr(self._account, name)

    def backtests(self, **params: Any) -> httpx2.Response:
        return self.request("GET", "/api/v1/backtests", params=params)

    def star(self, backtest_id: str, starred: bool = True) -> httpx2.Response:
        return self.request("PATCH", f"/api/v1/backtests/{backtest_id}", json={"starred": starred})

    def dashboard(self) -> httpx2.Response:
        return self.request("GET", "/api/v1/dashboard")

    def start(self, body: dict[str, Any], **kwargs: Any) -> httpx2.Response:
        return self.request("POST", "/api/v1/backtests", json=body, **kwargs)

    def document(self, backtest_id: str) -> httpx2.Response:
        return self.request("GET", f"/api/v1/backtests/{backtest_id}")

    def download(self, backtest_id: str) -> httpx2.Response:
        return self.request("GET", f"/api/v1/backtests/{backtest_id}/download")

    def ended(self, backtest_id: str, timeout: float = 30) -> dict[str, Any]:
        """The backtest's document once it has ended (or after ``timeout`` seconds)."""
        deadline = time.monotonic() + timeout
        while True:
            document = self.document(backtest_id).json()
            if document["backtest"]["status"] != "running" or time.monotonic() > deadline:
                return document
            time.sleep(0.05)

    def runnable_model(
        self,
        manifest: dict[str, Any] | None = None,
        *,
        file: Path | None = None,
        name: str = "raw-candles.keras",
    ) -> str:
        """A model with a manifest (the raw-candles test model's unless given); with ``file``
        also its file in the model storage, under the file's SHA-256."""
        model_id = self.model(name)
        sha256 = b"\0" * 32
        if file is not None:
            sha256 = hashlib.sha256(file.read_bytes()).digest()
            target = self.storage / self.id / f"{model_id}.keras"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(file, target)
        self.sql(
            "UPDATE models SET manifest = CAST(:manifest AS jsonb), sha256 = :sha WHERE id = :id",
            manifest=json.dumps(MANIFEST if manifest is None else manifest),
            sha=sha256,
            id=model_id,
        )
        return model_id

    def model(self, name: str, framework: str = "keras") -> str:
        """Adds a model straight in the table (its file does not matter here); its ID."""
        model_id = "mdl_" + secrets.token_hex(5)
        self.sql(
            "INSERT INTO models (id, user_id, name, framework, size_bytes, sha256, parameters, "
            "keras_version, created_at) VALUES (:id, :user_id, :name, :framework, 1000, :sha, 1, "
            "'3.15.1', now())",
            id=model_id,
            user_id=self.id,
            name=name,
            framework=framework,
            sha=b"\0" * 32,
        )
        return model_id

    def backtest(
        self,
        number: int,
        *,
        status: str = "finished",
        symbols: tuple[str, ...] = ("AAPL",),
        model_name: str = "lstm.keras",
        model_id: str | None = None,
        sharpe: float | None = 1.0,
        total_return: float | None = 0.1,
        max_drawdown: float | None = -0.1,
        win_rate: float | None = 0.5,
        num_trades: int | None = 100,
        starred: bool = False,
        seeded: bool = False,
        equity: dict[str, list[float]] | None = None,
    ) -> str:
        """Adds a backtest straight in the table, started `number` hours after ``EPOCH``; its
        ID. A failed one has no results."""
        backtest_id = f"bt_{self.id[4:8]}{number:06d}"
        failed = status == "failed"
        created = EPOCH + timedelta(hours=number)
        self.sql(
            "INSERT INTO backtests (id, user_id, number, status, instruments, representation, "
            "model_id, model_name, model_framework, setup, total_return, buy_and_hold_return, "
            "sharpe, max_drawdown, win_rate, num_trades, equity, duration_s, engine_version, "
            "error, starred, seeded, created_at, finished_at) VALUES (:id, :user_id, :number, "
            ":status, CAST(:instruments AS jsonb), 'renko', :model_id, :model_name, 'keras', "
            "CAST(:setup AS jsonb), :total_return, 0.5, :sharpe, :max_drawdown, :win_rate, "
            ":num_trades, CAST(:equity AS jsonb), 20, '0.2.0', :error, :starred, :seeded, "
            ":created, :finished)",
            id=backtest_id,
            user_id=self.id,
            number=number,
            status=status,
            instruments=json.dumps([{"market": "stock", "symbol": s} for s in symbols]),
            model_id=model_id,
            model_name=model_name,
            setup=json.dumps(SETUP),
            total_return=None if failed else total_return,
            sharpe=None if failed else sharpe,
            max_drawdown=None if failed else max_drawdown,
            win_rate=None if failed else win_rate,
            num_trades=None if failed else num_trades,
            equity=None if equity is None else json.dumps(equity),
            error="It failed." if failed else None,
            starred=starred,
            seeded=seeded,
            created=created,
            finished=created + timedelta(seconds=20),
        )
        return backtest_id


@pytest.fixture
def ada(account: Callable[[str], Any], settings: Settings) -> BacktestsUser:
    return BacktestsUser(account("usr_adaaaaaaaa"), settings.model_storage_dir)


@pytest.fixture
def bob(account: Callable[[str], Any], settings: Settings) -> BacktestsUser:
    return BacktestsUser(account("usr_bobbbbbbbb"), settings.model_storage_dir)
