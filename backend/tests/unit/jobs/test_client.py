"""The backend's side of a job: the candles go to the volume, the job to the queue, and the
files go once the result is in, whatever it is."""

from pathlib import Path

import polars as pl
import pytest

from candlestack.jobs import NO_ANSWER, Job, JobClient, JobFiles, JobResult

pytestmark = pytest.mark.anyio

SHA256 = "ab" * 32
MANIFEST = {"version": 1}
CANDLES = pl.DataFrame({"ts": [0], "open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]})
PREDICTIONS = pl.DataFrame({"ts": [0], "prediction": [0.5]})


class FakeQueue:
    """Answers each job as ``answer`` says, as the worker would."""

    def __init__(self, files: JobFiles, answer: str) -> None:
        self.files = files
        self.answer = answer
        self.submitted: list[Job] = []
        self.waits: list[float] = []

    async def submit(self, job: Job) -> None:
        self.submitted.append(job)

    async def result(self, job_id: str, wait: float) -> JobResult | None:
        self.waits.append(wait)
        job = self.submitted[-1]
        if self.answer == "none":
            return None
        if self.answer == "failed":
            return JobResult.failed(job_id, job.kind, "load", "Keras could not load it.")
        if job.kind == "predict":
            assert self.files.read_candles(job_id).equals(CANDLES)
            self.files.write_predictions(job_id, PREDICTIONS)
        return JobResult(id=job_id, kind=job.kind, status="done", samples=1)


def leftovers(path: Path) -> list[Path]:
    return list(path.iterdir())


def client(tmp_path: Path, answer: str) -> tuple[JobClient, FakeQueue, JobFiles]:
    files = JobFiles(tmp_path)
    queue = FakeQueue(files, answer)
    return JobClient(queue, files), queue, files  # ty: ignore[invalid-argument-type]


async def test_predict_returns_the_predictions_and_removes_the_files(tmp_path: Path) -> None:
    jobs, queue, _ = client(tmp_path, "done")

    result, predictions = await jobs.predict(
        model="u/m.keras", sha256=SHA256, manifest=MANIFEST, candles=CANDLES, wait=30
    )

    assert result.status == "done"
    assert predictions is not None
    assert predictions.equals(PREDICTIONS)
    (job,) = queue.submitted
    assert (job.kind, job.model, job.sha256, job.manifest) == (
        "predict",
        "u/m.keras",
        SHA256,
        MANIFEST,
    )
    assert queue.waits == [30]
    assert leftovers(tmp_path) == []


async def test_a_failed_predict_has_no_predictions_and_removes_the_files(tmp_path: Path) -> None:
    jobs, _, _ = client(tmp_path, "failed")

    result, predictions = await jobs.predict(
        model="u/m.keras", sha256=SHA256, manifest=MANIFEST, candles=CANDLES
    )

    assert (result.status, result.reason) == ("failed", "load")
    assert predictions is None
    assert leftovers(tmp_path) == []


async def test_no_answer_in_time_is_a_failed_result(tmp_path: Path) -> None:
    jobs, _, _ = client(tmp_path, "none")

    result = await jobs.check(model="u/m.keras", sha256=SHA256, manifest=MANIFEST, wait=0.1)

    assert (result.kind, result.status, result.reason, result.error) == (
        "check",
        "failed",
        "expired",
        NO_ANSWER,
    )


async def test_check_writes_no_files(tmp_path: Path) -> None:
    jobs, queue, _ = client(tmp_path, "done")

    result = await jobs.check(model="u/m.keras", sha256=SHA256, manifest=MANIFEST)

    assert result.status == "done"
    assert queue.submitted[0].kind == "check"
    assert leftovers(tmp_path) == []
