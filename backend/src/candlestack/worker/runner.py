"""The process of one job, started by the worker's loop (``service.py``) for every job: reads
the request as JSON on stdin, runs the job and writes its outcome as JSON on stdout.

A fresh process per job: a model that crashes, hangs or exhausts the memory takes down this
process only, the loop fails the job and goes on, and no state of one model reaches the next.
It is the only process that imports Keras or onnxruntime, and only the one the job's model
needs (the extension of its file says which): Keras takes seconds to import. It gets no keys
and no Redis URL from the loop, and the container it runs in has no network.
"""

import hashlib
import os
import sys
import tempfile
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol

import numpy as np
import polars as pl

from candlestack.jobs import EXAMPLE_TOLERANCE, JobFiles, JobResult, Reason, Runner, Timings
from candlestack.preprocessing import (
    InvalidManifest,
    Manifest,
    ModelInput,
    Samples,
    build_input,
    check_manifest,
    parse_manifest,
    warm_up,
)
from candlestack.worker.protocol import Outcome, Request
from candlestack.worker.samples import sample_candles

# Samples per batch of a prediction: large batches are faster, and 1024 samples of 25 x 23
# floats are 2.4 MB.
BATCH = 1024
# The most memory a part of the input takes: the samples are built and predicted a part at a
# time, as all of them at once grow with the period (134 MB for 50000 samples of 25 x 28) and
# a part does not. 32 MB are 11264 samples of 25 x 28 (11 batches): few calls of the model, and
# far under the worker's limit beside Keras and JAX (docs/architecture.md, "Worker").
PART_BYTES = 32 * 1024 * 1024
# The largest file the process may write (Linux): the model's copy (a Keras file without its
# compile config, of at most 50 MB unpacked to 100 MB of weights at most, or an ONNX file of at
# most 50 MB) and the predictions.
FILE_MAX = 512 * 1024 * 1024
ONNX_SUFFIX = ".onnx"
INTERNAL = "The worker failed on this job. This is a bug of CandleStack and it was reported."
OUT_OF_MEMORY = (
    "The model needs more memory than the worker has. Choose a shorter period or a smaller model."
)


class Model(Protocol):
    """A loaded model: Keras' own, or ``onnx_model.OnnxModel``, which answers alike."""

    def predict(self, x: Any, *, batch_size: int, verbose: int) -> Any: ...


@dataclass(frozen=True, slots=True)
class Framework:
    """What runs the models of one format: how a file loads (from its path, with a scratch
    directory), what the result names as the runner, and what the user reads when the file
    does not load (with ``{error}``, the type of the error)."""

    load: Callable[[Path, Path], Model]
    runner: Runner
    refused: str


def _keras() -> Framework:
    # Only the job of a Keras model imports Keras (2 to 4 s).
    import jax  # noqa: PLC0415
    import keras  # noqa: PLC0415

    from candlestack.worker import model  # noqa: PLC0415

    return Framework(
        load=model.load_keras,
        runner=Runner(
            keras=keras.__version__,
            backend=keras.backend.backend(),
            backend_version=jax.__version__,
        ),
        refused="Keras could not load the model ({error}). Save the model again with "
        'model.save("model.keras") and upload that file.',
    )


def _onnx() -> Framework:
    import onnxruntime  # noqa: PLC0415 (only the job of an ONNX model)

    from candlestack.worker import onnx_model  # noqa: PLC0415

    return Framework(
        load=onnx_model.load_onnx,
        runner=Runner(backend="onnxruntime", backend_version=onnxruntime.__version__),
        refused="onnxruntime could not load the model ({error}). Export the model to ONNX "
        "again and upload that file.",
    )


def framework_of(model: str) -> Framework:
    """What runs the model file named `model`, by its extension."""
    return _onnx() if model.endswith(ONNX_SUFFIX) else _keras()


