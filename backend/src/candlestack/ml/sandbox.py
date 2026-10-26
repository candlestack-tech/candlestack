"""Runs the check of an uploaded file (``inspect.py``) in a short-lived child process, one at a
time, with a time limit; on Linux the child limits its own memory, CPU time and file size.

The check parses a file the user made with zipfile, json and h5py (the HDF5 C library), or
with protobuf and the ONNX checker: a crafted file that crashes the parser or exhausts its
memory then takes down only the child, and fails the check instead of the API. The API process
never imports h5py or onnx.
"""

import json
import logging
import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import anyio

from candlestack.ml.errors import ModelCheckFailed
from candlestack.ml.inspect import ModelFacts
from candlestack.ml.schemas import FILE_MAX_BYTES, Framework, ModelCheck

logger = logging.getLogger(__name__)

# A real model is checked in well under a second (the child's start included).
CHECK_TIMEOUT = 20.0
# How long an upload waits for its turn: the checks of all users run one after another.
QUEUE_TIMEOUT = 120.0
# What the table's columns hold (`models.py`): the Keras version (none for ONNX), bigints; a
# shape has a few dimensions.
_KERAS_VERSION_MAX = 32
_BIGINT_MAX = 2**63 - 1
_DIMENSIONS_MAX = 32
_FACT_NAMES = frozenset(field.name for field in fields(ModelFacts))
# One check at a time: they never pile up, and never use the thread pool that the sync
# dependencies of the routes need.
_CHECKS = anyio.CapacityLimiter(1)
_SRC = Path(__file__).parents[2]
# The child imports the check without the package roots, whose imports (the app, the
# database) it does not need: empty packages stand in for them. ``-I`` keeps the environment
# and the script's directory off its path.
_CHILD = """\
import sys, types
for name in ("candlestack", "candlestack.ml"):
    package = types.ModuleType(name)
    package.__path__ = [sys.argv[1] + "/" + name.replace(".", "/")]
    sys.modules[name] = package
from candlestack.ml.inspect import main
sys.exit(main(sys.argv[2:], limits=True))
"""
# What the child gets of the environment: no keys or database URL of the app. One thread for
# the numeric libraries, which would reserve memory per core otherwise.
_ENVIRONMENT_KEPT = ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")
_THREADS = {"OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
# What to do about a file whose check stopped, by its format.
SAVE_AGAIN = {
    Framework.keras: 'Save the model again with model.save("model.keras") and upload that file.',
    Framework.onnx: "Export the model to ONNX again and upload that file.",
}
TOO_LONG = "The check took too long. The file may be damaged. "
BUSY = "Many models are being checked right now. Upload the file again in a few minutes."
STOPPED = "The check of the file stopped before it finished. The file may be damaged. "


def _environment() -> dict[str, str]:
    kept = {name: os.environ[name] for name in _ENVIRONMENT_KEPT if name in os.environ}
    return {**kept, **_THREADS}


async def check_upload(path: Path, scratch: Path, framework: Framework) -> ModelFacts:
    """Checks the uploaded file at `path`, of the format `framework`, in a child process; the
    weights of a Keras file are unpacked to `scratch`, which the caller removes.

    Raises:
        ModelCheckFailed: A check failed, the checks of other uploads kept it waiting too long,
            the child took too long or stopped (crashed, killed at a limit).
    """
    command = [
        sys.executable,
        "-I",
        "-c",
        _CHILD,
        str(_SRC),
        str(path),
        str(scratch),
        str(FILE_MAX_BYTES),
        framework.value,
    ]
    try:
        with anyio.fail_after(QUEUE_TIMEOUT):
            await _CHECKS.acquire()
    except TimeoutError:
        logger.warning("An upload waited longer than %s s for its check", QUEUE_TIMEOUT)
        raise ModelCheckFailed(ModelCheck.opens, BUSY) from None
    try:
        # Past the time limit the child is killed.
        with anyio.move_on_after(CHECK_TIMEOUT) as limit:
            done = await anyio.run_process(
                command, check=False, stdin=subprocess.DEVNULL, env=_environment()
            )
    finally:
        _CHECKS.release()
    if limit.cancelled_caught:
        logger.warning("The check of an upload took longer than %s s", CHECK_TIMEOUT)
        raise ModelCheckFailed(ModelCheck.opens, TOO_LONG + SAVE_AGAIN[framework])
    return _result(done.returncode, done.stdout, framework)


def _result(returncode: int, stdout: bytes, framework: Framework) -> ModelFacts:
    """What the child found.

    Raises:
        ModelCheckFailed: A check failed, or the child wrote no result or one off its form.
    """
    try:
        result = json.loads(stdout) if returncode == 0 else None
        if result is not None and "failed" in result:
            failed = result["failed"]
            raise ModelCheckFailed(ModelCheck(failed["check"]), str(failed["detail"]))
        if result is not None:
            return _facts(result["facts"], framework)
    except (ValueError, KeyError, TypeError):  # no JSON, or not what the child writes
        pass
    logger.warning("The check of an upload stopped (exit code %s)", returncode)
    raise ModelCheckFailed(ModelCheck.opens, STOPPED + SAVE_AGAIN[framework])


def _is_count(value: object, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= maximum


def _is_shape(value: object) -> bool:
    return value is None or (
        isinstance(value, list)
        and 0 < len(value) <= _DIMENSIONS_MAX
        and all(_is_count(dim, _BIGINT_MAX) for dim in value)
    )


def _is_version(value: object, framework: Framework) -> bool:
    """A Keras version for a Keras file, none for an ONNX file."""
    if framework is Framework.onnx:
        return value is None
    return isinstance(value, str) and 0 < len(value) <= _KERAS_VERSION_MAX


def _facts(raw: object, framework: Framework) -> ModelFacts:
    """The facts the child wrote, when they fit what the table holds.

    Raises:
        ValueError: They do not.
    """
    if not isinstance(raw, dict) or set(raw) != _FACT_NAMES:
        raise ValueError("not the facts of a check")
    if (
        not _is_version(raw["keras_version"], framework)
        or not _is_count(raw["parameters"], _BIGINT_MAX)
        or not _is_shape(raw["input_shape"])
        or not _is_shape(raw["output_shape"])
    ):
        raise ValueError("facts off their form")
    return ModelFacts(**raw)
