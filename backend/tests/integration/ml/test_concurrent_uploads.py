"""Uploads on the service with a real Postgres and Redis: two of one user at the same time (the
upload lock in Redis turns the second away; the lock of the user's models in Postgres alone
still keeps them from both passing the quota or taking one name), a file that arrives too
slowly, and an account deleted while its file uploads."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text

from candlestack.core import Database, create_redis
from candlestack.ml import LocalModelStorage
from candlestack.ml import service as service_module
from candlestack.ml.errors import AccountGone, StorageFull, UploadInProgress, UploadTimeout
from candlestack.ml.schemas import QUOTA_BYTES, Framework, ModelOut
from candlestack.ml.service import ModelsAccountDeletion, ModelsService
from candlestack.ml.store import ModelStore
from candlestack.ml.uploads import RedisUploadLocks, UploadLocks

pytestmark = [pytest.mark.integration, pytest.mark.anyio]

USER = "usr_adaaaaaaaa"
CONTENT = (Path(__file__).parents[2] / "fixtures" / "keras" / "functional.keras").read_bytes()


class NoUploadLocks:
    """Lets every upload run, so the lock of the user's models in Postgres is all there is."""

    @asynccontextmanager
    async def hold(self, user_id: str) -> AsyncIterator[None]:
        yield


@pytest.fixture
async def database(database_url: str) -> AsyncIterator[Database]:
    database = Database(database_url)
    now = datetime.now(UTC)
    async with database.sessions() as session:
        await session.execute(
            text("INSERT INTO users VALUES (:id, 'Ada', 'ada@example.com', :now, :now)"),
            {"id": USER, "now": now},
        )
        await session.commit()
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
async def storage(tmp_path: Path) -> LocalModelStorage:
    storage = LocalModelStorage(tmp_path / "models")
    await storage.prepare()
    return storage


@pytest.fixture
async def redis_locks(redis_url: str) -> AsyncIterator[RedisUploadLocks]:
    redis = create_redis(redis_url)
    await redis.delete(f"ml:upload:{USER}")
    try:
        yield RedisUploadLocks(redis)
    finally:
        await redis.aclose()


async def fill(database: Database, size: int) -> None:
    async with database.sessions() as session:
        await session.execute(
            text(
                "INSERT INTO models (id, user_id, name, framework, size_bytes, sha256, "
                "parameters, keras_version, created_at) VALUES ('mdl_bigbigbigb', :user, "
                "'big.keras', 'keras', :size, :sha, 1, '3.15.1', now())"
            ),
            {"user": USER, "size": size, "sha": b"\0" * 32},
        )
        await session.commit()


async def upload_together(
    database: Database, storage: LocalModelStorage, locks: UploadLocks
) -> Sequence[ModelOut | BaseException]:
    """Two uploads whose files come in only once both passed the first look at the quota (or
    one was turned away)."""
    arrived = 0
    both = asyncio.Event()

    def one_more() -> None:
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()

    async def body() -> AsyncIterator[bytes]:
        one_more()
        await both.wait()
        yield CONTENT

    async def upload() -> ModelOut:
        async with database.sessions() as session:
            service = ModelsService(ModelStore(session), storage, locks)
            try:
                return await service.upload(USER, "lstm.keras", len(CONTENT), body())
            except UploadInProgress:
                one_more()
                raise

    return await asyncio.gather(upload(), upload(), return_exceptions=True)


def incoming(storage: LocalModelStorage) -> list[Path]:
    return list((storage.path(USER, "x", Framework.keras).parents[1] / ".incoming").iterdir())


async def test_only_one_of_two_uploads_fits(database: Database, storage: LocalModelStorage):
    # Room for one file, not two.
    await fill(database, QUOTA_BYTES - len(CONTENT) * 3 // 2)

    results = await upload_together(database, storage, NoUploadLocks())

    kept = [result for result in results if isinstance(result, ModelOut)]
    refused = [result for result in results if isinstance(result, StorageFull)]
    assert len(kept) == 1
    assert len(refused) == 1
    assert refused[0].used_bytes == QUOTA_BYTES - len(CONTENT) * 3 // 2 + len(CONTENT)
    assert [
        path.name for path in (storage.path(USER, kept[0].id, Framework.keras).parent).iterdir()
    ] == [f"{kept[0].id}.keras"]
    assert incoming(storage) == []


async def test_two_uploads_of_one_name_get_two_names(
    database: Database, storage: LocalModelStorage
) -> None:
    results = await upload_together(database, storage, NoUploadLocks())

    assert sorted(result.name for result in results if isinstance(result, ModelOut)) == [
        "lstm (2).keras",
        "lstm.keras",
    ]


async def test_the_upload_lock_turns_a_second_upload_away(
    database: Database, storage: LocalModelStorage, redis_locks: RedisUploadLocks
) -> None:
    results = await upload_together(database, storage, redis_locks)

    assert sorted(type(result).__name__ for result in results) == ["ModelOut", "UploadInProgress"]
    # The lock went with the upload that held it.
    async with redis_locks.hold(USER):
        pass


async def test_a_file_that_arrives_too_slowly(
    database: Database,
    storage: LocalModelStorage,
    redis_locks: RedisUploadLocks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_module, "RECEIVE_TIMEOUT", 0.2)

    async def slow() -> AsyncIterator[bytes]:
        yield CONTENT[:1000]
        await asyncio.sleep(5)
        yield CONTENT[1000:]

    async with database.sessions() as session:
        service = ModelsService(ModelStore(session), storage, redis_locks)
        with pytest.raises(UploadTimeout):
            await service.upload(USER, "lstm.keras", None, slow())

    assert incoming(storage) == []


async def test_an_account_deleted_while_its_file_uploads(
    database: Database, storage: LocalModelStorage, redis_locks: RedisUploadLocks
) -> None:
    async def body_then_deletion() -> AsyncIterator[bytes]:
        yield CONTENT
        async with database.sessions() as other:
            await ModelsAccountDeletion(storage)(USER, other)
            await other.execute(text("DELETE FROM users WHERE id = :id"), {"id": USER})
            await other.commit()

    async with database.sessions() as session:
        service = ModelsService(ModelStore(session), storage, redis_locks)
        with pytest.raises(AccountGone):
            await service.upload(USER, "lstm.keras", len(CONTENT), body_then_deletion())

    assert incoming(storage) == []
    assert not storage.path(USER, "x", Framework.keras).parent.exists()
