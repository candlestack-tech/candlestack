"""The worker's loop: a job's process that answers, takes too long, crashes or is killed, and
the loop that goes on through an empty queue, Redis out of reach, jobs nobody waits for and a
stop in the middle of a job."""

import json
import logging
import os
import runpy
import signal
import sys
import time
from pathlib import Path
from typing import Any

import anyio
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from candlestack.core import Settings
from candlestack.jobs import WORKER_STOPPED, Claimed, Job, JobFiles, JobResult
from candlestack.worker import service
from candlestack.worker.service import (
    EXPIRED,
    INTERNAL,
    OUTLIVED,
    STOPPED,
    JobProcess,
    Paths,
    Worker,
    child_environment,
)

pytestmark = pytest.mark.anyio

SHA256 = "ab" * 32


def job(kind: str = "check", deadline: float | None = None) -> Job:
    return Job.model_validate(
        {
            "kind": kind,
            "model": "u/m.keras",
            "sha256": SHA256,
            "manifest": {},
            "enqueued_at": time.time() - 2,
            "deadline": time.time() + 60 if deadline is None else deadline,
        }
    )


def process(tmp_path: Path, script: str, time_limit: float = 20) -> JobProcess:
    return JobProcess(
        Paths(tmp_path / "models", tmp_path / "jobs"), time_limit, [sys.executable, "-c", script]
    )


ANSWERS = """
import json, sys, time
job = json.load(sys.stdin)["job"]
result = {"id": job["id"], "kind": job["kind"], "status": "done", "samples": 3,
          "timings": {"load": 5.0}}
print(json.dumps({"result": result, "began_at": time.time(), "trace": TRACE}))
"""


async def test_the_jobs_process_gives_its_result(tmp_path: Path) -> None:
    submitted = job()

    result = await process(tmp_path, ANSWERS.replace("TRACE", "None")).run(submitted)

    assert (result.id, result.status, result.samples) == (submitted.id, "done", 3)
    assert result.timings.load == 5.0
    assert result.timings.start is not None
    assert result.timings.start >= 0


