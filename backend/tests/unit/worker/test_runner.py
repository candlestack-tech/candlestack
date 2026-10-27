"""One job in the job's process, run here in the test's own: the predictions of a ``predict``,
the trial run and the example of a ``check``, and a failure for every way a model, its
manifest or its candles can be wrong, said in plain words."""

import io
import json
import os
import subprocess
import sys
import tracemalloc
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.jobs import EXAMPLE_TOLERANCE, Job, JobFiles
from candlestack.preprocessing import build_input, parse_manifest
from candlestack.worker import model as keras_model
from candlestack.worker import runner
from candlestack.worker.model import load_keras
from candlestack.worker.onnx_model import load_onnx
from candlestack.worker.protocol import Outcome, Request
from candlestack.worker.runner import (
    FILE_MAX,
    INTERNAL,
    OUT_OF_MEMORY,
    PART_BYTES,
    main,
    part_sizes,
    run,
)

USER = "user-1"


@pytest.fixture
def run_job(models: Path, files_dir: Path) -> Callable[[Job], Outcome]:
    def run_it(job: Job) -> Outcome:
        return run(Request(job=job, models_dir=models, files_dir=files_dir))

    return run_it


def test_predict_writes_one_prediction_per_sample(
    make_job: Callable[..., Job],
    make_candles: Callable[[int], pl.DataFrame],
    run_job: Callable[[Job], Outcome],
    files_dir: Path,
) -> None:
    job = make_job()
    candles = make_candles(100)
    JobFiles(files_dir).write_candles(job.id, candles)

    outcome = run_job(job)

    result = outcome.result
    assert (result.status, result.samples, result.dropped) == ("done", 76, 24)
    assert result.runner is not None
    assert (result.runner.keras, result.runner.backend) == ("3.15.1", "jax")
    assert result.timings.load is not None
    assert result.timings.input is not None
    assert result.timings.predict is not None
    assert outcome.trace is None
    predictions = JobFiles(files_dir).read_predictions(job.id)
    assert predictions.schema == pl.Schema({"ts": pl.Int64, "prediction": pl.Float64})
    assert predictions["ts"].to_list() == candles["ts"][24:].to_list()
    assert predictions["prediction"].is_between(-1, 1).all()  # the model's tanh


def test_check_runs_the_model_on_built_in_candles(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome], files_dir: Path
) -> None:
    result = run_job(make_job("check")).result

    assert (result.status, result.samples, result.dropped) == ("done", 64, 24)
    assert (result.verified, result.example_difference) == (False, None)
    assert not files_dir.exists()  # a check needs no files


def renko_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """The raw-candles manifest on Renko triples with the window ``last: current``: still
    25 x 5, the volume replaced by each triple's direction."""
    document = edited(
        manifest,
        representation={"id": "renko-triples@1", "params": {"atr_multiple": 0.6}},
        window={"length": 25, "last": "current"},
    )
    document["input"]["features"][4] = {
        "name": "direction",
        "id": "renko-direction@1",
        "norm": {"id": "none@1"},
    }
    return document


