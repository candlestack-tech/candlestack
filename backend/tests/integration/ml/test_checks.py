"""The worker's check of a model with its manifest, over HTTP against a real Postgres and the
real job queue (Redis), with the worker faked (conftest.py): what a check's result makes of the
model, a manifest replaced while its check ran, no worker, and the checks at startup."""

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import redis
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candlestack.core import Settings, create_redis
from candlestack.jobs import Job, JobClient, JobFiles, JobQueue, JobResult

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
RAW_CANDLES = (FIXTURES / "raw_candles.keras").read_bytes()
EXAMPLE_ERROR = (
    "For the manifest's example the model gives up to 0.5 away from the expected output."
)


def raw_manifest(note: str = "First") -> dict[str, Any]:
    manifest = json.loads((FIXTURES / "raw_candles.manifest.json").read_text(encoding="utf-8"))
    manifest["about"] = {"note": note}
    return manifest


def until(condition: Callable[[], bool], timeout: float = 10) -> None:
    """Waits for `condition`, which a check in a task of the app makes true."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def with_manifest(user: Any, note: str = "First") -> dict[str, Any]:
    model = user.upload(RAW_CANDLES, name="raw.keras").json()
    response = user.set_manifest(model["id"], json.dumps(raw_manifest(note)))
    assert response.status_code == 200
    return response.json()


def passed(job: Job) -> JobResult:
    return JobResult(id=job.id, kind=job.kind, status="done", samples=64, verified=True)


def failed_on_the_example(job: Job) -> JobResult:
    return JobResult.failed(job.id, job.kind, "example", EXAMPLE_ERROR)


def only(user: Any) -> dict[str, Any]:
    (model,) = user.models().json()["models"]
    return model


def checked(sql: Callable[..., list[Any]]) -> bool:
    return sql("SELECT checked_at IS NOT NULL FROM models") == [(True,)]


def queued(settings: Settings) -> int:
    """The jobs waiting in the queue."""
    with redis.Redis.from_url(settings.jobs_redis_url.get_secret_value()) as queue:
        return queue.llen("jobs:queue")


def test_the_check_of_an_onnx_model_names_its_onnx_file(ada, worker) -> None:
    onnx_file = Path(__file__).parents[2] / "fixtures" / "onnx" / "raw_candles.onnx"
    model = ada.upload(onnx_file.read_bytes(), name="raw.onnx").json()

    assert ada.set_manifest(model["id"], json.dumps(raw_manifest())).status_code == 200
    until(lambda: only(ada)["state"] == "verified")

    (job,) = worker.jobs
    assert job.model == f"{ada.id}/{model['id']}.onnx"
    assert job.sha256 == hashlib.sha256(onnx_file.read_bytes()).hexdigest()


def test_a_check_that_gives_the_example_verifies_the_model(ada, sql, worker) -> None:
    model = with_manifest(ada)

    until(lambda: only(ada)["state"] == "verified")

    assert model["state"] == "ready"
    assert only(ada)["check_error"] is None
    (job,) = worker.jobs
    stored = sql("SELECT sha256, manifest FROM models")[0]
    assert (job.kind, job.model, job.sha256) == (
        "check",
        f"{ada.id}/{model['id']}.keras",
        hashlib.sha256(RAW_CANDLES).hexdigest(),
    )
    assert job.manifest == stored.manifest
    assert sql("SELECT verified_at = checked_at FROM models") == [(True,)]


def test_a_failed_check_leaves_the_model_ready_with_its_error(ada, sql, worker) -> None:
    worker.answer = failed_on_the_example
    with_manifest(ada)

    until(lambda: checked(sql))

    assert only(ada)["state"] == "ready"
    assert only(ada)["check_error"] == EXAMPLE_ERROR
    assert sql("SELECT verified_at FROM models") == [(None,)]


def test_a_check_without_an_example_leaves_the_model_ready(ada, sql, worker) -> None:
    worker.answer = lambda job: JobResult(
        id=job.id, kind=job.kind, status="done", samples=64, verified=False
    )
    with_manifest(ada)

    until(lambda: checked(sql))

    assert (only(ada)["state"], only(ada)["check_error"]) == ("ready", None)


def test_a_new_manifest_clears_the_check_of_the_old_one(ada, sql, worker) -> None:
    worker.answer = failed_on_the_example
    model = with_manifest(ada)
    until(lambda: checked(sql))
    worker.hold = threading.Event()

    response = ada.set_manifest(model["id"], json.dumps(raw_manifest("Second")))

    assert (response.json()["state"], response.json()["check_error"]) == ("ready", None)
    assert sql("SELECT checked_at, verified_at FROM models") == [(None, None)]


def test_a_manifest_replaced_while_its_check_ran_keeps_the_model_ready(
    ada, sql, settings, worker, caplog
) -> None:
    worker.hold = threading.Event()
    model = with_manifest(ada, "First")
    until(lambda: queued(settings) == 1)
    ada.set_manifest(model["id"], json.dumps(raw_manifest("Second")))
    # The second check waits for the first: one job of the user's in the queue.
    assert queued(settings) == 1
    # The check of the first manifest would verify the model, the second's fails.
    worker.answer = lambda job: (
        passed(job) if job.manifest["about"]["note"] == "First" else failed_on_the_example(job)
    )

    with caplog.at_level(logging.INFO):
        worker.hold.set()
        until(lambda: checked(sql) and "no longer has" in caplog.text)

    assert [job.manifest["about"]["note"] for job in worker.jobs] == ["First", "Second"]
    assert (only(ada)["state"], only(ada)["check_error"]) == ("ready", EXAMPLE_ERROR)
    assert f"The check of model {model['id']} ran with a manifest it no longer has" in caplog.text


def test_the_check_of_a_deleted_model_keeps_nothing(ada, sql, settings, worker, caplog) -> None:
    worker.hold = threading.Event()
    model = with_manifest(ada)
    until(lambda: queued(settings) == 1)
    ada.delete(model["id"])

    with caplog.at_level(logging.INFO):
        worker.hold.set()
        until(lambda: "no longer has" in caplog.text)

    assert sql("SELECT count(*) FROM models") == [(0,)]


@pytest.mark.parametrize("reason", ["worker-stopped", "internal", "model-missing"])
def test_a_check_that_failed_on_the_worker_leaves_the_model_unchecked(
    ada, sql, worker, caplog, reason
) -> None:
    worker.answer = lambda job: JobResult.failed(job.id, job.kind, reason, "It stopped.")

    with caplog.at_level(logging.WARNING):
        model = with_manifest(ada)
        until(lambda: f"came to no end ({reason})" in caplog.text)

    assert (only(ada)["state"], only(ada)["check_error"]) == ("ready", None)
    assert sql("SELECT checked_at FROM models") == [(None,)]
    assert f"The check of model {model['id']} came to no end" in caplog.text


def test_without_a_worker_the_model_stays_ready(app: FastAPI, ada, sql, caplog) -> None:
    app.state.model_checks._wait = 0.2

    with caplog.at_level(logging.WARNING):
        model = with_manifest(ada)
        until(lambda: "came to no end (expired)" in caplog.text)

    assert (only(ada)["state"], only(ada)["check_error"]) == ("ready", None)
    assert sql("SELECT checked_at FROM models") == [(None,)]
    assert f"The check of model {model['id']} came to no end (expired)" in caplog.text


def test_without_a_queue_the_model_stays_ready(
    app: FastAPI, ada, sql, caplog, tmp_path: Path
) -> None:
    # Prod for now: the backend's queue is a host that is not there.
    app.state.model_checks._jobs = JobClient(
        JobQueue(create_redis("redis://127.0.0.1:1/0")), JobFiles(tmp_path)
    )

    with caplog.at_level(logging.WARNING):
        started = time.monotonic()
        model = with_manifest(ada)
        until(lambda: "could not reach the worker" in caplog.text)

    assert time.monotonic() - started < 10
    assert (only(ada)["state"], only(ada)["check_error"]) == ("ready", None)
    assert f"The check of model {model['id']} could not reach the worker" in caplog.text


def recover(app: FastAPI, client: TestClient) -> None:
    """What the app does at startup: checks the models left unchecked."""

    async def start() -> None:
        app.state.model_checks.recover()

    assert client.portal is not None
    client.portal.call(start)


def test_the_models_left_unchecked_are_checked_at_startup(
    app: FastAPI, client: TestClient, ada, bob, sql, worker
) -> None:
    first, second = with_manifest(ada), with_manifest(bob)
    until(lambda: len(worker.jobs) == 2)
    until(lambda: sql("SELECT count(*) FROM models WHERE verified_at IS NOT NULL") == [(2,)])
    # As if the process had stopped while the checks ran.
    sql("UPDATE models SET checked_at = NULL, verified_at = NULL")

    recover(app, client)

    until(lambda: len(worker.jobs) == 4)
    assert [job.model for job in worker.jobs[2:]] == [
        f"{ada.id}/{first['id']}.keras",
        f"{bob.id}/{second['id']}.keras",
    ]
    until(lambda: sql("SELECT count(*) FROM models WHERE verified_at IS NOT NULL") == [(2,)])


def test_the_checks_at_startup_stop_at_the_first_without_a_worker(
    app: FastAPI, client: TestClient, ada, bob, sql, caplog
) -> None:
    app.state.model_checks._wait = 0.2
    with caplog.at_level(logging.WARNING):
        with_manifest(ada)
        with_manifest(bob)
        until(lambda: caplog.text.count("came to no end (expired)") == 2)
        caplog.clear()

        recover(app, client)
        until(lambda: "wait for the next start" in caplog.text)

    assert caplog.text.count("came to no end (expired)") == 1
    assert sql("SELECT count(*) FROM models WHERE checked_at IS NULL") == [(2,)]


def test_a_model_without_a_manifest_is_not_checked(app: FastAPI, client: TestClient, ada, worker):
    model = ada.upload(RAW_CANDLES, name="raw.keras").json()

    async def check() -> bool:
        return await app.state.model_checks._check(ada.id, model["id"])

    assert client.portal is not None
    assert client.portal.call(check) is True
    assert worker.jobs == []
