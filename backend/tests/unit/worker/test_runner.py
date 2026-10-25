"""One job in the job's process, run here in the test's own: the predictions of a ``predict``,
the trial run and the example of a ``check``, and a failure for every way a model, its
manifest or its candles can be wrong, said in plain words."""

import io
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.jobs import EXAMPLE_TOLERANCE, Job, JobFiles
from candlestack.worker import runner
from candlestack.worker.model import load_keras
from candlestack.worker.protocol import Outcome, Request
from candlestack.worker.runner import FILE_MAX, INTERNAL, OUT_OF_MEMORY, main, run

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
    monkeypatch.setattr(runner, "load_keras", lambda *_: NotFinite())

    result = run_job(make_job("check")).result

    assert (result.reason, result.error) == (
        "output",
        "The model gave 1 of 64 values that are not finite numbers.",
    )


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
    ("target", "replacement"),
    [
        ("load_keras", raise_memory_error),
        ("load_keras", lambda *_: TooLarge()),
        ("build_input", raise_memory_error),
    ],
)
def test_a_model_that_needs_more_memory_than_there_is_is_no_bug(
    make_job: Callable[..., Job],
    run_job: Callable[[Job], Outcome],
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    replacement: Callable[..., object],
) -> None:
    monkeypatch.setattr(runner, target, replacement)

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