class Counted:
    """The model, with the size of every input it predicted and its batch."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.calls: list[tuple[int, int]] = []

    def predict(self, samples: np.ndarray, *, batch_size: int, **options: Any) -> np.ndarray:
        self.calls.append((len(samples), batch_size))
        return self.model.predict(samples, batch_size=batch_size, **options)


@pytest.mark.parametrize("window", ["closed", "current"])
def test_predictions_made_a_part_at_a_time_are_the_predictions_at_once(
    make_job: Callable[..., Job],
    make_candles: Callable[[int], pl.DataFrame],
    run_job: Callable[[Job], Outcome],
    models: Path,
    files_dir: Path,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    window: str,
) -> None:
    document = manifest if window == "closed" else renko_manifest(manifest)
    job = make_job(manifest=document)
    candles = make_candles(803)
    JobFiles(files_dir).write_candles(job.id, candles)
    (models.parent / "scratch").mkdir()
    model = Counted(load_keras(models / USER / "raw_candles.keras", models.parent / "scratch"))
    monkeypatch.setattr(keras_model, "load_keras", lambda *_: model)
    # Parts of 8 samples of 25 x 5 float32 (500 bytes each), in batches of 4.
    monkeypatch.setattr(runner, "BATCH", 4)
    monkeypatch.setattr(runner, "PART_BYTES", 10 * 500)

    result = run_job(job).result

    built = build_input(candles, parse_manifest(document))
    count = len(built.windows)
    assert (result.status, result.samples, result.dropped) == ("done", count, built.dropped)
    assert model.calls == [(8, 4)] * (count // 8) + [(count % 8, 4)]  # the last part shorter
    at_once = model.model.predict(built.samples, batch_size=4, verbose=0)
    predictions = JobFiles(files_dir).read_predictions(job.id)
    assert predictions["ts"].equals(built.ts)
    assert predictions["prediction"].to_list() == at_once[:, 0].astype(np.float64).tolist()


@pytest.mark.parametrize(
    ("sample_bytes", "sizes"),
    [
        (25 * 28 * 4, (11 * 1024, 1024)),  # 32 MB hold 11983 samples: 11 whole batches
        (1000 * 100 * 4, (83, 83)),  # a batch of 1024 would take 400 MB
        (PART_BYTES + 1, (1, 1)),  # one sample at least
    ],
)
def test_a_part_of_the_input_is_whole_batches_within_its_memory(
    sample_bytes: int, sizes: tuple[int, int]
) -> None:
    assert part_sizes(sample_bytes) == sizes


class Zeros:
    def predict(self, samples: np.ndarray, **_: object) -> np.ndarray:
        return np.zeros((len(samples), 1), dtype=np.float32)


def test_the_input_of_50000_candles_of_25_x_28_never_takes_its_whole_memory(
    make_candles: Callable[[int], pl.DataFrame], manifest: dict[str, Any]
) -> None:
    """All at once, 50000 samples of 25 x 28 float32 are 140 MB; a part at a time, the numpy
    arrays of the build and the prediction stay within a part and the rows."""
    features = [
        {"name": f"close{window}", "id": "sma@1", "params": {"window": window}}
        for window in range(2, 30)
    ]
    document = edited(manifest, features=features)
    document["signature"]["input"]["shape"] = [25, 28]
    parsed = parse_manifest(document)
    candles = make_candles(50_000)
    model: Any = Zeros()

    tracemalloc.start()
    try:
        built = build_input(candles, parsed)
        scores = runner._predict(model, built.windows, parsed)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert scores.shape == (len(built.windows), 1) == (50_000 - 28 - 24, 1)
    assert built.windows.sample_bytes * len(built.windows) > 139_000_000
    assert peak < PART_BYTES + 24_000_000


def example_of(models: Path, manifest: dict[str, Any], shift: float = 0.0) -> dict[str, Any]:
    """The manifest with an example of two samples and the model's own output for them."""
    inputs = np.stack([np.full((25, 5), 101.0), np.linspace(95, 110, 125).reshape(25, 5)])
    scratch = models.parent / "scratch"
    scratch.mkdir(exist_ok=True)
    model = load_keras(models / USER / "raw_candles.keras", scratch)
    output = model.predict(inputs.astype(np.float32), verbose=0) + shift
    return {**manifest, "example": {"input": inputs.tolist(), "output": output.tolist()}}


def test_check_with_an_example_verifies_the_model(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    models: Path,
    manifest: dict[str, Any],
) -> None:
    job = make_job("check", manifest=example_of(models, manifest))

    result = run_job(job).result

    assert (result.status, result.verified) == ("done", True)
    assert result.example_difference is not None
    assert result.example_difference <= EXAMPLE_TOLERANCE


def test_an_example_the_model_does_not_give_fails_the_check(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    models: Path,
    manifest: dict[str, Any],
) -> None:
    job = make_job("check", manifest=example_of(models, manifest, shift=0.01))

    result = run_job(job).result

    assert (result.status, result.reason, result.verified) == ("failed", "example", False)
    assert result.example_difference == pytest.approx(0.01, abs=1e-6)
    assert result.error is not None
    assert result.error.startswith("For the manifest's example the model gives up to 0.01")
    assert result.runner is not None


