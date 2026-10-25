"""Loading an uploaded Keras file in the worker: without its compile config, with stubs for
the user's training-only objects, and never a Lambda's code or a class of the user's."""

import json
import zipfile
from pathlib import Path

import keras
import numpy as np
import pytest

from candlestack.worker.model import load_keras, training_only_stubs

USER = "user-1"


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    path = tmp_path / "scratch"
    path.mkdir()
    return path


def model_file(models: Path, name: str) -> Path:
    return models / USER / f"{name}.keras"


def test_a_model_loads_and_answers(models: Path, scratch: Path) -> None:
    model = load_keras(model_file(models, "raw_candles"), scratch)

    output = model.predict(np.full((2, 25, 5), 105, dtype=np.float32), verbose=0)

    assert output.shape == (2, 1)
    assert np.isfinite(output).all()


def test_the_compile_config_is_never_loaded(models: Path, scratch: Path) -> None:
    original = model_file(models, "operations")
    with zipfile.ZipFile(original) as archive:
        assert "compile_config" in json.loads(archive.read("config.json"))

    model = load_keras(original, scratch)

    with zipfile.ZipFile(scratch / "model.keras") as copy:
        assert "compile_config" not in json.loads(copy.read("config.json"))
        assert sorted(copy.namelist()) == ["config.json", "metadata.json", "model.weights.h5"]
    assert not model.compiled


def test_the_users_regularizer_becomes_a_stub(models: Path, scratch: Path) -> None:
    path = model_file(models, "custom_regularizer")
    # Keras alone cannot load it: the class is the user's.
    with pytest.raises(TypeError, match="OrthogonalL2Regularizer"):
        keras.saving.load_model(path, compile=False, safe_mode=True)

    model = load_keras(path, scratch)

    assert model.predict(np.zeros((1, 25, 23), dtype=np.float32), verbose=0).shape == (1, 1)


@pytest.mark.parametrize("name", ["lambda_layer", "custom_layer"])
def test_code_of_the_user_never_loads(models: Path, scratch: Path, name: str) -> None:
    with pytest.raises((ValueError, TypeError)):
        load_keras(model_file(models, name), scratch)


def test_a_file_that_is_no_zip_does_not_load(tmp_path: Path, scratch: Path) -> None:
    path = tmp_path / "m.keras"
    path.write_bytes(b"not a zip")

    with pytest.raises(zipfile.BadZipFile):
        load_keras(path, scratch)


@pytest.mark.parametrize(
    ("members", "message"),
    [
        ({"metadata.json": "{}"}, "The file has no config.json"),
        ({"config.json": "[1]"}, "config.json holds no model"),
        ({"config.json": " " * (1024 * 1024 + 1)}, "config.json is larger than a model's"),
    ],
)
def test_a_zip_without_a_model_does_not_load(
    tmp_path: Path, scratch: Path, members: dict[str, str], message: str
) -> None:
    path = tmp_path / "m.keras"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)

    with pytest.raises(ValueError, match=message):
        load_keras(path, scratch)


def test_stubs_are_made_for_training_only_objects_of_the_user_only() -> None:
    config = {
        "layers": [
            {
                "config": {
                    "kernel_regularizer": {"registered_name": "Custom>L2", "config": {}},
                    "bias_constraint": {"registered_name": "Custom>Unit", "config": {}},
                    "activity_regularizer": {"registered_name": "L1", "config": {}},
                    "recurrent_constraint": None,
                }
            },
            {"class_name": "Scale", "registered_name": "Custom>Scale"},
            {"config": {"layer": {"class_name": "Scale", "registered_name": "Custom>Scale"}}},
            {"config": {"layer": {"config": {"beta_constraint": {"registered_name": "My>Max"}}}}},
        ],
        3: "keys that are no strings",
    }

    stubs = training_only_stubs(config)  # ty: ignore[invalid-argument-type]

    assert sorted(stubs) == ["Custom>L2", "Custom>Unit", "My>Max"]
    assert stubs["Custom>L2"]().get_config() == {}
    assert float(stubs["Custom>L2"].from_config({"l2": 1})(np.ones(3))) == 0.0
    constraint = stubs["Custom>Unit"].from_config({})
    assert constraint(np.ones(2)).tolist() == [1.0, 1.0]
    assert constraint.get_config() == {}
