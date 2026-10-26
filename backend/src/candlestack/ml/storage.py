"""Where the files of the uploaded models are kept: ``ModelStorage``, which the service uses,
and ``LocalModelStorage``, a directory (a Docker volume on the VM). Another storage, such as
Cloudflare R2, can replace it without touching the service.

An upload streams into a file of its own in the incoming directory, is checked there and then
moved to its place, so a model's file is always whole. The files are served only through the
API, never straight from the directory.
"""

import logging
import secrets
import shutil
import time
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Protocol

import anyio

from candlestack.ml.schemas import SUFFIXES, Framework

logger = logging.getLogger(__name__)

# An incoming file older than this was left by an upload that crashed.
STALE_INCOMING = timedelta(hours=1)
SWEEP_EVERY = timedelta(minutes=10)
_INCOMING = ".incoming"


def model_file(user_id: str, model_id: str, framework: str) -> str:
    """The file of the user's model of the format `framework` (``keras``, ``onnx``) relative
    to the storage's directory, as a job names it: ``<user_id>/<model_id>.keras``."""
    return f"{user_id}/{model_id}{SUFFIXES[Framework(framework)]}"


class ModelStorage(Protocol):
    def incoming_path(self) -> Path:
        """A new local path for a file that comes in (an upload, the weights unpacked by the
        check); the caller removes it with `discard`."""
        ...

    async def save(self, incoming: Path, user_id: str, model_id: str, framework: Framework) -> None:
        """Moves the finished file `incoming` to the place of the user's model of the format
        `framework`."""
        ...

    async def delete(self, user_id: str, model_id: str) -> None:
        """Deletes the file of the user's model; nothing when it is gone already."""
        ...

    async def delete_user(self, user_id: str) -> None:
        """Deletes the files of every model of the user."""
        ...

    async def discard(self, incoming: Path) -> None:
        """Removes a file of `incoming_path`; nothing when there is none."""
        ...

    async def prepare(self) -> None:
        """Makes the storage ready at startup and sweeps it."""
        ...

    async def sweep(self) -> None:
        """Removes what crashed uploads left (incoming files older than an hour)."""
        ...


class LocalModelStorage:
    """The files in a directory: ``<user_id>/<model_id>.keras`` or ``.onnx`` (``model_file``),
    and the uploads in progress as ``.incoming/<random>.part``, on the same filesystem, so a
    move is an atomic rename."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._incoming = root / _INCOMING

    def path(self, user_id: str, model_id: str, framework: Framework) -> Path:
        """Where the file of the user's model of the format `framework` is."""
        return self._root / model_file(user_id, model_id, framework)

    def incoming_path(self) -> Path:
        return self._incoming / f"{secrets.token_hex(16)}.part"

    async def save(self, incoming: Path, user_id: str, model_id: str, framework: Framework) -> None:
        target = self.path(user_id, model_id, framework)

        def move() -> None:
            target.parent.mkdir(exist_ok=True)
            incoming.replace(target)

        await anyio.to_thread.run_sync(move)

    async def delete(self, user_id: str, model_id: str) -> None:
        # The row that says the format may be gone already: the file goes in either format.
        def remove() -> None:
            for framework in Framework:
                self.path(user_id, model_id, framework).unlink(missing_ok=True)

        await anyio.to_thread.run_sync(remove)

    async def delete_user(self, user_id: str) -> None:
        def remove() -> None:
            # Gone already is fine: a user who never uploaded has no directory.
            if (directory := self._root / user_id).exists():
                shutil.rmtree(directory)

        await anyio.to_thread.run_sync(remove)

    async def discard(self, incoming: Path) -> None:
        await anyio.to_thread.run_sync(partial(incoming.unlink, missing_ok=True))

    async def prepare(self) -> None:
        await anyio.to_thread.run_sync(partial(self._incoming.mkdir, parents=True, exist_ok=True))
        await self.sweep()

    async def sweep(self) -> None:
        removed = await anyio.to_thread.run_sync(self._sweep)
        if removed:
            logger.info("Removed %d incoming model files that crashed uploads left", removed)

    def _sweep(self) -> int:
        stale_before = time.time() - STALE_INCOMING.total_seconds()
        removed = 0
        for path in self._incoming.iterdir():
            # One entry that cannot be read or removed does not stop the others.
            try:
                if path.is_file() and path.stat().st_mtime < stale_before:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError as error:
                logger.warning("An incoming model file was not removed (%s)", type(error).__name__)
        return removed


async def keep_incoming_swept(storage: ModelStorage) -> None:
    """Removes what crashed uploads left every ``SWEEP_EVERY`` while the app runs, so a process
    that keeps running does not keep their files either."""
    while True:
        await anyio.sleep(SWEEP_EVERY.total_seconds())
        try:
            await storage.sweep()
        except OSError as error:  # the directory cannot be read now: the next sweep tries again
            logger.warning("The incoming model files were not swept (%s)", type(error).__name__)