async def test_a_bug_in_the_jobs_process_is_logged_with_its_trace(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    submitted = job()
    script = ANSWERS.replace("TRACE", json.dumps("Traceback: RuntimeError"))

    with caplog.at_level(logging.ERROR):
        await process(tmp_path, script).run(submitted)

    (record,) = caplog.records
    assert record.getMessage() == f"Job {submitted.id} failed with an error of the worker"
    assert record.__dict__["trace"] == "Traceback: RuntimeError"


async def test_a_job_over_its_time_limit_is_stopped(tmp_path: Path) -> None:
    started = time.monotonic()

    result = await process(tmp_path, "import time; time.sleep(30)", time_limit=0.5).run(job())

    assert (result.status, result.reason) == ("failed", "time-limit")
    assert result.error == (
        "The model took longer than 0.5 seconds and was stopped. Choose a shorter period or a "
        "smaller model."
    )
    assert time.monotonic() - started < 10


async def test_a_job_stops_at_its_deadline_when_that_comes_first(tmp_path: Path) -> None:
    started = time.monotonic()
    late = job(deadline=time.time() + 0.5)

    result = await process(tmp_path, "import time; time.sleep(30)").run(late)

    assert (result.status, result.reason, result.error) == ("failed", "expired", OUTLIVED)
    assert time.monotonic() - started < 10


# Starts a child that outlives the job's process unless it is stopped with it, then answers.
LEAVES_A_CHILD = """
import json, subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open(sys.argv[1], "w").write(str(child.pid))
job = json.load(sys.stdin)["job"]
result = {"id": job["id"], "kind": job["kind"], "status": "done"}
print(json.dumps({"result": result, "began_at": time.time()}))
"""


@pytest.mark.skipif(sys.platform == "win32", reason="process groups")
@pytest.mark.parametrize("time_limit", [20, 0.5])
async def test_nothing_the_jobs_process_started_outlives_it(
    tmp_path: Path, time_limit: float
) -> None:
    pid_file = tmp_path / "child.pid"
    script = LEAVES_A_CHILD
    if time_limit < 1:
        script += "time.sleep(30)\n"
    command = [sys.executable, "-c", script, str(pid_file)]
    runner = JobProcess(Paths(tmp_path / "models", tmp_path / "jobs"), time_limit, command)

    await runner.run(job())

    child = int(pid_file.read_text())
    for _ in range(100):
        if not _alive(child):
            break
        await anyio.sleep(0.05)
    assert not _alive(child)


def _alive(pid: int) -> bool:
    """Whether ``pid`` still runs (a zombie, killed but not yet reaped, does not)."""
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    except OSError:  # no /proc (macOS)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    return state != "Z"


async def test_a_process_that_ends_without_a_result_is_a_bug(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    submitted = job()

    with caplog.at_level(logging.ERROR):
        result = await process(tmp_path, "import sys; sys.exit(3)").run(submitted)

    assert (result.reason, result.error) == ("internal", INTERNAL)
    assert f"The process of job {submitted.id} ended without a result (exit code 3)" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="signals")
async def test_a_killed_process_is_the_models_doing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    script = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"

    with caplog.at_level(logging.WARNING):
        result = await process(tmp_path, script).run(job())

    assert (result.reason, result.error) == ("stopped", STOPPED)
    assert "was killed (signal 9)" in caplog.text


async def test_an_outcome_too_large_is_no_outcome(tmp_path: Path) -> None:
    script = "import sys; sys.stdout.write('x' * 3 * 1024 * 1024); sys.stdout.flush()"

    result = await process(tmp_path, script).run(job())

    assert result.status == "failed"
    assert result.reason in {"stopped", "internal"}  # killed: a signal on Linux, 1 on Windows


def test_the_jobs_process_gets_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOBS_REDIS_URL", "redis://secret")
    monkeypatch.setenv("SENTRY_DSN", "https://secret")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.delenv("KERAS_BACKEND", raising=False)
    monkeypatch.setenv("KERAS_HOME", "/tmp/keras")

    environment = child_environment()

    assert "JOBS_REDIS_URL" not in environment
    assert "SENTRY_DSN" not in environment
    assert environment["PATH"] == "/usr/bin"
    assert environment["KERAS_BACKEND"] == "jax"
    assert environment["KERAS_HOME"] == "/tmp/keras"


class FakeQueue:
    """The queue as the worker sees it, in memory; ``down`` makes a method fail as Redis does."""

    def __init__(self, *claimed: Claimed) -> None:
        self.claimed = list(claimed)
        self.finished: list[JobResult] = []
        self.renewed: list[str] = []
        self.stalled: list[JobResult] = []
        self.sweeps = 0
        self.down: set[str] = set()
        self.published = True

    def _fail(self, method: str) -> None:
        if method in self.down:
            raise RedisConnectionError("Connection refused")

    async def claim(self, worker: str, lease: int) -> Claimed | None:
        self._fail("claim")
        assert (worker, lease) == ("worker-1", service.LEASE)
        return self.claimed.pop(0) if self.claimed else None

    async def renew(self, job_id: str, worker: str, lease: int) -> None:
        self._fail("renew")
        self.renewed.append(job_id)

    async def finish(self, result: JobResult) -> bool:
        self._fail("finish")
        self.finished.append(result)
        return self.published

    async def sweep(self) -> list[JobResult]:
        self._fail("sweep")
        self.sweeps += 1
        stalled, self.stalled = self.stalled, []
        return stalled


class FakeProcess:
    def __init__(self, delay: float = 0) -> None:
        self.delay = delay
        self.ran: list[Job] = []

    async def run(self, job: Job) -> JobResult:
        self.ran.append(job)
        await anyio.sleep(self.delay)
        return JobResult(id=job.id, kind=job.kind, status="done", samples=1)


def worker(
    tmp_path: Path, queue: FakeQueue, fake: FakeProcess | None = None
) -> tuple[Worker, FakeProcess]:
    fake = fake or FakeProcess()
    made = Worker(queue, JobFiles(tmp_path / "jobs"), fake, "worker-1")  # ty: ignore[invalid-argument-type]
    return made, fake


def claimed(submitted: Job) -> Claimed:
    return Claimed(submitted.id, submitted, submitted.kind)


@pytest.fixture(autouse=True)
def no_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "RETRY_AFTER", 0)
    monkeypatch.setattr(service, "POLL", 0.01)


