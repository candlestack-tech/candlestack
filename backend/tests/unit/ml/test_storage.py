"""The model files in a directory."""

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import cast

import pytest

from candlestack.ml import storage as storage_module
from candlestack.ml.schemas import Framework
from candlestack.ml.storage import (
    LocalModelStorage,
    ModelStorage,
    keep_incoming_swept,
    model_file,
)

pytestmark = pytest.mark.anyio

USER = "usr_7f3k2m9qx4"


@pytest.fixture
async def storage(tmp_path: Path) -> LocalModelStorage:
    storage = LocalModelStorage(tmp_path / "models")
    await storage.prepare()
    return storage


async def incoming(storage: LocalModelStorage, content: bytes = b"model") -> Path:
    path = storage.incoming_path()
    path.write_bytes(content)
    return path


async def test_prepare_creates_the_directories(tmp_path: Path) -> None:
    await LocalModelStorage(tmp_path / "var" / "models").prepare()

    assert (tmp_path / "var" / "models" / ".incoming").is_dir()


async def test_incoming_files_are_new_part_files(storage: LocalModelStorage, tmp_path: Path):
    first, second = storage.incoming_path(), storage.incoming_path()

    assert first != second
    assert first.parent == second.parent == tmp_path / "models" / ".incoming"
    assert first.suffix == ".part"


async def test_save_moves_the_file_to_the_users_directory(
    storage: LocalModelStorage, tmp_path: Path
) -> None:
    path = await incoming(storage)

    await storage.save(path, USER, "mdl_aaaaaaaaaa", Framework.keras)

    assert not path.exists()
    assert (tmp_path / "models" / USER / "mdl_aaaaaaaaaa.keras").read_bytes() == b"model"
    assert storage.path(USER, "mdl_aaaaaaaaaa", Framework.keras) == (
        tmp_path / "models" / USER / "mdl_aaaaaaaaaa.keras"
    )


async def test_an_onnx_model_is_kept_as_an_onnx_file(
    storage: LocalModelStorage, tmp_path: Path
) -> None:
    await storage.save(await incoming(storage), USER, "mdl_aaaaaaaaaa", Framework.onnx)

    assert (tmp_path / "models" / USER / "mdl_aaaaaaaaaa.onnx").read_bytes() == b"model"
    assert model_file(USER, "mdl_aaaaaaaaaa", "onnx") == f"{USER}/mdl_aaaaaaaaaa.onnx"
    assert model_file(USER, "mdl_aaaaaaaaaa", "keras") == f"{USER}/mdl_aaaaaaaaaa.keras"

    await storage.delete(USER, "mdl_aaaaaaaaaa")

    assert not storage.path(USER, "mdl_aaaaaaaaaa", Framework.onnx).exists()


async def test_delete_removes_one_file(storage: LocalModelStorage) -> None:
    await storage.save(await incoming(storage), USER, "mdl_aaaaaaaaaa", Framework.keras)
    await storage.save(await incoming(storage), USER, "mdl_bbbbbbbbbb", Framework.keras)

    await storage.delete(USER, "mdl_aaaaaaaaaa")
    await storage.delete(USER, "mdl_aaaaaaaaaa")  # gone already is fine

    assert not storage.path(USER, "mdl_aaaaaaaaaa", Framework.keras).exists()
    assert storage.path(USER, "mdl_bbbbbbbbbb", Framework.keras).exists()


async def test_delete_user_removes_the_users_directory(
    storage: LocalModelStorage, tmp_path: Path
) -> None:
    await storage.save(await incoming(storage), USER, "mdl_aaaaaaaaaa", Framework.keras)
    await storage.save(await incoming(storage), "usr_other00000", "mdl_bbbbbbbbbb", Framework.keras)

    await storage.delete_user(USER)
    await storage.delete_user(USER)  # a user without files is fine

    assert not (tmp_path / "models" / USER).exists()
    assert storage.path("usr_other00000", "mdl_bbbbbbbbbb", Framework.keras).exists()


async def test_discard_removes_an_incoming_file(storage: LocalModelStorage) -> None:
    path = await incoming(storage)

    await storage.discard(path)
    await storage.discard(path)

    assert not path.exists()


async def test_prepare_removes_what_crashed_uploads_left(
    storage: LocalModelStorage, caplog: pytest.LogCaptureFixture
) -> None:
    stale, fresh = await incoming(storage), await incoming(storage)
    two_hours_ago = time.time() - 7200
    os.utime(stale, (two_hours_ago, two_hours_ago))

    with caplog.at_level(logging.INFO):
        await storage.prepare()

    assert not stale.exists()
    assert fresh.exists()  # an upload in progress
    assert "Removed 1 incoming model files that crashed uploads left" in caplog.text


async def test_the_sweep_skips_what_is_no_file_and_goes_on_after_an_error(
    storage: LocalModelStorage,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    two_hours_ago = time.time() - 7200
    directory = tmp_path / "models" / ".incoming" / "a-directory"
    directory.mkdir()
    stuck, stale = await incoming(storage), await incoming(storage)
    for path in (directory, stuck, stale):
        os.utime(path, (two_hours_ago, two_hours_ago))
    unlink = Path.unlink

    def failing_unlink(path: Path, *, missing_ok: bool = False) -> None:
        if path == stuck:
            raise PermissionError(str(path))
        unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", failing_unlink)

    with caplog.at_level(logging.INFO):
        await storage.sweep()

    assert directory.is_dir()
    assert stuck.exists()
    assert not stale.exists()
    assert "An incoming model file was not removed (PermissionError)" in caplog.text
    assert "Removed 1 incoming model files" in caplog.text


async def test_the_incoming_files_are_swept_every_10_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweeps: list[float] = []
    sleeps: list[float] = []

    class Storage:
        async def sweep(self) -> None:
            sweeps.append(1)
            if len(sweeps) == 3:
                raise asyncio.CancelledError

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(storage_module.anyio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await keep_incoming_swept(cast("ModelStorage", Storage()))

    assert sleeps == [600, 600, 600]
    assert len(sweeps) == 3


async def test_a_sweep_that_fails_does_not_stop_the_next(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sweeps: list[int] = []

    class Storage:
        async def sweep(self) -> None:
            sweeps.append(1)
            if len(sweeps) == 1:
                raise PermissionError("/data/models/.incoming")
            raise asyncio.CancelledError

    async def sleep(_seconds: float) -> None:
        pass

    monkeypatch.setattr(storage_module.anyio, "sleep", sleep)

    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await keep_incoming_swept(cast("ModelStorage", Storage()))

    assert len(sweeps) == 2
    assert "The incoming model files were not swept (PermissionError)" in caplog.text