def edited(manifest: dict[str, Any], **changes: Any) -> dict[str, Any]:
    document = json.loads(json.dumps(manifest))
    document["input"].update(changes)
    return document


@pytest.mark.parametrize(
    ("change", "start"),
    [
        ({"window": {"length": "25"}}, "The manifest is not valid. input.window.length"),
        ({"representation": {"id": "renko@1"}}, "The manifest cannot run. The catalog has no"),
    ],
)
def test_a_manifest_that_cannot_run_fails_the_job(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    manifest: dict[str, Any],
    change: dict[str, Any],
    start: str,
) -> None:
    result = run_job(make_job("check", manifest=edited(manifest, **change))).result

    assert (result.status, result.reason) == ("failed", "manifest")
    assert result.error is not None
    assert result.error.startswith(start)


def test_a_deleted_model_fails_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome]
) -> None:
    result = run_job(make_job("check", name="deleted")).result

    assert (result.reason, result.error) == (
        "model-missing",
        "The model's file is gone. Upload the model again.",
    )


@pytest.mark.skipif(os.name != "posix", reason="symbolic links")
def test_a_link_out_of_the_storage_is_no_model(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome], models: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.keras"
    outside.write_bytes((models / USER / "raw_candles.keras").read_bytes())
    (models / USER / "link.keras").symlink_to(outside)

    result = run_job(make_job("check", name="link")).result

    assert result.reason == "model-missing"


def test_a_changed_model_file_fails_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome]
) -> None:
    result = run_job(make_job("check", sha256="0" * 64)).result

    assert result.reason == "model-changed"


def test_a_model_keras_refuses_fails_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome]
) -> None:
    result = run_job(make_job("check", name="lambda_layer")).result

    assert result.reason == "load"
    assert result.error is not None
    assert result.error.startswith("Keras could not load the model (")


def test_a_predict_without_its_candles_fails(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome]
) -> None:
    result = run_job(make_job()).result

    assert (result.reason, result.error) == (
        "candles",
        "The job's candles are missing. Run it again.",
    )


def test_too_few_candles_for_one_sample_fail_the_job(
    make_job: Callable[..., Job],
    make_candles: Callable[[int], pl.DataFrame],
    run_job: Callable[[Job], Outcome],
    files_dir: Path,
) -> None:
    job = make_job()
    JobFiles(files_dir).write_candles(job.id, make_candles(24))

    result = run_job(job).result

    assert (result.reason, result.error) == (
        "candles",
        "The 24 candles make no sample: the manifest needs at least 25. Choose a longer period.",
    )


def test_candles_that_break_their_rules_fail_the_job(
    make_job: Callable[..., Job],
    make_candles: Callable[[int], pl.DataFrame],
    run_job: Callable[[Job], Outcome],
    files_dir: Path,
) -> None:
    job = make_job()
    JobFiles(files_dir).write_candles(job.id, make_candles(30).reverse())

    result = run_job(job).result

    assert result.reason == "candles"
    assert result.error == (
        "The model's input cannot be built. Each candle's ts must be after the one before it."
    )


def test_a_model_that_fails_on_its_input_fails_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome]
) -> None:
    # 25 x 23 in, but the manifest builds 25 x 5.
    result = run_job(make_job("check", name="functional")).result

    assert result.reason == "predict"
    assert result.error is not None
    assert result.error.startswith("The model failed on its input (")


def test_an_output_of_another_shape_fails_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome], manifest: dict[str, Any]
) -> None:
    # 30 x 5 in, three numbers out.
    document = edited(manifest, window={"length": 30})
    document["signature"]["input"]["shape"] = [30, 5]

    result = run_job(make_job("check", name="sequential", manifest=document)).result

    assert (result.reason, result.error) == (
        "output",
        "The model gave an output of shape [3] per sample, the manifest says [1].",
    )


class NotFinite:
    def predict(self, samples: np.ndarray, **_: object) -> np.ndarray:
        output = np.zeros((len(samples), 1), dtype=np.float32)
        output[0, 0] = np.nan
        return output


def test_numbers_that_are_not_finite_fail_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(keras_model, "load_keras", lambda *_: NotFinite())

    result = run_job(make_job("check")).result

    assert (result.reason, result.error) == (
        "output",
        "The model gave 1 of 64 values that are not finite numbers.",
    )