async def test_a_job_runs_and_its_result_is_published(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    submitted = job()
    queue = FakeQueue(claimed(submitted))
    loop, fake = worker(tmp_path, queue)

    with caplog.at_level(logging.INFO):
        assert await loop.step()

    assert fake.ran == [submitted]
    (result,) = queue.finished
    assert (result.id, result.status) == (submitted.id, "done")
    assert result.timings.queued is not None
    assert result.timings.queued >= 2000
    assert result.timings.total is not None
    record = next(r for r in caplog.records if r.getMessage() == f"Job {submitted.id} done")
    assert (record.__dict__["kind"], record.__dict__["status"]) == ("check", "done")


async def test_an_empty_queue_is_no_job(tmp_path: Path) -> None:
    queue = FakeQueue()
    loop, _ = worker(tmp_path, queue)

    assert not await loop.step()
    assert queue.finished == []


async def test_a_job_past_its_deadline_does_not_run(tmp_path: Path) -> None:
    submitted = job(deadline=time.time() - 1)
    queue = FakeQueue(claimed(submitted))
    loop, fake = worker(tmp_path, queue)

    await loop.step()

    assert fake.ran == []
    assert [(r.reason, r.error) for r in queue.finished] == [("expired", EXPIRED)]


async def test_a_job_that_is_gone_or_unreadable_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    queue = FakeQueue(
        Claimed("a" * 32, None, "predict", gone=True), Claimed("b" * 32, None, "check")
    )
    loop, fake = worker(tmp_path, queue)

    with caplog.at_level(logging.ERROR):
        await loop.step()
        await loop.step()

    assert fake.ran == []
    assert [(r.id, r.kind, r.reason) for r in queue.finished] == [
        ("a" * 32, "predict", "expired"),
        ("b" * 32, "check", "internal"),
    ]
    assert queue.finished[0].timings.queued is None
    assert caplog.messages == [f"Job {'b' * 32} cannot be read"]


async def test_redis_out_of_reach_is_a_warning_and_a_retry(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    queue = FakeQueue(claimed(job()))
    queue.down = {"claim"}
    loop, fake = worker(tmp_path, queue)

    with caplog.at_level(logging.WARNING):
        assert not await loop.step()

    assert fake.ran == []
    assert "The job queue cannot be reached (ConnectionError)" in caplog.text
    queue.down = set()
    assert await loop.step()


@pytest.mark.parametrize(
    ("down", "published", "message"),
    [
        ({"finish"}, True, "was not published (ConnectionError)"),
        (set(), False, "was failed by a sweep before it finished"),
    ],
)
async def test_a_result_that_cannot_be_published_is_a_warning(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    down: set[str],
    published: bool,
    message: str,
) -> None:
    queue = FakeQueue(claimed(job()))
    queue.down, queue.published = down, published
    loop, _ = worker(tmp_path, queue)

    with caplog.at_level(logging.WARNING):
        assert await loop.step()

    assert message in caplog.text


async def test_the_lease_is_renewed_while_the_job_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(service, "RENEW_EVERY", 0.01)
    submitted = job()
    queue = FakeQueue(claimed(submitted), claimed(job()))
    loop, _ = worker(tmp_path, queue, FakeProcess(delay=0.2))

    await loop.step()
    assert submitted.id in queue.renewed

    queue.down = {"renew"}
    with caplog.at_level(logging.WARNING):
        await loop.step()
    assert "was not renewed (ConnectionError)" in caplog.text
    assert [r.status for r in queue.finished] == ["done", "done"]


async def test_a_stop_during_a_job_fails_it_as_worker_stopped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    submitted = job()
    queue = FakeQueue(claimed(submitted))
    loop, _ = worker(tmp_path, queue, FakeProcess(delay=30))

    with caplog.at_level(logging.WARNING), anyio.move_on_after(0.3):
        await loop.step()

    assert [(r.id, r.reason, r.error) for r in queue.finished] == [
        (submitted.id, "worker-stopped", WORKER_STOPPED)
    ]
    assert caplog.messages == [f"Job {submitted.id} failed: the worker stopped while it ran"]


async def test_sweeps_run_when_they_are_due(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    files = JobFiles(tmp_path / "jobs")
    old = "c" * 32
    files.directory(old).mkdir(parents=True)
    os.utime(files.directory(old), (time.time() - 7200, time.time() - 7200))
    queue = FakeQueue()
    queue.stalled = [JobResult.failed("d" * 32, "check", "worker-stopped", WORKER_STOPPED)]
    loop, _ = worker(tmp_path, queue)

    with caplog.at_level(logging.INFO):
        await loop.step()
        await loop.step()

    assert queue.sweeps == 1
    assert f"Job {'d' * 32} was failed: its worker stopped while it ran" in caplog.text
    assert "Removed the files of 1 jobs nobody waits for" in caplog.text
    assert not files.directory(old).exists()


async def test_a_sweep_of_files_that_fails_is_a_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(*_: object) -> int:
        raise PermissionError("jobs")

    monkeypatch.setattr(JobFiles, "sweep", refuse)
    loop, _ = worker(tmp_path, FakeQueue())

    with caplog.at_level(logging.WARNING):
        await loop.step()

    assert "The jobs' files were not swept (PermissionError)" in caplog.text


async def test_the_loop_runs_jobs_until_it_is_stopped(tmp_path: Path) -> None:
    queue = FakeQueue(claimed(job()), claimed(job()))
    loop, fake = worker(tmp_path, queue)

    with anyio.move_on_after(0.3):
        await loop.serve()

    assert len(fake.ran) == 2


@pytest.mark.skipif(sys.platform == "win32", reason="signals")
async def test_sigterm_stops_the_loop(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO), anyio.fail_after(5):
        async with anyio.create_task_group() as group:
            group.start_soon(service._stop_on_signal, group.cancel_scope)
            await anyio.sleep(0.1)
            os.kill(os.getpid(), signal.SIGTERM)
            await anyio.sleep(10)

    assert "The worker stops (SIGTERM)" in caplog.text


def test_main_sets_up_logging_and_sentry_and_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    settings = Settings(app_env="test", log_level="WARNING")
    monkeypatch.setattr(service, "get_settings", lambda: settings)
    monkeypatch.setattr(service, "setup_logging", lambda level: calls.append(("logging", level)))
    monkeypatch.setattr(service, "setup_sentry", lambda got: calls.append(("sentry", got)))
    monkeypatch.setattr(service.anyio, "run", lambda function, got: calls.append((function, got)))

    runpy.run_module("candlestack.worker", run_name="__main__")

    assert calls == [("logging", "WARNING"), ("sentry", settings), (service.serve, settings)]
