"""The check of an upload in its child process: what it finds, and a child that takes too long,
crashes or writes something else fails the check instead of the request."""

import json
import logging
import sys
from pathlib import Path

import pytest

from candlestack.ml import sandbox
from candlestack.ml.errors import ModelCheckFailed
from candlestack.ml.inspect import ModelFacts
from candlestack.ml.sandbox import BUSY, check_upload
from candlestack.ml.schemas import Framework, ModelCheck

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
ONNX_FIXTURES = Path(__file__).parents[2] / "fixtures" / "onnx"
SAVE_AGAIN = 'Save the model again with model.save("model.keras") and upload that file.'
TOO_LONG = "The check took too long. The file may be damaged. " + SAVE_AGAIN
STOPPED = "The check of the file stopped before it finished. The file may be damaged. " + (
    SAVE_AGAIN
)


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    return tmp_path / "weights.part"


async def test_a_model_passes(scratch: Path) -> None:
    facts = await check_upload(FIXTURES / "functional.keras", scratch, Framework.keras)

    assert facts == ModelFacts("3.15.1", 9325, [25, 23], [1])


async def test_an_onnx_model_passes(scratch: Path) -> None:
    facts = await check_upload(ONNX_FIXTURES / "raw_candles.onnx", scratch, Framework.onnx)

    assert facts == ModelFacts(None, 128, [25, 5], [1])


async def test_a_failed_check_of_an_onnx_file_is_named(tmp_path: Path, scratch: Path) -> None:
    keras_file = tmp_path / "keras.onnx"
    keras_file.write_bytes((FIXTURES / "functional.keras").read_bytes())

    with pytest.raises(ModelCheckFailed) as failed:
        await check_upload(keras_file, scratch, Framework.onnx)

    assert failed.value.check is ModelCheck.opens
    assert failed.value.detail.startswith("The file is not a readable ONNX model.")


async def test_a_stopped_check_of_an_onnx_file_says_to_export_it_again(
    scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sandbox, "_CHILD", "import sys; sys.exit(3)")

    with pytest.raises(ModelCheckFailed) as failed:
        await check_upload(ONNX_FIXTURES / "raw_candles.onnx", scratch, Framework.onnx)

    assert failed.value.detail == (
        "The check of the file stopped before it finished. The file may be damaged. Export the "
        "model to ONNX again and upload that file."
    )


async def test_a_failed_check_is_named(scratch: Path) -> None:
    with pytest.raises(ModelCheckFailed) as failed:
        await check_upload(FIXTURES / "lambda_layer.keras", scratch, Framework.keras)

    assert failed.value.check is ModelCheck.opens
    assert failed.value.detail.startswith('The layer "lambda" uses custom code (Lambda)')


async def test_a_check_that_takes_too_long_is_killed(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sandbox, "_CHILD", "import time; time.sleep(30)")
    monkeypatch.setattr(sandbox, "CHECK_TIMEOUT", 0.5)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelCheckFailed) as failed:
        await check_upload(FIXTURES / "functional.keras", scratch, Framework.keras)

    assert (failed.value.check, failed.value.detail) == (ModelCheck.opens, TOO_LONG)
    assert "The check of an upload took longer than 0.5 s" in caplog.text


@pytest.mark.parametrize(
    "child",
    [
        "import sys; sys.exit(3)",
        "print('not JSON')",
        "print('{\"other\": 1}')",
        'print(\'{"facts": {"parameters": 1}}\')',
        'print(\'{"failed": {"check": "loads", "detail": "x"}}\')',
    ],
    ids=["crash", "not-json", "unknown-result", "missing-facts", "unknown-check"],
)
async def test_a_child_without_a_result_fails_the_check(
    child: str, scratch: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sandbox, "_CHILD", child)

    with caplog.at_level(logging.WARNING), pytest.raises(ModelCheckFailed) as failed:
        await check_upload(FIXTURES / "functional.keras", scratch, Framework.keras)

    assert (failed.value.check, failed.value.detail) == (ModelCheck.opens, STOPPED)
    assert "The check of an upload stopped (exit code" in caplog.text