class WrongOnTheExample:
    """Gives zeros for the trial run, and for the example's two samples `answer`."""

    def __init__(self, answer: Callable[[int], np.ndarray]) -> None:
        self.answer = answer

    def predict(self, samples: np.ndarray, **_: object) -> np.ndarray:
        if len(samples) == 2:
            return self.answer(len(samples))
        return np.zeros((len(samples), 1), dtype=np.float32)


def fail_on_input(_count: int) -> np.ndarray:
    raise ValueError("bad input")


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (fail_on_input, "For the manifest's example: The model failed on its input (ValueError)."),
        (
            lambda count: np.full((count, 1), np.nan, dtype=np.float32),
            "For the manifest's example: The model gave 2 of 2 values that are not finite numbers.",
        ),
        (
            lambda count: np.zeros((count, 3), dtype=np.float32),
            (
                "For the manifest's example: The model gave an output of shape [3] per sample, "
                "the manifest says [1]."
            ),
        ),
    ],
)
def test_a_model_that_fails_on_the_example_fails_the_example_not_the_trial_run(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    models: Path,
    manifest: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    answer: Callable[[int], np.ndarray],
    error: str,
) -> None:
    job = make_job("check", manifest=example_of(models, manifest))
    monkeypatch.setattr(keras_model, "load_keras", lambda *_: WrongOnTheExample(answer))

    result = run_job(job).result

    assert (result.status, result.reason, result.verified) == ("failed", "example", False)
    assert result.error is not None
    assert result.error.startswith(error)


