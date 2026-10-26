"""Fixtures of the models integration tests: the app on a real Postgres (emptied before each
test) with its model storage in a temporary directory (tests/integration/conftest.py), and
signed-in users with the models API at hand. The checks of the models go to the real job queue
(Redis at REDIS_URL, emptied first), which nobody answers unless a test takes the ``worker``
(``FakeWorker``: it answers the queue as the worker does, without Keras).

Test modules cannot import this file, so the helpers they need hang on the ``ModelsUser`` the
``ada`` and ``bob`` fixtures return, and on ``Storage``.
"""

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import anyio
import httpx2
import pytest
import redis
from pydantic import SecretStr

from candlestack.core import Settings, create_redis
from candlestack.jobs import Job, JobQueue, JobResult


@pytest.fixture(autouse=True)
def _empty_tables(empty_tables: None) -> None:
    """Every test starts without users."""


@pytest.fixture
def settings(settings: Settings, redis_url: str, tmp_path: Path) -> Settings:
    """The app's job queue is the test Redis, emptied first."""
    with redis.Redis.from_url(redis_url) as queue:
        queue.flushdb()
    return settings.model_copy(
        update={"jobs_redis_url": SecretStr(redis_url), "job_files_dir": tmp_path / "jobs"}
    )


def passed(job: Job) -> JobResult:
    """The result of a check whose model gave the manifest's example."""
    return JobResult(id=job.id, kind=job.kind, status="done", samples=64, verified=True)


class FakeWorker:
    """Answers the jobs of the queue as the worker would, without a model: each with what
    ``answer`` makes of it (``passed`` by default). ``jobs`` are the jobs it took; with
    ``hold`` set it takes none until the event is set."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self.answer: Callable[[Job], JobResult] = passed
        self.jobs: list[Job] = []
        self.hold: threading.Event | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=anyio.run, args=(self._serve,), daemon=True)

    def __enter__(self) -> "FakeWorker":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=10)

    async def _serve(self) -> None:
        client = create_redis(self._redis_url)
        queue = JobQueue(client)
        try:
            while not self._stop.is_set():
                held = self.hold is not None and not self.hold.is_set()
                claimed = None if held else await queue.claim("fake-worker", 30)
                if claimed is None or claimed.job is None:
                    await anyio.sleep(0.02)
                    continue
                self.jobs.append(claimed.job)
                await queue.finish(self.answer(claimed.job))
        finally:
            await client.aclose()


@pytest.fixture
def worker(settings: Settings) -> Iterator[FakeWorker]:
    with FakeWorker(settings.jobs_redis_url.get_secret_value()) as fake:
        yield fake


class Storage:
    """The model storage's directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def file(self, user_id: str, model_id: str, suffix: str = ".keras") -> Path:
        return self.root / user_id / f"{model_id}{suffix}"

    def incoming(self) -> list[Path]:
        """What uploads left in the incoming directory."""
        return list((self.root / ".incoming").iterdir())

    def files(self) -> list[Path]:
        """Every model file, of every user."""
        return sorted(path for path in self.root.glob("usr_*/*") if path.is_file())


@pytest.fixture
def storage(settings: Settings) -> Storage:
    return Storage(settings.model_storage_dir)


class ModelsUser:
    """A signed-in user (the shared ``Account``: ``id``, ``request``) and the models API."""

    def __init__(self, account: Any) -> None:
        self._account = account

    def __getattr__(self, name: str) -> Any:
        return getattr(self._account, name)

    def upload(self, content: bytes | Any, name: str = "functional.keras") -> httpx2.Response:
        return self.request("POST", "/api/v1/models", params={"name": name}, content=content)

    def models(self) -> httpx2.Response:
        return self.request("GET", "/api/v1/models")

    def update(self, model_id: str, **body: Any) -> httpx2.Response:
        return self.request("PATCH", f"/api/v1/models/{model_id}", json=body)

    def delete(self, model_id: str) -> httpx2.Response:
        return self.request("DELETE", f"/api/v1/models/{model_id}")

    def set_manifest(self, model_id: str, content: bytes | Any) -> httpx2.Response:
        return self.request("PUT", f"/api/v1/models/{model_id}/manifest", content=content)

    def manifest(self, model_id: str) -> httpx2.Response:
        return self.request("GET", f"/api/v1/models/{model_id}/manifest")


@pytest.fixture
def ada(account: Callable[[str], Any]) -> ModelsUser:
    return ModelsUser(account("usr_adaaaaaaaa"))


@pytest.fixture
def bob(account: Callable[[str], Any]) -> ModelsUser:
    return ModelsUser(account("usr_bobbbbbbbb"))
