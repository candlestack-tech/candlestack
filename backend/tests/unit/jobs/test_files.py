"""The jobs' files on the shared volume: written whole, read back, removed."""

import logging
import os
import shutil
import time
from pathlib import Path

import polars as pl
import pytest

from candlestack.jobs import CANDLES, PREDICTIONS, JobFiles

JOB_ID = "0123456789abcdef0123456789abcdef"
CANDLE_FRAME = pl.DataFrame(
    {
        "ts": [0, 60],
        "open": [1.0, 2.0],
        "high": [1.5, 2.5],
        "low": [0.5, 1.5],
        "close": [1.2, 2.2],
        "volume": [10.0, 20.0],
    }
)


@pytest.fixture
def files(tmp_path: Path) -> JobFiles:
    return JobFiles(tmp_path / "jobs")


def test_candles_and_predictions_go_through_the_volume(files: JobFiles, tmp_path: Path) -> None:
    predictions = pl.DataFrame({"ts": [60], "prediction": [0.25]})

    files.write_candles(JOB_ID, CANDLE_FRAME)
    files.write_predictions(JOB_ID, predictions)

    assert files.read_candles(JOB_ID).equals(CANDLE_FRAME)
    assert files.read_predictions(JOB_ID).equals(predictions)
    directory = tmp_path / "jobs" / JOB_ID
    assert sorted(path.name for path in directory.iterdir()) == [CANDLES, PREDICTIONS]


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_a_jobs_directory_is_writable_by_the_other_side(files: JobFiles) -> None:
    files.write_candles(JOB_ID, CANDLE_FRAME)

    assert files.directory(JOB_ID).stat().st_mode & 0o777 == 0o777


def test_a_missing_file_is_an_os_error(files: JobFiles) -> None:
    with pytest.raises(OSError, match=r"No such file|cannot find|not found"):
        files.read_predictions(JOB_ID)


def test_a_damaged_file_is_an_os_error(files: JobFiles) -> None:
    files.write_candles(JOB_ID, CANDLE_FRAME)
    (files.directory(JOB_ID) / PREDICTIONS).write_bytes(b"not parquet")

    with pytest.raises(OSError, match=r"predictions\.parquet cannot be read"):
        files.read_predictions(JOB_ID)


@pytest.mark.parametrize("job_id", ["..", "../x", "ABC", "0" * 31, f"{'0' * 31}/"])
def test_a_job_id_never_names_a_path_outside(files: JobFiles, job_id: str) -> None:
    with pytest.raises(ValueError, match="Not a job ID"):
        files.directory(job_id)


def test_discard_removes_the_jobs_files_and_tolerates_none(files: JobFiles) -> None:
    files.write_candles(JOB_ID, CANDLE_FRAME)

    files.discard(JOB_ID)
    files.discard(JOB_ID)

    assert not files.directory(JOB_ID).exists()


def test_discard_reports_what_it_cannot_remove(
    files: JobFiles, monkeypatch: pytest.MonkeyPatch
) -> None:
    files.write_candles(JOB_ID, CANDLE_FRAME)

    def refuse(path: str, *args: object, **kwargs: object) -> None:
        raise PermissionError(path)

    monkeypatch.setattr(os, "unlink", refuse)

    with pytest.raises(PermissionError):
        files.discard(JOB_ID)


def test_sweep_removes_only_old_job_directories(files: JobFiles, tmp_path: Path) -> None:
    old, new = "a" * 32, "b" * 32
    files.write_candles(old, CANDLE_FRAME)
    files.write_candles(new, CANDLE_FRAME)
    other = tmp_path / "jobs" / "not-a-job"
    other.mkdir()
    hour_ago = time.time() - 3700
    for path in (files.directory(old), other):
        os.utime(path, (hour_ago, hour_ago))

    assert files.sweep() == 1

    assert not files.directory(old).exists()
    assert files.directory(new).exists()
    assert other.exists()


def test_sweep_of_no_directory_removes_nothing(files: JobFiles) -> None:
    assert files.sweep() == 0


def test_sweep_goes_on_past_what_it_cannot_remove(
    files: JobFiles, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    for job_id in ("a" * 32, "b" * 32):
        files.write_candles(job_id, CANDLE_FRAME)
        hour_ago = time.time() - 3700
        os.utime(files.directory(job_id), (hour_ago, hour_ago))
    removed: list[str] = []

    def rmtree(path: Path) -> None:
        if path.name.startswith("a"):
            raise PermissionError(str(path))
        removed.append(path.name)

    monkeypatch.setattr(shutil, "rmtree", rmtree)

    with caplog.at_level(logging.WARNING):
        assert files.sweep() == 1

    assert removed == ["b" * 32]
    assert "The files of a job were not removed (PermissionError)" in caplog.text
