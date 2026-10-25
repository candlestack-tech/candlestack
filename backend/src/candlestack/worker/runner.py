"""The process of one job, started by the worker's loop (``service.py``) for every job: reads
the request as JSON on stdin, runs the job and writes its outcome as JSON on stdout.

A fresh process per job: a model that crashes, hangs or exhausts the memory takes down this
process only, the loop fails the job and goes on, and no state of one model reaches the next.
It is the only process that imports Keras. It gets no keys and no Redis URL from the loop, and
the container it runs in has no network.
"""

import hashlib
import os
import sys
import tempfile
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any

import jax
import keras
import numpy as np
import polars as pl

from candlestack.jobs import EXAMPLE_TOLERANCE, JobFiles, JobResult, Reason, Runner, Timings
from candlestack.preprocessing import (
    InvalidManifest,
    Manifest,
    ModelInput,
    build_input,
    check_manifest,
    parse_manifest,
    warm_up,
)
from candlestack.worker.model import load_keras
from candlestack.worker.protocol import Outcome, Request
from candlestack.worker.samples import sample_candles

# Samples per batch of a prediction: large batches are faster, and 1024 samples of 25 x 23
# floats are 2.4 MB.
BATCH = 1024
# The largest file the process may write (Linux): the model's copy without its compile config
# (a file of at most 50 MB, unpacked to 100 MB of weights at most) and the predictions.
FILE_MAX = 512 * 1024 * 1024
_SAVE_AGAIN = 'Save the model again with model.save("model.keras") and upload that file.'
INTERNAL = "The worker failed on this job. This is a bug of CandleStack and it was reported."


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
    try:
        result = _run(request, timings)
    except JobFailed as failed:
        result = JobResult.failed(job.id, job.kind, failed.reason, failed.message).model_copy(
            update={"runner": _runner(), **failed.found}
        )
    except Exception:  # noqa: BLE001 (a bug of ours: the loop logs it with its traceback)
        trace = traceback.format_exc()
        result = JobResult.failed(job.id, job.kind, "internal", INTERNAL)
    return Outcome(
        result=result.model_copy(update={"timings": Timings(**timings)}),
        began_at=began_at,
        trace=trace,
    )


def _run(request: Request, timings: dict[str, float]) -> JobResult:
    job = request.job
    manifest = _manifest(job.manifest)
    path = _model_file(request.models_dir, job.model, job.sha256)
    with tempfile.TemporaryDirectory() as scratch, _timed(timings, "load"):
        model = _load(path, Path(scratch))
    files = JobFiles(request.files_dir)
    if job.kind == "predict":
        candles = _candles(files, job.id)
        made = _input(candles, manifest, timings)
        with _timed(timings, "predict"):
            scores = _predict(model, made.samples, manifest)
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
            runner=_runner(),
        )
    made = _input(sample_candles(manifest), manifest, timings)
    with _timed(timings, "predict"):
        _predict(model, made.samples, manifest)
        difference = _example(model, manifest)
    return JobResult(
        id=job.id,
        kind=job.kind,
        status="done",
        samples=len(made.samples),
        dropped=made.dropped,
        verified=difference is not None,
        example_difference=difference,
        runner=_runner(),
    )


@contextmanager
def _timed(timings: dict[str, float], step: str) -> Iterator[None]:
    start = time.perf_counter()
    try:
        yield
    finally:
        timings[step] = round((time.perf_counter() - start) * 1000, 1)


def _runner() -> Runner:
    return Runner(
        keras=keras.__version__, backend=keras.backend.backend(), backend_version=jax.__version__
    )


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


def _load(path: Path, scratch: Path) -> keras.Model:
    try:
        return load_keras(path, scratch)
    except Exception as error:  # noqa: BLE001 (Keras raises any error on a file it cannot load)
        raise JobFailed(
            "load", f"Keras could not load the model ({type(error).__name__}). " + _SAVE_AGAIN
        ) from None


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
    if not len(made.samples):
        needed = warm_up(manifest).candles + 1
        raise JobFailed(
            "candles",
            f"The {candles.height} candles make no sample: the manifest needs at least "
            f"{needed}. Choose a longer period.",
        )
    return made


def _predict(model: keras.Model, samples: np.ndarray, manifest: Manifest) -> np.ndarray:
    """The model's output for ``samples``, checked to be one finite score per sample."""
    try:
        output = np.asarray(model.predict(samples, batch_size=BATCH, verbose=0))
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
    if bad := int(np.count_nonzero(~np.isfinite(output))):
        raise JobFailed(
            "output",
            f"The model gave {bad} of {output.size} values that are not finite numbers.",
        )
    return output


def _example(model: keras.Model, manifest: Manifest) -> float | None:
    """The largest difference between the model's output for the manifest's example and the
    expected one; None without an example."""
    example = manifest.example
    if example is None:
        return None
    output = _predict(model, np.asarray(example.input, dtype=np.float32), manifest)
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
    """Standard output for the outcome only: what Keras, JAX or a library print goes to
    standard error, where the container's log gets it."""
    channel = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    return channel


if __name__ == "__main__":
    sys.exit(main(sys.stdin.buffer, _result_channel(), limits=True))
