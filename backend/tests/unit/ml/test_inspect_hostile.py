"""The static check of uploaded Keras files on the hostile files of tests/fixtures/keras/hostile,
built by make_hostile.py: real models with one harmful change each, checked at the real upload
limit."""

import zipfile
from pathlib import Path

import pytest

from candlestack.ml.inspect import CheckFailed, inspect_keras

HOSTILE = Path(__file__).parents[2] / "fixtures" / "keras" / "hostile"
FILE_MAX = 50 * 1024 * 1024  # FILE_MAX_BYTES of the API
SAVE_AGAIN = 'Save the model again with model.save("model.keras") and upload that file.'
TOO_LARGE = "more than any model CandleStack takes. Upload a smaller model."


def custom_code(layer: str, name: str) -> str:
    return (
        f'The layer "{layer}" uses custom code ({name}), which CandleStack does not run. '
        "Rebuild the model with the built-in Keras layers only and upload it again."
    )


def hostile(name: str) -> Path:
    return HOSTILE / f"{name}.keras"


@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("zip_bomb", f"The file unpacks to more than 200 MB, {TOO_LARGE}"),
        ("zip64", "The file holds far more files than a Keras model has. " + SAVE_AGAIN),
        ("path_traversal", "The file holds a path that points outside the model. " + SAVE_AGAIN),
        ("huge_config", f"The config.json of the file is larger than 1 MB, {TOO_LARGE}"),
        # Found in the model nested in the model, named by its own layer.
        ("nested_lambda", custom_code("lambda", "Lambda")),
        ("get_file", custom_code("dense", "get_file")),
        ("unsafe_deserialization", custom_code("dense", "enable_unsafe_deserialization")),
    ],
)
def test_a_hostile_file_fails_opens_before_anything_is_unpacked(
    name: str, detail: str, tmp_path: Path
) -> None:
    scratch = tmp_path / "weights.part"

    with pytest.raises(CheckFailed) as failed:
        inspect_keras(hostile(name), scratch, file_max=FILE_MAX)

    assert (failed.value.check, failed.value.detail) == ("opens", detail)
    assert not scratch.exists()


@pytest.mark.xfail(
    strict=True,
    reason="The check lets a symlink among the assets through. Harmless today: the worker "
    "copies the members by name, which drops the link, and zipfile never makes one.",
)
def test_a_symlink_in_the_archive_fails_opens(tmp_path: Path) -> None:
    with pytest.raises(CheckFailed) as failed:
        inspect_keras(hostile("symlink"), tmp_path / "weights.part", file_max=FILE_MAX)

    assert failed.value.check == "opens"


def test_the_files_are_what_they_say() -> None:
    """Each file holds its harm, so the tests above do not pass for another reason."""
    with zipfile.ZipFile(hostile("zip_bomb")) as archive:
        weights = archive.getinfo("model.weights.h5")
        assert weights.file_size > 4 * FILE_MAX
        assert weights.compress_size < 2048
    assert b"PK\x06\x06" in hostile("zip64").read_bytes()
    with zipfile.ZipFile(hostile("symlink")) as archive:
        link = archive.getinfo("assets/vocabulary.txt")
        assert (link.external_attr >> 16) & 0o170000 == 0o120000
        assert archive.read(link) == b"/etc/passwd"
    with zipfile.ZipFile(hostile("path_traversal")) as archive:
        assert "assets/../../../tmp/evil.py" in archive.namelist()
