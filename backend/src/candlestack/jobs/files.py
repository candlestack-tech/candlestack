"""The files of the jobs on the volume the backend and the worker share (``JOB_FILES_DIR``):
one directory per job, ``<job_id>/candles.parquet`` from the backend and
``<job_id>/predictions.parquet`` from the worker.

Each file is written under a temporary name and renamed, so the other side never reads half
of one. The backend removes a job's directory once it has the result (``discard``); the worker
removes the ones a crash left (``sweep``).
"""

import logging
import re
import shutil
import time
from datetime import timedelta
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

CANDLES = "candles.parquet"
PREDICTIONS = "predictions.parquet"
# A job's directory older than this was left by a backend that stopped waiting for it.
STALE_FILES = timedelta(hours=1)
_PART = ".part"
_JOB_ID = re.compile(r"^[0-9a-f]{32}$")
# The backend and the worker may run as different users (the dev backend runs as root): both
# must create and remove files in a job's directory. Only these two containers mount the volume.
_SHARED = 0o777


class JobFiles:
    def __init__(self, root: Path) -> None:
        self._root = root

    def directory(self, job_id: str) -> Path:
        """The directory of the job's files.

        Raises:
            ValueError: ``job_id`` is no job ID, so it could name a path outside the volume.
        """
        if not _JOB_ID.match(job_id):
            raise ValueError(f"Not a job ID: {job_id!r}")
        return self._root / job_id

    def write_candles(self, job_id: str, candles: pl.DataFrame) -> None:
        directory = self.directory(job_id)
        directory.mkdir(parents=True, exist_ok=True)
        directory.chmod(_SHARED)
        _write(candles, directory / CANDLES)

    def read_candles(self, job_id: str) -> pl.DataFrame:
        """Raises:
        OSError: The job has no candles file, or it cannot be read.
        """
        return _read(self.directory(job_id) / CANDLES)

    def write_predictions(self, job_id: str, predictions: pl.DataFrame) -> None:
        _write(predictions, self.directory(job_id) / PREDICTIONS)

    def read_predictions(self, job_id: str) -> pl.DataFrame:
        """``ts`` (Int64) and ``prediction`` (Float64), one row per sample.

        Raises:
            OSError: The job has no predictions file, or it cannot be read.
        """
        return _read(self.directory(job_id) / PREDICTIONS)

    def discard(self, job_id: str) -> None:
        """Removes the job's directory; nothing when it is gone already."""
        shutil.rmtree(self.directory(job_id), onexc=_gone_is_fine)

    def sweep(self, older_than: timedelta = STALE_FILES) -> int:
        """Removes the directories of jobs older than ``older_than``; returns how many."""
        stale_before = time.time() - older_than.total_seconds()
        removed = 0
        if not self._root.is_dir():
            return 0
        for path in self._root.iterdir():
            # One entry that cannot be read or removed does not stop the others.
            try:
                if _JOB_ID.match(path.name) and path.stat().st_mtime < stale_before:
                    shutil.rmtree(path)
                    removed += 1
            except OSError as error:
                logger.warning("The files of a job were not removed (%s)", type(error).__name__)
        return removed


def _write(frame: pl.DataFrame, path: Path) -> None:
    part = path.with_name(path.name + _PART)
    frame.write_parquet(part)
    part.replace(path)


def _read(path: Path) -> pl.DataFrame:
    try:
        return pl.read_parquet(path)
    except pl.exceptions.PolarsError as error:
        raise OSError(f"{path.name} cannot be read: {error}") from error


def _gone_is_fine(_function: object, _path: str, error: BaseException) -> None:
    if not isinstance(error, FileNotFoundError):
        raise error
