"""The worker as it runs: its loop on a real Redis at REDIS_URL (flushed before each test) and
a real process with Keras or onnxruntime for each job, against the backend's ``JobClient``. The
raw-candles test models (tests/fixtures/keras and tests/fixtures/onnx) are the models; each
Keras job's process takes a few seconds to import Keras."""

import hashlib
import json
import shutil
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import numpy as np
import polars as pl
import pytest
from redis.asyncio import Redis

from candlestack.core import Settings, create_redis
from candlestack.jobs import JobClient, JobFiles, JobQueue
from candlestack.worker.service import serve

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
MODEL = "user-1/raw_candles.keras"
SHA256 = hashlib.sha256((FIXTURES / "raw_candles.keras").read_bytes()).hexdigest()
ONNX_FILE = Path(__file__).parents[2] / "fixtures" / "onnx" / "raw_candles.onnx"
ONNX_MODEL = "user-1/raw_candles.onnx"


@pytest.fixture
async def redis(redis_url: str) -> AsyncIterator[Redis]:
    client = create_redis(redis_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
def settings(redis_url: str, tmp_path: Path) -> Settings:
    models = tmp_path / "models"
    (models / "user-1").mkdir(parents=True)
    shutil.copy(FIXTURES / "raw_candles.keras", models / MODEL)
    shutil.copy(ONNX_FILE, models / ONNX_MODEL)
    return Settings(
        app_env="test",
        jobs_redis_url=redis_url,
        model_storage_dir=models,
        job_files_dir=tmp_path / "jobs",
        job_time_limit=60,
    )


@pytest.fixture
async def jobs(settings: Settings, redis: Redis) -> AsyncIterator[JobClient]:
    """The backend's client, with the worker running next to it."""
    async with anyio.create_task_group() as group:
        group.start_soon(serve, settings)
        yield JobClient(JobQueue(redis), JobFiles(settings.job_files_dir))
        group.cancel_scope.cancel()


def manifest() -> dict[str, Any]:
    return json.loads((FIXTURES / "raw_candles.manifest.json").read_text())


async def test_predict_returns_one_prediction_per_sample(
    jobs: JobClient, settings: Settings
) -> None:
    rng = np.random.default_rng(3)
    close = 100 + rng.random(40) * 10
    candles = pl.DataFrame(
        {
            "ts": 1_717_421_400 + np.arange(40) * 1800,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100 + rng.random(40),
        }
    )

    result, predictions = await jobs.predict(
        model=MODEL, sha256=SHA256, manifest=manifest(), candles=candles, wait=60
    )

    assert (result.status, result.samples, result.dropped) == ("done", 16, 24), result.error
    assert result.runner is not None
    assert result.runner.backend == "jax"
    assert result.timings.start is not None
    assert result.timings.total is not None
    assert predictions is not None
    assert predictions["ts"].to_list() == candles["ts"][24:].to_list()
    assert not (settings.job_files_dir / result.id).exists()


async def test_check_runs_the_trial_and_fails_a_wrong_model_file(jobs: JobClient) -> None:
    passed = await jobs.check(model=MODEL, sha256=SHA256, manifest=manifest(), wait=60)
    changed = await jobs.check(model=MODEL, sha256="0" * 64, manifest=manifest(), wait=60)

    assert (passed.status, passed.samples, passed.verified) == ("done", 64, False)
    assert (changed.status, changed.reason) == ("failed", "model-changed")


async def test_an_onnx_model_with_its_example_is_verified(jobs: JobClient) -> None:
    import onnxruntime  # noqa: PLC0415 (the worker's dependency, here to make the example)

    inputs = np.stack([np.full((25, 5), 101.0), np.linspace(95, 110, 125).reshape(25, 5)])
    session = onnxruntime.InferenceSession(ONNX_FILE, providers=["CPUExecutionProvider"])
    (output,) = session.run(None, {"candles": inputs.astype(np.float32)})
    example = {"input": inputs.tolist(), "output": np.asarray(output).tolist()}
    document = {**manifest(), "example": example}
    sha256 = hashlib.sha256(ONNX_FILE.read_bytes()).hexdigest()

    result = await jobs.check(model=ONNX_MODEL, sha256=sha256, manifest=document, wait=60)

    assert (result.status, result.samples, result.verified) == ("done", 64, True), result.error
    assert result.runner is not None
    assert (result.runner.keras, result.runner.backend) == (None, "onnxruntime")
