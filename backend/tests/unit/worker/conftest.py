"""Fixtures of the worker's tests: a model storage with the Keras and ONNX test files
(tests/fixtures/keras, tests/fixtures/onnx) and the manifest of ``raw_candles.keras``, which
``raw_candles.onnx`` takes too, and jobs for them."""

import hashlib
import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.jobs import Job

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
ONNX_FIXTURES = Path(__file__).parents[2] / "fixtures" / "onnx"
USER = "user-1"


def raw_manifest() -> dict[str, Any]:
    return json.loads((FIXTURES / "raw_candles.manifest.json").read_text())


@pytest.fixture
def models(tmp_path: Path) -> Path:
    """The model storage, with every Keras test file as ``user-1/<name>.keras`` and every ONNX
    one as ``user-1/<name>.onnx``."""
    root = tmp_path / "models"
    (root / USER).mkdir(parents=True)
    for path in [*FIXTURES.glob("*.keras"), *ONNX_FIXTURES.glob("*.onnx")]:
        shutil.copy(path, root / USER / path.name)
    return root


@pytest.fixture
def files_dir(tmp_path: Path) -> Path:
    return tmp_path / "jobs"


@pytest.fixture
def make_job(models: Path) -> Callable[..., Job]:
    def make(
        kind: str = "predict",
        name: str = "raw_candles",
        manifest: Any = None,
        suffix: str = ".keras",
        **changes: Any,
    ) -> Job:
        path = models / USER / f"{name}{suffix}"
        fields = {
            "kind": kind,
            "model": f"{USER}/{name}{suffix}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "0" * 64,
            "manifest": raw_manifest() if manifest is None else manifest,
            "deadline": time.time() + 60,
        }
        return Job.model_validate({**fields, **changes})

    return make


@pytest.fixture
def manifest() -> dict[str, Any]:
    """The manifest of ``raw_candles.keras``: the last 25 candles as they are."""
    return raw_manifest()


@pytest.fixture
def make_candles() -> Callable[[int], pl.DataFrame]:
    def make(count: int) -> pl.DataFrame:
        """``count`` 30-minute candles around 105, as the raw-candles model was trained on."""
        rng = np.random.default_rng(1)
        close = 100 + rng.random(count) * 10
        return pl.DataFrame(
            {
                "ts": 1_717_421_400 + np.arange(count, dtype=np.int64) * 1800,
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 100 + rng.random(count) * 10,
            }
        )

    return make