def test_the_child_gets_no_secrets_of_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    monkeypatch.setenv("PATH", "/usr/bin")

    environment = sandbox._environment()

    assert "DATABASE_URL" not in environment
    assert environment["PATH"] == "/usr/bin"
    assert environment["OPENBLAS_NUM_THREADS"] == "1"


async def test_an_upload_waits_for_its_turn_a_limited_time(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(sandbox, "QUEUE_TIMEOUT", 0.2)

    other = object()
    await sandbox._CHECKS.acquire_on_behalf_of(other)  # another upload's check runs
    try:
        with caplog.at_level(logging.WARNING), pytest.raises(ModelCheckFailed) as failed:
            await check_upload(FIXTURES / "functional.keras", scratch, Framework.keras)
    finally:
        sandbox._CHECKS.release_on_behalf_of(other)

    assert (failed.value.check, failed.value.detail) == (ModelCheck.opens, BUSY)
    assert "An upload waited longer than 0.2 s for its check" in caplog.text


FACTS = {
    "keras_version": "3.15.1",
    "parameters": 9325,
    "input_shape": [25, 23],
    "output_shape": [1],
}


@pytest.mark.parametrize(
    "change",
    [
        {"keras_version": ""},
        {"keras_version": "3" * 33},
        {"keras_version": 3},
        {"keras_version": None},
        {"parameters": 0},
        {"parameters": -1},
        {"parameters": True},
        {"parameters": 2**63},
        {"parameters": 1.5},
        {"input_shape": []},
        {"input_shape": [25, 0]},
        {"input_shape": [25, "23"]},
        {"input_shape": [1] * 33},
        {"output_shape": "1"},
        {"extra": 1},
    ],
)
def test_facts_off_their_form_are_refused(change: dict[str, object]) -> None:
    stdout = json.dumps({"facts": {**FACTS, **change}}).encode()

    with pytest.raises(ModelCheckFailed) as failed:
        sandbox._result(0, stdout, Framework.keras)

    assert failed.value.detail == STOPPED


def test_facts_without_shapes_pass() -> None:
    stdout = json.dumps({"facts": {**FACTS, "input_shape": None, "output_shape": None}})

    assert sandbox._result(0, stdout.encode(), Framework.keras) == ModelFacts(
        "3.15.1", 9325, None, None
    )


def test_the_facts_of_an_onnx_file_have_no_keras_version() -> None:
    onnx_facts = json.dumps({"facts": {**FACTS, "keras_version": None}}).encode()
    keras_facts = json.dumps({"facts": FACTS}).encode()

    assert sandbox._result(0, onnx_facts, Framework.onnx).keras_version is None
    with pytest.raises(ModelCheckFailed):
        sandbox._result(0, keras_facts, Framework.onnx)
    with pytest.raises(ModelCheckFailed):
        sandbox._result(0, onnx_facts, Framework.keras)


@pytest.mark.skipif(sys.platform != "linux", reason="the limits are set on Linux only")
async def test_a_child_over_its_memory_limit_stops(
    scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The child's own start and its limits, then 300 MB: more than its data limit.
    greedy = sandbox._CHILD.replace(
        "from candlestack.ml.inspect import main\nsys.exit(main(sys.argv[2:], limits=True))\n",
        "import resource\n"
        "from candlestack.ml.inspect import limit_resources\n"
        "limit_resources(resource, file_max=int(sys.argv[5]))\n"
        "memory = bytearray(300 * 1024 * 1024)\n"
        "print('{}')\n",
    )
    assert greedy != sandbox._CHILD
    monkeypatch.setattr(sandbox, "_CHILD", greedy)

    with pytest.raises(ModelCheckFailed) as failed:
        await check_upload(FIXTURES / "functional.keras", scratch, Framework.keras)

    assert failed.value.detail == STOPPED
