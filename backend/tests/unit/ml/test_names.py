"""The names models get: of an upload, numbered when taken, and what Rename takes."""

import pytest
from pydantic import ValidationError

from candlestack.ml.errors import UnsupportedModelFormat
from candlestack.ml.schemas import Framework, ModelUpdate
from candlestack.ml.service import free_name, numbered, upload_name


@pytest.mark.parametrize(
    ("file_name", "name"),
    [
        ("lstm.keras", "lstm.keras"),
        ("LSTM v2.KERAS", "LSTM v2.KERAS"),
        ("C:\\Users\\ada\\models\\lstm.keras", "lstm.keras"),
        ("../../etc/lstm.keras", "lstm.keras"),
        ("  lstm\t\x00 .keras", "lstm.keras"),
        ("ls\u202etm\u200b.keras", "lstm.keras"),  # a bidi override, a zero-width space (Cf)
        ("\u2066\u2069.keras", "model.keras"),
        (".keras", "model.keras"),
        ("dir/\x07.keras", "model.keras"),
        ("a" * 80 + ".keras", "a" * 60 + ".keras"),
        ("a" * 59 + " b.keras", "a" * 59 + ".keras"),
    ],
)
def test_the_name_of_an_upload(file_name: str, name: str) -> None:
    assert upload_name(file_name) == (name, Framework.keras)


@pytest.mark.parametrize(
    ("file_name", "name"),
    [
        ("mixer.onnx", "mixer.onnx"),
        ("Mixer.ONNX", "Mixer.ONNX"),
        ("C:\\models\\mixer.onnx", "mixer.onnx"),
        (".onnx", "model.onnx"),
        ("a" * 80 + ".onnx", "a" * 60 + ".onnx"),
    ],
)
def test_the_name_of_an_onnx_upload(file_name: str, name: str) -> None:
    assert upload_name(file_name) == (name, Framework.onnx)


@pytest.mark.parametrize(
    "file_name",
    ["model.pt", "model.h5", "model.keras.zip", "keras", "model.keras/", "model", "model.onnx.gz"],
)
def test_only_keras_and_onnx_files_are_taken(file_name: str) -> None:
    with pytest.raises(UnsupportedModelFormat):
        upload_name(file_name)


@pytest.mark.parametrize(
    ("number", "name"),
    [(1, "lstm.keras"), (2, "lstm (2).keras"), (13, "lstm (13).keras")],
)
def test_a_taken_name_gets_a_number(number: int, name: str) -> None:
    assert numbered("lstm.keras", number) == name


def test_an_onnx_name_gets_its_number_before_its_extension() -> None:
    assert numbered("mixer.onnx", 2) == "mixer (2).onnx"
    assert numbered("a" * 60 + ".onnx", 10) == "a" * 55 + " (10).onnx"


def test_a_numbered_name_stays_within_what_rename_takes() -> None:
    name = numbered("a" * 56 + " bcd.KERAS", 12)

    assert name == "a" * 55 + " (12).KERAS"
    assert len(name.removesuffix(".KERAS")) == 60


@pytest.mark.parametrize(
    ("taken", "name"),
    [
        ([], "lstm.keras"),
        (["lstm.keras"], "lstm (2).keras"),
        (["lstm.keras", "lstm (2).keras", "lstm (4).keras"], "lstm (3).keras"),
        (["lstm (2).keras", "lstm v2.keras"], "lstm.keras"),
    ],
)
def test_the_free_name(taken: list[str], name: str) -> None:
    assert free_name("lstm.keras", "lstm.keras", set(taken)) == name


def test_the_free_name_compares_as_postgres_lowercases() -> None:
    # Python lowercases "İ" to two letters; Postgres (C collation) leaves it as it is.
    assert free_name("İnci.KERAS", "İnci.keras", {"İnci.keras"}) == "İnci (2).KERAS"


def test_rename_strips_the_name() -> None:
    assert ModelUpdate(name="  lstm v2 ").name == "lstm v2"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "a" * 61,
        "lstm/v2",
        "lstm\\v2",
        "lstm\nv2",
        "lstm\x00",
        "tab\there",
        "gpj.\u202eexe",
        "ls\u200btm",
        "\ufeffbom",
        "n\x85el",
    ],
)
def test_rename_takes_1_to_60_characters_without_slashes_or_control_characters(name: str):
    with pytest.raises(ValidationError):
        ModelUpdate(name=name)


def test_rename_takes_60_characters_and_unicode() -> None:
    assert ModelUpdate(name="Modèl ✓ " + "a" * 52).name == "Modèl ✓ " + "a" * 52


def test_an_update_changes_only_what_it_names() -> None:
    assert ModelUpdate(starred=True).model_dump(exclude_unset=True) == {"starred": True}


def test_an_unknown_field_is_refused() -> None:
    with pytest.raises(ValidationError):
        ModelUpdate.model_validate({"framework": "torch"})