def test_a_bug_of_ours_is_an_internal_failure_with_its_trace(
    make_job: Callable[..., Job],
    make_candles: Callable[[int], pl.DataFrame],
    run_job: Callable[[Job], Outcome],
    files_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = make_job()
    JobFiles(files_dir).write_candles(job.id, make_candles(30))

    def broken(*_: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(JobFiles, "write_predictions", broken)

    outcome = run_job(job)

    assert (outcome.result.reason, outcome.result.error) == ("internal", INTERNAL)
    assert outcome.trace is not None
    assert "RuntimeError: a bug" in outcome.trace


class TooLarge:
    def predict(self, samples: np.ndarray, **_: object) -> np.ndarray:
        raise MemoryError


def raise_memory_error(*_: object) -> None:
    raise MemoryError


@pytest.mark.parametrize(
    ("module", "target", "replacement"),
    [
        (keras_model, "load_keras", raise_memory_error),
        (keras_model, "load_keras", lambda *_: TooLarge()),
        (runner, "build_input", raise_memory_error),
    ],
)
def test_a_model_that_needs_more_memory_than_there_is_is_no_bug(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    target: str,
    replacement: Callable[..., object],
) -> None:
    monkeypatch.setattr(module, target, replacement)

    outcome = run_job(make_job("check"))

    assert (outcome.result.reason, outcome.result.error) == ("stopped", OUT_OF_MEMORY)
    assert outcome.trace is None


def test_main_reads_the_request_and_writes_the_outcome(
    make_job: Callable[..., Job], models: Path, files_dir: Path
) -> None:
    request = Request(job=make_job("check"), models_dir=models, files_dir=files_dir)
    stdout = io.StringIO()

    assert main(io.BytesIO(request.model_dump_json().encode()), stdout, limits=False) == 0

    outcome = Outcome.model_validate_json(stdout.getvalue())
    assert (outcome.result.id, outcome.result.status) == (request.job.id, "done")


def test_the_jobs_process_limits_itself_on_linux(
    make_job: Callable[..., Job],
    models: Path,
    files_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits: list[tuple[str, tuple[int, int]]] = []

    class FakeResource:
        RLIMIT_CORE, RLIMIT_FSIZE = "core", "fsize"

        @staticmethod
        def setrlimit(limit: str, values: tuple[int, int]) -> None:
            limits.append((limit, values))

    oom_score_adj = tmp_path / "oom_score_adj"
    monkeypatch.setattr(runner.sys, "platform", "linux")
    monkeypatch.setitem(runner.sys.modules, "resource", FakeResource)
    monkeypatch.setattr(runner, "OOM_SCORE_ADJ", oom_score_adj)
    request = Request(job=make_job("check", name="deleted"), models_dir=models, files_dir=files_dir)

    main(io.BytesIO(request.model_dump_json().encode()), io.StringIO(), limits=True)

    assert limits == [("core", (0, 0)), ("fsize", (FILE_MAX, FILE_MAX))]
    assert oom_score_adj.read_text() == "1000"


def test_an_onnx_model_predicts_with_onnxruntime(
    make_job: Callable[..., Job],
    make_candles: Callable[[int], pl.DataFrame],
    run_job: Callable[[Job], Outcome],
    files_dir: Path,
    models: Path,
) -> None:
    job = make_job(suffix=".onnx")
    candles = make_candles(100)
    JobFiles(files_dir).write_candles(job.id, candles)

    result = run_job(job).result

    assert (result.status, result.samples, result.dropped) == ("done", 76, 24), result.error
    assert result.runner is not None
    assert (result.runner.keras, result.runner.backend) == (None, "onnxruntime")
    predictions = JobFiles(files_dir).read_predictions(job.id)
    assert predictions["ts"].to_list() == candles["ts"][24:].to_list()
    # The same model, run here alone on the windows of the candles.
    columns = candles.select("open", "high", "low", "close", "volume").to_numpy()
    windows = np.stack([columns[end - 25 : end] for end in range(25, 101)]).astype(np.float32)
    model = load_onnx(models / USER / "raw_candles.onnx", models.parent)
    expected = model.predict(windows, batch_size=1024)[:, 0]
    np.testing.assert_allclose(predictions["prediction"].to_numpy(), expected, atol=1e-6)


def test_an_onnx_model_with_its_example_is_verified(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    models: Path,
    manifest: dict[str, Any],
) -> None:
    inputs = np.stack([np.full((25, 5), 101.0), np.linspace(95, 110, 125).reshape(25, 5)])
    model = load_onnx(models / USER / "raw_candles.onnx", models.parent)
    output = model.predict(inputs, batch_size=2)
    document = {**manifest, "example": {"input": inputs.tolist(), "output": output.tolist()}}

    result = run_job(make_job("check", manifest=document, suffix=".onnx")).result

    assert (result.status, result.samples, result.verified) == ("done", 64, True)
    assert result.example_difference is not None
    assert result.example_difference <= EXAMPLE_TOLERANCE


def test_an_onnx_file_onnxruntime_refuses_fails_the_job(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome], models: Path
) -> None:
    (models / USER / "broken.onnx").write_bytes(b"no model")

    result = run_job(make_job("check", name="broken", suffix=".onnx")).result

    assert result.reason == "load"
    assert result.error is not None
    assert result.error.startswith("onnxruntime could not load the model (")
    assert result.error.endswith("Export the model to ONNX again and upload that file.")
    assert result.runner is not None
    assert result.runner.backend == "onnxruntime"


def test_a_failure_before_the_framework_is_known_names_no_runner(
    make_job: Callable[..., Job], run_job: Callable[[Job], Outcome], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_model: str) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(runner, "framework_of", broken)

    outcome = run_job(make_job("check"))

    assert (outcome.result.reason, outcome.result.runner) == ("internal", None)


def test_the_job_of_an_onnx_model_does_not_import_keras(
    make_job: Callable[..., Job], models: Path, files_dir: Path
) -> None:
    request = Request(job=make_job("check", suffix=".onnx"), models_dir=models, files_dir=files_dir)
    code = (
        "import io, sys\n"
        "from candlestack.worker import runner\n"
        "out = io.StringIO()\n"
        "runner.main(sys.stdin.buffer, out, limits=False)\n"
        "print(out.getvalue())\n"
        "print(sorted({'keras', 'jax', 'onnxruntime'} & set(sys.modules)))\n"
    )

    done = subprocess.run(
        [sys.executable, "-c", code],
        input=request.model_dump_json().encode(),
        capture_output=True,
        check=True,
    )

    outcome, imported = done.stdout.decode().strip().splitlines()
    assert Outcome.model_validate_json(outcome).result.status == "done"
    assert imported == "['onnxruntime']"