class JobFailed(Exception):
    """The job failed for a reason the user can act on; ``message`` says it to them."""

    def __init__(self, reason: Reason, message: str, **found: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.found = found


def run(request: Request) -> Outcome:
    """Runs the job; never raises."""
    began_at = time.time()
    job = request.job
    timings: dict[str, float] = {}
    trace = None
    framework = None
    try:
        framework = framework_of(job.model)
        result = _run(request, framework, timings)
    except JobFailed as failed:
        runner = None if framework is None else framework.runner
        result = JobResult.failed(job.id, job.kind, failed.reason, failed.message).model_copy(
            update={"runner": runner, **failed.found}
        )
    except MemoryError:
        # An allocation larger than the memory can ever give (a long period of a large window)
        # fails here; a smaller one past the container's limit gets this process killed, which
        # the loop reports the same way.
        result = JobResult.failed(job.id, job.kind, "stopped", OUT_OF_MEMORY)
    except Exception:  # noqa: BLE001 (a bug of ours: the loop logs it with its traceback)
        trace = traceback.format_exc()
        result = JobResult.failed(job.id, job.kind, "internal", INTERNAL)
    return Outcome(
        result=result.model_copy(update={"timings": Timings(**timings)}),
        began_at=began_at,
        trace=trace,
    )


def _run(request: Request, framework: Framework, timings: dict[str, float]) -> JobResult:
    job = request.job
    manifest = _manifest(job.manifest)
    path = _model_file(request.models_dir, job.model, job.sha256)
    with tempfile.TemporaryDirectory() as scratch, _timed(timings, "load"):
        model = _load(framework, path, Path(scratch))
    files = JobFiles(request.files_dir)
    if job.kind == "predict":
        candles = _candles(files, job.id)
        made = _input(candles, manifest, timings)
        with _timed(timings, "predict"):
            scores = _predict(model, made.windows, manifest)
        predictions = pl.DataFrame(
            {"ts": made.ts.cast(pl.Int64), "prediction": scores[:, 0].astype(np.float64)}
        )
        files.write_predictions(job.id, predictions)
        return JobResult(
            id=job.id,
            kind=job.kind,
            status="done",
            samples=predictions.height,
            dropped=made.dropped,
            runner=framework.runner,
        )
    made = _input(sample_candles(manifest), manifest, timings)
    with _timed(timings, "predict"):
        _predict(model, made.windows, manifest)
        difference = _example(model, manifest)
    return JobResult(
        id=job.id,
        kind=job.kind,
        status="done",
        samples=len(made.windows),
        dropped=made.dropped,
        verified=difference is not None,
        example_difference=difference,
        runner=framework.runner,
    )


@contextmanager
def _timed(timings: dict[str, float], step: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step] = round((time.perf_counter() - start) * 1000, 1)


def _manifest(document: dict[str, Any]) -> Manifest:
    try:
        manifest = parse_manifest(document)
    except InvalidManifest as error:
        raise JobFailed("manifest", f"The manifest is not valid. {error.problem.message}") from None
    if problems := check_manifest(manifest):
        raise JobFailed(
            "manifest", "The manifest cannot run. " + " ".join(p.message for p in problems)
        )
    return manifest


def _model_file(models_dir: Path, name: str, sha256: str) -> Path:
    """The model's file, checked to be inside the model storage and the one uploaded."""
    root = models_dir.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise JobFailed("model-missing", "The model's file is gone. Upload the model again.")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise JobFailed(
            "model-changed", "The model's file is not the one uploaded. Upload the model again."
        )
    return path


def _load(framework: Framework, path: Path, scratch: Path) -> Model:
    try:
        return framework.load(path, scratch)
    except MemoryError:
        raise
    except Exception as error:  # noqa: BLE001 (any error on a file the framework cannot load)
        raise JobFailed("load", framework.refused.format(error=type(error).__name__)) from None


def _candles(files: JobFiles, job_id: str) -> pl.DataFrame:
    try:
        return files.read_candles(job_id)
    except OSError:
        raise JobFailed("candles", "The job's candles are missing. Run it again.") from None


def _input(candles: pl.DataFrame, manifest: Manifest, timings: dict[str, float]) -> ModelInput:
    with _timed(timings, "input"):
        try:
            made = build_input(candles, manifest)
        except ValueError as error:
            raise JobFailed("candles", f"The model's input cannot be built. {error}.") from None
    if not len(made.windows):
        needed = warm_up(manifest).candles + 1
        raise JobFailed(
            "candles",
            f"The {candles.height} candles make no sample: the manifest needs at least "
            f"{needed}. Choose a longer period.",
        )
    return made


def _predict(model: Model, windows: Samples, manifest: Manifest) -> np.ndarray:
    """The model's output for every sample, checked to be one finite score per sample.

    The samples are built and predicted a part at a time (``part_sizes``), and the outputs
    joined in order: the same as predicting all of them at once in the same batches.
    """
    per_part, batch = part_sizes(windows.sample_bytes)
    output = np.concatenate(
        [
            _predict_part(model, windows.part(start, start + per_part), batch, manifest)
            for start in range(0, len(windows), per_part)
        ]
    )
    return _finite(output)


def part_sizes(sample_bytes: int) -> tuple[int, int]:
    """The samples of a part and of a batch of the model, for samples of ``sample_bytes``.

    A part takes at most ``PART_BYTES`` (one sample at least) and is a whole number of
    batches, so the model sees the same batches as on the whole input: a batch of ``BATCH``
    samples, fewer when such a batch alone would take more than a part.
    """
    per_part = max(PART_BYTES // sample_bytes, 1)
    batch = min(BATCH, per_part)
    return per_part // batch * batch, batch


def _predict_part(model: Model, samples: np.ndarray, batch: int, manifest: Manifest) -> np.ndarray:
    """The model's output for ``samples``, checked to be of the manifest's shape."""
    try:
        output = np.asarray(model.predict(samples, batch_size=batch, verbose=0))
    except MemoryError:
        raise
    except Exception as error:  # noqa: BLE001 (any error of the model on its input)
        raise JobFailed(
            "predict",
            f"The model failed on its input ({type(error).__name__}). Check that the manifest "
            "describes the input the model was trained on.",
        ) from None
    expected = [len(samples), *manifest.signature.output.shape]
    if list(output.shape) != expected:
        raise JobFailed(
            "output",
            f"The model gave an output of shape {list(output.shape[1:])} per sample, the "
            f"manifest says {manifest.signature.output.shape}.",
        )
    return output


def _finite(output: np.ndarray) -> np.ndarray:
    """``output``, checked to hold only finite numbers."""
    if bad := int(np.count_nonzero(~np.isfinite(output))):
        raise JobFailed(
            "output",
            f"The model gave {bad} of {output.size} values that are not finite numbers.",
        )
    return output


def _example(model: Model, manifest: Manifest) -> float | None:
    """The largest difference between the model's output for the manifest's example and the
    expected one; None without an example. Every failure here is an ``example`` one: the model
    failed on the example, gave a wrong output for it or not the expected one."""
    example = manifest.example
    if example is None:
        return None
    samples = np.asarray(example.input, dtype=np.float32)
    try:
        output = _finite(_predict_part(model, samples, BATCH, manifest))
    except JobFailed as failure:
        # The trial run passed, so a model that fails on the example fails the example.
        raise JobFailed(
            "example", f"For the manifest's example: {failure.message}", verified=False
        ) from None
    difference = float(np.max(np.abs(output - np.asarray(example.output, dtype=np.float64))))
    if difference > EXAMPLE_TOLERANCE:
        raise JobFailed(
            "example",
            f"For the manifest's example the model gives up to {difference:.3g} away from the "
            f"expected output, more than {EXAMPLE_TOLERANCE:g}. Check the order of the features "
            "and their normalization.",
            verified=False,
            example_difference=difference,
        )
    return difference


# Where Linux takes this process' standing when the container runs out of memory.
OOM_SCORE_ADJ = Path("/proc/self/oom_score_adj")


def limit_resources(resource: Any, oom_score_adj: Path) -> None:
    """Limits this process (Linux): no core dump (a crash must not write a model's memory to
    the disk), no file over ``FILE_MAX``, and it is the first the kernel stops when the
    container runs out of memory, not the loop."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_MAX, FILE_MAX))
    oom_score_adj.write_text("1000")


def main(stdin: IO[bytes], stdout: IO[str], *, limits: bool) -> int:
    """Reads the request from ``stdin`` and writes the outcome to ``stdout``."""
    # Only the job's own process is limited (`limits`), never one that imports this module to
    # call it, such as the tests.
    if limits and sys.platform == "linux":
        import resource  # noqa: PLC0415 (Linux only)

        limit_resources(resource, OOM_SCORE_ADJ)
    request = Request.model_validate_json(stdin.read())
    stdout.write(run(request).model_dump_json())
    stdout.flush()
    return 0


def _result_channel() -> IO[str]:
    """Standard output for the outcome only: what Keras, JAX, onnxruntime or a library print
    goes to standard error, where the container's log gets it."""
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    return channel


if __name__ == "__main__":
    sys.exit(main(sys.stdin.buffer, _result_channel(), limits=True))
