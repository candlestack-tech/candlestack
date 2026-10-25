"""The static check of uploaded Keras files, on the real files of tests/fixtures/keras and on
broken ones made from them."""

import json
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import h5py
import pytest

from candlestack.ml import allowlist, inspect
from candlestack.ml.inspect import CheckFailed, ModelFacts, count_parameters, inspect_keras

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
SAVE_AGAIN = 'Save the model again with model.save("model.keras") and upload that file.'
FILE_MAX = 50 * 1024 * 1024

Config = dict[str, Any]


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    return tmp_path / "weights.part"


def fixture(name: str) -> Path:
    return FIXTURES / f"{name}.keras"


def members(source: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(source) as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def write_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def rezip(
    tmp_path: Path,
    source: str = "functional",
    *,
    drop: tuple[str, ...] = (),
    put: dict[str, bytes] | None = None,
) -> Path:
    """A copy of a fixture without the members `drop`, with the members `put` added or
    replaced."""
    files = {name: data for name, data in members(fixture(source)).items() if name not in drop}
    files.update(put or {})
    return write_zip(tmp_path / "model.keras", files)


def with_config(tmp_path: Path, edit: Callable[[Config], object], source: str = "functional"):
    """A copy of a fixture whose config.json `edit` changed in place."""
    config = json.loads(members(fixture(source))["config.json"])
    edit(config)
    return rezip(tmp_path, source, put={"config.json": json.dumps(config).encode()})


def layer(config: Config, name: str) -> Config:
    return next(item for item in config["config"]["layers"] if item["name"] == name)


def check(path: Path, scratch: Path, file_max: int = FILE_MAX) -> ModelFacts:
    return inspect_keras(path, scratch, file_max=file_max)


def failure(path: Path, scratch: Path, file_max: int = FILE_MAX) -> CheckFailed:
    with pytest.raises(CheckFailed) as failed:
        check(path, scratch, file_max)
    return failed.value


def opens_fails(path: Path, scratch: Path, file_max: int = FILE_MAX) -> str:
    """The detail of the failed `opens` check."""
    failed = failure(path, scratch, file_max)
    assert failed.check == "opens"
    assert ";" not in failed.detail
    return failed.detail


@pytest.mark.parametrize(
    ("name", "facts"),
    [
        ("functional", ModelFacts("3.15.1", 9325, [25, 23], [1])),
        ("sequential", ModelFacts("3.15.1", 1235, [30, 5], [3])),
        # The model of the manifest without preprocessing; Normalization keeps its statistics.
        ("raw_candles", ModelFacts("3.15.1", 1028, [25, 5], [1])),
        # A user's regularizer and metric only shape and report training.
        ("custom_regularizer", ModelFacts("3.15.1", 4617, [25, 23], [1])),
        ("two_inputs", ModelFacts("3.15.1", 586, None, [1])),
    ],
)
def test_real_models_pass(name: str, facts: ModelFacts, scratch: Path) -> None:
    assert check(fixture(name), scratch) == facts


@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("lambda_layer", 'The layer "lambda" uses custom code (Lambda)'),
        ("custom_layer", 'The layer "scale" uses custom code (Scale)'),
    ],
)
def test_custom_code_fails_opens(name: str, detail: str, scratch: Path) -> None:
    assert opens_fails(fixture(name), scratch) == (
        f"{detail}, which CandleStack does not run. Rebuild the model with the built-in Keras "
        "layers only and upload it again."
    )


def test_a_model_without_weights_fails_weights(scratch: Path) -> None:
    failed = failure(fixture("no_weights"), scratch)

    assert failed.check == "weights"
    assert failed.detail == (
        "The file holds the layers only. Save the model after training and upload that file."
    )


@pytest.mark.parametrize(
    "content",
    [b"", b"not a zip at all", fixture("functional").read_bytes()[:70_000]],
    ids=["empty", "not-a-zip", "truncated"],
)
def test_a_file_that_is_no_zip_fails_opens(content: bytes, tmp_path: Path, scratch: Path):
    path = tmp_path / "model.keras"
    path.write_bytes(content)

    assert opens_fails(path, scratch) == (
        "The file is not a readable Keras model. It may be damaged or not a .keras file at all. "
        + SAVE_AGAIN
    )


def test_a_damaged_member_fails_opens(tmp_path: Path, scratch: Path) -> None:
    content = bytearray(fixture("functional").read_bytes())
    content[80_000] ^= 0xFF  # inside the stored weights, so their CRC does not match
    path = tmp_path / "model.keras"
    path.write_bytes(bytes(content))

    assert opens_fails(path, scratch).startswith("The file is not a readable Keras model.")


@pytest.mark.parametrize(
    "member", ["../config.py", "assets/../../evil.sh", "/etc/passwd", "\\evil.dll", "C:/evil"]
)
def test_a_path_outside_the_model_fails_opens(member: str, tmp_path: Path, scratch: Path):
    path = rezip(tmp_path, put={member: b"x"})

    assert opens_fails(path, scratch) == (
        "The file holds a path that points outside the model. " + SAVE_AGAIN
    )


def test_an_unknown_member_fails_opens(tmp_path: Path, scratch: Path) -> None:
    path = rezip(tmp_path, put={"custom_objects.py": b"import os"})

    assert opens_fails(path, scratch) == (
        "The file holds files a Keras model does not have. " + SAVE_AGAIN
    )


def test_assets_are_fine(tmp_path: Path, scratch: Path) -> None:
    path = rezip(tmp_path, put={"assets/": b"", "assets/vocabulary.txt": b"up\ndown\n"})

    assert check(path, scratch).parameters == 9325


@pytest.mark.parametrize("member", ["config.json", "metadata.json", "model.weights.h5"])
def test_a_missing_member_fails_opens(member: str, tmp_path: Path, scratch: Path) -> None:
    path = rezip(tmp_path, drop=(member,))

    assert opens_fails(path, scratch) == (
        f"The file has no {member}, so it is not a whole Keras model. " + SAVE_AGAIN
    )


@pytest.mark.parametrize(
    "file_max",
    [
        30_000,  # the fixture unpacks to 145 KB, more than 4 x 30 KB
        60_000,  # its weights alone are 139 KB, more than 2 x 60 KB
    ],
    ids=["all-files", "weights"],
)
def test_a_zip_bomb_fails_opens(file_max: int, scratch: Path) -> None:
    assert opens_fails(fixture("functional"), scratch, file_max) == (
        "The file unpacks to more than 0 MB, more than any model CandleStack takes. "
        "Upload a smaller model."
    )
    assert not scratch.exists()  # nothing was unpacked


def test_the_limits() -> None:
    assert inspect.JSON_MAX == 1024 * 1024
    assert inspect.ENTRIES_MAX == 1000
    assert inspect.CENTRAL_DIRECTORY_MAX == 1024 * 1024
    assert inspect.HDF5_OBJECTS_MAX == 100_000


def _with_eocd(path: Path, **fields: int) -> Path:
    """`path` with fields of its end of central directory record replaced."""
    content = bytearray(path.read_bytes())
    at = content.rfind(b"PK\x05\x06")
    offsets = {"entries_on_disk": 8, "entries": 10, "directory_size": 12, "directory_at": 16}
    for name, value in fields.items():
        width = 2 if name.startswith("entries") else 4
        content[at + offsets[name] : at + offsets[name] + width] = value.to_bytes(width, "little")
    path.write_bytes(bytes(content))
    return path


def test_a_zip_with_too_many_entries_fails_before_its_index_is_read(
    tmp_path: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = rezip(tmp_path, put={f"assets/{number}": b"" for number in range(1001)})

    def never(*_args: object) -> None:
        raise AssertionError("zipfile read the index")

    monkeypatch.setattr(zipfile.ZipFile, "__init__", never)

    assert opens_fails(path, scratch) == (
        "The file holds far more files than a Keras model has. " + SAVE_AGAIN
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"entries": 600_000 % 65_535},  # what the record of 600 000 entries could say
        {"directory_size": 34 * 1024 * 1024},
        {"entries": 0xFFFF},
        {"entries_on_disk": 0xFFFF},
        {"directory_size": 0xFFFFFFFF},
        {"directory_at": 0xFFFFFFFF},
    ],
    ids=["many-entries", "huge-index", "zip64-entries", "zip64-disk", "zip64-size", "zip64-at"],
)
def test_a_forged_index_fails_opens(fields: dict[str, int], tmp_path: Path, scratch: Path):
    path = _with_eocd(rezip(tmp_path), **fields)

    assert opens_fails(path, scratch).startswith("The file holds far more files")


def test_a_zip64_locator_fails_opens(tmp_path: Path, scratch: Path) -> None:
    content = rezip(tmp_path).read_bytes()
    at = content.rfind(b"PK\x05\x06")
    locator = b"PK\x06\x07" + bytes(16)
    path = tmp_path / "zip64.keras"
    path.write_bytes(content[:at] + locator + content[at:])

    assert opens_fails(path, scratch).startswith("The file holds far more files")


def test_a_cut_end_record_fails_opens(tmp_path: Path, scratch: Path) -> None:
    path = tmp_path / "cut.keras"
    path.write_bytes(fixture("functional").read_bytes()[:-10])

    assert opens_fails(path, scratch).startswith("The file is not a readable Keras model.")


def test_the_entries_zipfile_reads_are_counted_too(
    tmp_path: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As if the end record had said fewer entries than the index holds.
    path = rezip(tmp_path, put={f"assets/{number}": b"" for number in range(20)})
    monkeypatch.setattr(inspect, "_check_index", lambda _path: None)
    monkeypatch.setattr(inspect, "ENTRIES_MAX", 10)

    assert opens_fails(path, scratch).startswith("The file holds far more files")


def test_a_huge_config_fails_opens(
    tmp_path: Path, scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(inspect, "JSON_MAX", 5_000)  # the config is 5540 bytes

    assert opens_fails(fixture("functional"), scratch).startswith(
        "The config.json of the file is larger than 0 MB"
    )


@pytest.mark.parametrize(
    "content",
    [b"{", b"[1, 2]", b"\xff\xfe{}", b"[" * 100_000 + b"]" * 100_000],
    ids=["broken", "a-list", "not-utf8", "nested-too-deep"],
)
@pytest.mark.parametrize("member", ["config.json", "metadata.json"])
def test_json_that_is_no_object_fails_opens(
    member: str, content: bytes, tmp_path: Path, scratch: Path
) -> None:
    path = rezip(tmp_path, put={member: content})

    assert (
        opens_fails(path, scratch) == f"The {member} of the file is not valid JSON. " + SAVE_AGAIN
    )


@pytest.mark.parametrize(
    "metadata", [{}, {"keras_version": ""}, {"keras_version": 3}, {"keras_version": "3." * 20}]
)
def test_metadata_without_a_keras_version_fails_opens(
    metadata: dict[str, Any], tmp_path: Path, scratch: Path
) -> None:
    path = rezip(tmp_path, put={"metadata.json": json.dumps(metadata).encode()})

    assert opens_fails(path, scratch).startswith(
        "The file does not say which Keras version saved it."
    )


def _subclassed(config: Config) -> None:
    config.update(class_name="TradingModel", module="my_models", registered_name="Custom>X")


@pytest.mark.parametrize(
    "edit",
    [
        _subclassed,
        lambda config: config.update(module="kerasx.models"),
        lambda config: config.update(module=None),
        lambda config: config.update(config=[]),
        lambda config: config.update(class_name="Model"),
    ],
    ids=["subclassed", "not-keras", "no-module", "no-config", "plain-model"],
)
def test_anything_but_a_functional_or_sequential_model_fails_opens(
    edit: Callable[[Config], None], tmp_path: Path, scratch: Path
) -> None:
    path = with_config(tmp_path, edit)

    assert opens_fails(path, scratch).startswith(
        "The file holds no Functional or Sequential Keras model."
    )


def _from_package(config: Config) -> None:
    layer(config, "dense")["module"] = "my_package.layers"


def _registered_initializer(config: Config) -> None:
    layer(config, "dense")["config"]["kernel_initializer"]["registered_name"] = "Custom>Init"


def _tf_op(config: Config) -> None:
    layer(config, "flatten")["class_name"] = "TFOpLambda"


def _keras2_layer(config: Config) -> None:
    dense = layer(config, "dense")
    del dense["name"]  # Keras 2 names a layer in its config only
    dense["module"] = "evil"


@pytest.mark.parametrize(
    ("edit", "detail"),
    [
        (_from_package, 'The layer "dense" uses custom code (Dense)'),
        (_registered_initializer, 'The layer "dense" uses custom code (GlorotUniform)'),
        (_tf_op, 'The layer "flatten" uses custom code (TFOpLambda)'),
        (_keras2_layer, 'The layer "dense" uses custom code (Dense)'),
    ],
    ids=["another-package", "registered", "tf-op", "keras-2-layer"],
)
def test_custom_code_in_a_layer_fails_opens(
    edit: Callable[[Config], None], detail: str, tmp_path: Path, scratch: Path
) -> None:
    assert opens_fails(with_config(tmp_path, edit), scratch).startswith(detail + ", which")


def test_custom_code_outside_the_layers_names_the_model(tmp_path: Path, scratch: Path) -> None:
    def edit(config: Config) -> None:
        config["config"]["dtype"] = {"class_name": "\x00Policy\n", "module": "evil", "config": {}}

    assert opens_fails(with_config(tmp_path, edit), scratch).startswith(
        "The model uses custom code (Policy), which"
    )


@pytest.mark.parametrize("class_name", ["\n", 5])
def test_an_object_without_a_shown_name(class_name: object, tmp_path: Path, scratch: Path):
    def edit(config: Config) -> None:
        dense = layer(config, "dense")
        dense.update(name="\x07", class_name=class_name, module="evil")

    assert opens_fails(with_config(tmp_path, edit), scratch).startswith(
        "The model uses custom code (an object without a name), which"
    )


def test_what_only_shapes_or_reports_training_is_ignored(tmp_path: Path, scratch: Path) -> None:
    custom = {"module": None, "class_name": "Mine", "registered_name": "Custom>Mine"}

    def edit(config: Config) -> None:
        dense = layer(config, "dense")["config"]
        dense["kernel_regularizer"] = custom
        dense["bias_constraint"] = custom
        config["compile_config"]["loss"] = custom

    assert check(with_config(tmp_path, edit), scratch).parameters == 9325


# Objects that make a load run code or touch files, from the advisories of Keras.
GET_FILE = {
    "module": "keras.utils",
    "class_name": "function",
    "config": "get_file",
}  # CVE-2025-8747
UNSAFE = {  # CVE-2025-9906
    "module": "keras.config",
    "class_name": "function",
    "config": "enable_unsafe_deserialization",
}
TFSM_LAYER = {
    "module": "keras.layers",
    "class_name": "TFSMLayer",
    "config": {"filepath": "/etc", "call_endpoint": "serve"},
    "registered_name": None,
}
DESERIALIZE = {"module": "builtins", "class_name": "function", "config": "deserialize"}
OFF_THE_LIST = {"module": "keras.saving", "class_name": "KerasFileEditor", "config": {}}


def _as_activation(payload: Config) -> Callable[[Config], None]:
    def edit(config: Config) -> None:
        layer(config, "dense")["config"]["activation"] = payload

    return edit


def _as_loss(payload: Config) -> Callable[[Config], None]:
    def edit(config: Config) -> None:
        config["compile_config"]["loss"] = payload

    return edit


def _as_regularizer(payload: Config) -> Callable[[Config], None]:
    def edit(config: Config) -> None:
        layer(config, "dense")["config"]["kernel_regularizer"] = payload

    return edit


@pytest.mark.parametrize(
    ("payload", "shown"),
    [
        (GET_FILE, "get_file"),
        (UNSAFE, "enable_unsafe_deserialization"),
        (TFSM_LAYER, "TFSMLayer"),
        (DESERIALIZE, "deserialize"),
        (OFF_THE_LIST, "KerasFileEditor"),
        ({"class_name": "__lambda__", "config": {"code": "..."}}, "__lambda__"),
        ({"class_name": "function", "config": None}, "a function"),
    ],
    ids=[
        "get-file",
        "unsafe-deserialization",
        "tfsm-layer",
        "deserialize",
        "off-list",
        "lambda",
        "function-without-name",
    ],
)
@pytest.mark.parametrize("where", [_as_activation, _as_regularizer])
def test_code_outside_the_allowlist_fails_opens_wherever_it_is(
    payload: Config, shown: str, where: Callable[..., Callable[[Config], None]], tmp_path, scratch
) -> None:
    detail = opens_fails(with_config(tmp_path, where(payload)), scratch)

    assert f"uses custom code ({shown}), which" in detail


def test_a_tfsm_layer_among_the_layers_fails_opens(tmp_path: Path, scratch: Path) -> None:
    def edit(config: Config) -> None:
        config["config"]["layers"].insert(1, {**TFSM_LAYER, "name": "saved"})

    assert opens_fails(with_config(tmp_path, edit), scratch).startswith(
        'The layer "saved" uses custom code (TFSMLayer)'
    )


def test_a_registered_object_is_stubbed_only_where_training_uses_it(tmp_path, scratch) -> None:
    custom = {"module": "metrics", "class_name": "RankIC", "registered_name": "Custom>RankIC"}

    passes = check(with_config(tmp_path, _as_regularizer(custom)), scratch)
    detail = opens_fails(with_config(tmp_path, _as_activation(custom)), scratch)

    assert passes.parameters == 9325
    assert detail.startswith('The layer "dense" uses custom code (RankIC)')


@pytest.mark.parametrize(
    "payload",
    [
        {"module": "builtins", "class_name": "function", "config": "gelu"},
        {
            "module": "builtins",
            "class_name": "function",
            "config": "mean_squared_error",
            "registered_name": "function",
        },
        {"module": "keras.activations", "class_name": "function", "config": "relu"},
        {"class_name": "__numpy__", "config": {"value": [1.0], "dtype": "float32"}},
    ],
    ids=["builtin-activation", "builtin-loss", "keras-activation", "numpy-constant"],
)
def test_built_in_functions_and_values_pass(payload: Config, tmp_path: Path, scratch: Path):
    assert check(with_config(tmp_path, _as_activation(payload)), scratch).parameters == 9325


def test_a_function_with_a_registered_name_fails_opens(tmp_path: Path, scratch: Path) -> None:
    payload = {
        "module": "builtins",
        "class_name": "function",
        "config": "gelu",
        "registered_name": "Custom>gelu",
    }

    assert opens_fails(with_config(tmp_path, _as_activation(payload)), scratch).startswith(
        'The layer "dense" uses custom code (gelu)'
    )


# What the supervisor's models hold in their compile settings: an optimizer saved with the
# torch backend, and a metric function of the user's own.
TORCH_ADAM = {
    "module": "keras.src.backend.torch.optimizers.torch_adam",
    "class_name": "Adam",
    "config": {"name": "adam", "learning_rate": 0.001},
    "registered_name": "Adam",
}
SIGN_ACCURACY = {
    "module": "builtins",
    "class_name": "function",
    "config": "Custom>sign_accuracy",
    "registered_name": "function",
}


def test_the_compile_settings_of_the_supervisors_models_pass(tmp_path, scratch) -> None:
    def edit(config: Config) -> None:
        config["compile_config"].update(
            optimizer=TORCH_ADAM,
            loss={"module": "keras.losses", "class_name": "Huber", "config": {}},
            metrics=[
                "mse",
                SIGN_ACCURACY,
                {"module": "metrics", "class_name": "RankIC", "registered_name": "Custom>RankIC"},
            ],
        )

    assert check(with_config(tmp_path, edit), scratch).parameters == 9325


@pytest.mark.parametrize(
    "payload",
    [GET_FILE, UNSAFE, TFSM_LAYER, DESERIALIZE, OFF_THE_LIST, {"class_name": "__lambda__"}],
    ids=["get-file", "unsafe-deserialization", "tfsm-layer", "deserialize", "off-list", "lambda"],
)
def test_the_compile_settings_are_not_checked(payload: Config, tmp_path, scratch) -> None:
    # The worker removes them before it loads the file, so nothing in them runs.
    assert check(with_config(tmp_path, _as_loss(payload)), scratch).parameters == 9325


def test_compile_settings_of_a_model_inside_the_model_are_checked(tmp_path, scratch) -> None:
    # Only the model's own compile settings are removed before loading.
    def edit(config: Config) -> None:
        layer(config, "dense")["compile_config"] = {"loss": GET_FILE}

    assert opens_fails(with_config(tmp_path, edit), scratch).startswith(
        'The layer "dense" uses custom code (get_file)'
    )


def test_operations_between_layers_pass(scratch: Path) -> None:
    assert check(fixture("operations"), scratch) == ModelFacts("3.15.1", 409, [25, 23], [1])


def test_the_allowlist_holds_what_the_fixtures_name() -> None:
    assert "keras.layers.Dense" in allowlist.CLASSES
    assert "keras.src.ops.numpy.Add" in allowlist.CLASSES
    assert "keras.layers.Lambda" not in allowlist.CLASSES
    assert "keras.layers.TFSMLayer" not in allowlist.CLASSES
    assert "keras.src.ops.core.Cond" not in allowlist.CLASSES
    assert "keras.activations.gelu" in allowlist.FUNCTIONS
    assert allowlist.KERAS_VERSION == "3.15.1"


def test_weights_that_are_no_hdf5_fail_weights(tmp_path: Path, scratch: Path) -> None:
    path = rezip(tmp_path, put={"model.weights.h5": b"not HDF5"})

    failed = failure(path, scratch)

    assert failed.check == "weights"
    assert failed.detail == "The weights of the model (model.weights.h5) cannot be read. " + (
        SAVE_AGAIN
    )


def test_only_the_layers_count_not_the_optimizer(tmp_path: Path) -> None:
    weights = tmp_path / "model.weights.h5"
    with h5py.File(weights, "w") as file:
        file.create_dataset("optimizer/vars/0", data=[1.0, 2.0])
        file.create_group("layers")

    with pytest.raises(CheckFailed) as failed:
        count_parameters(weights)

    assert failed.value.check == "weights"
    assert failed.value.detail.startswith("The file holds the layers only.")


def test_weights_of_the_optimizer_only(tmp_path: Path) -> None:
    weights = tmp_path / "model.weights.h5"
    with h5py.File(weights, "w") as file:
        file.create_dataset("optimizer/vars/0", data=[1.0, 2.0])

    with pytest.raises(CheckFailed) as failed:
        count_parameters(weights)

    assert failed.value.detail.startswith("The file holds the layers only.")


def test_weights_without_a_layers_group(tmp_path: Path) -> None:
    weights = tmp_path / "model.weights.h5"
    with h5py.File(weights, "w") as file:
        file.create_dataset("layers", data=[1.0])  # a dataset, not the group of the layers

    with pytest.raises(CheckFailed):
        count_parameters(weights)


def test_more_weights_than_postgres_counts_fail_weights(tmp_path: Path) -> None:
    weights = tmp_path / "model.weights.h5"
    with h5py.File(weights, "w") as file:
        # Chunked and never written: the shape is metadata only, nothing is allocated.
        file.create_dataset("layers/dense/vars/0", shape=(2**62, 4), dtype="i1", chunks=(1, 4))

    with pytest.raises(CheckFailed) as failed:
        count_parameters(weights)

    assert failed.value.detail.startswith("The weights of the model (model.weights.h5) cannot")


def _soft_layers(file: h5py.File) -> None:
    file.create_dataset("elsewhere/vars/0", data=[1.0])
    file["layers"] = h5py.SoftLink("/elsewhere")


def _soft_below(file: h5py.File) -> None:
    file.create_dataset("layers/dense/vars/0", data=[1.0])
    file["layers/dense/vars/1"] = h5py.SoftLink("/layers/dense/vars/0")


def _external_below(file: h5py.File) -> None:
    file.create_dataset("layers/dense/vars/0", data=[1.0])
    file["layers/dense/vars/1"] = h5py.ExternalLink("/etc/passwd", "/")


@pytest.mark.parametrize(
    "build", [_soft_layers, _soft_below, _external_below], ids=["layers", "soft", "external"]
)
def test_links_in_the_weights_fail_weights(build: Callable[[h5py.File], None], tmp_path: Path):
    weights = tmp_path / "model.weights.h5"
    with h5py.File(weights, "w") as file:
        build(file)

    with pytest.raises(CheckFailed) as failed:
        count_parameters(weights)

    assert failed.value.check == "weights"
    assert failed.value.detail.startswith("The weights of the model (model.weights.h5) cannot")


def test_too_many_objects_in_the_weights_fail_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    weights = tmp_path / "model.weights.h5"
    with h5py.File(weights, "w") as file:
        for number in range(6):
            file.create_dataset(f"layers/dense_{number}/vars/0", data=[1.0])
    monkeypatch.setattr(inspect, "HDF5_OBJECTS_MAX", 10)  # 6 layers, 6 vars, 6 datasets

    with pytest.raises(CheckFailed) as failed:
        count_parameters(weights)

    assert failed.value.check == "weights"


def test_the_check_process_writes_the_facts(capsys: pytest.CaptureFixture[str], scratch: Path):
    assert (
        inspect.main([str(fixture("functional")), str(scratch), str(FILE_MAX)], limits=False) == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "facts": {
            "keras_version": "3.15.1",
            "parameters": 9325,
            "input_shape": [25, 23],
            "output_shape": [1],
        }
    }


def test_the_check_process_writes_a_failed_check(capsys: pytest.CaptureFixture[str], scratch):
    assert (
        inspect.main([str(fixture("no_weights")), str(scratch), str(FILE_MAX)], limits=False) == 0
    )

    failed = json.loads(capsys.readouterr().out)["failed"]
    assert failed["check"] == "weights"


def test_the_check_process_limits_itself_on_linux(
    capsys: pytest.CaptureFixture[str], scratch: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    limits: list[tuple[str, tuple[int, int]]] = []

    class FakeResource:
        RLIMIT_DATA, RLIMIT_CPU, RLIMIT_FSIZE, RLIMIT_CORE = "data", "cpu", "fsize", "core"

        @staticmethod
        def setrlimit(limit: str, values: tuple[int, int]) -> None:
            limits.append((limit, values))

    monkeypatch.setattr(inspect.sys, "platform", "linux")
    monkeypatch.setitem(sys.modules, "resource", FakeResource)

    inspect.main([str(fixture("functional")), str(scratch), "1000"], limits=True)

    assert limits == [
        ("data", (192 * 1024 * 1024, 192 * 1024 * 1024)),
        ("cpu", (15, 15)),
        ("fsize", (2000, 2000)),
        ("core", (0, 0)),
    ]
    assert "failed" in capsys.readouterr().out  # 1000 bytes is no room for a model


def _output(name: object) -> Callable[[Config], None]:
    def edit(config: Config) -> None:
        config["config"]["output_layers"] = name

    return edit


@pytest.mark.parametrize(
    ("edit", "shape"),
    [
        (_output(["flatten", 0, 0]), [575]),
        (_output(["batch_normalization", 0, 0]), [25, 23]),
        (_output([["dense_1", 0, 0]]), [1]),
        (_output(["dropout", 0, 0]), None),  # built without a build_config
        (_output(["input_layer", 0, 0]), None),  # no build_config either
        (_output([["dense", 0, 0], ["dense_1", 0, 0]]), None),  # two outputs
        (_output(["nowhere", 0, 0]), None),
        (_output("dense_1"), None),
    ],
    ids=[
        "flatten",
        "shape-keeping",
        "one-in-a-list",
        "no-build-config",
        "input-layer",
        "two-outputs",
        "unknown-layer",
        "malformed",
    ],
)
def test_the_output_shape(
    edit: Callable[[Config], None], shape: list[int] | None, tmp_path: Path, scratch: Path
) -> None:
    assert check(with_config(tmp_path, edit), scratch).output_shape == shape


def _units(units: object) -> Callable[[Config], None]:
    def edit(config: Config) -> None:
        layer(config, "dense_1")["config"]["units"] = units

    return edit


def _variable_length(config: Config) -> None:
    layer(config, "dense_1")["build_config"]["input_shape"] = [None, None]


def _no_dense_config(config: Config) -> None:
    layer(config, "dense_1")["config"] = None
    layer(config, "dense_1")["name"] = "dense_1"


def _not_plain(config: Config) -> None:
    layer(config, "dense_1")["class_name"] = "Conv1D"


@pytest.mark.parametrize(
    ("edit", "shape"),
    [
        (_units(4), [4]),
        (_units(True), None),
        (_units(0), None),
        (_no_dense_config, None),
        (_variable_length, None),
        (_not_plain, None),
    ],
    ids=["units", "bool-units", "no-units", "no-config", "unknown-size", "not-plain"],
)
def test_the_output_shape_of_the_output_layer(
    edit: Callable[[Config], None], shape: list[int] | None, tmp_path: Path, scratch: Path
) -> None:
    assert check(with_config(tmp_path, edit), scratch).output_shape == shape


def _batch_shape(shape: object, key: str = "batch_shape") -> Callable[[Config], None]:
    def edit(config: Config) -> None:
        settings = layer(config, "input_layer")["config"]
        del settings["batch_shape"]
        settings[key] = shape

    return edit


def _input_config(config: Config) -> None:
    layer(config, "input_layer")["config"] = "?"


@pytest.mark.parametrize(
    ("edit", "shape"),
    [
        (_batch_shape([None, 25, 23], "batch_input_shape"), [25, 23]),
        (_batch_shape([None, None, 23]), None),
        (_batch_shape([None]), None),
        (_batch_shape("25x23"), None),
        (_input_config, None),
    ],
    ids=["keras-2", "unknown-size", "no-dimension", "malformed", "no-config"],
)
def test_the_input_shape(
    edit: Callable[[Config], None], shape: list[int] | None, tmp_path: Path, scratch: Path
) -> None:
    assert check(with_config(tmp_path, edit), scratch).input_shape == shape


def test_a_sequential_of_an_input_only_has_no_output_shape(tmp_path: Path, scratch: Path):
    def edit(config: Config) -> None:
        config["config"]["layers"] = config["config"]["layers"][:1]

    facts = check(with_config(tmp_path, edit, "sequential"), scratch)

    assert facts.input_shape == [30, 5]
    assert facts.output_shape is None


def test_a_model_without_a_list_of_layers_has_no_shapes(tmp_path: Path, scratch: Path) -> None:
    def edit(config: Config) -> None:
        config["config"]["layers"] = {"not": "a list"}

    facts = check(with_config(tmp_path, edit, "sequential"), scratch)

    assert (facts.input_shape, facts.output_shape) == (None, None)


def test_what_is_no_layer_in_the_layers_is_passed_over(tmp_path: Path, scratch: Path) -> None:
    def edit(config: Config) -> None:
        config["config"]["layers"].insert(1, "not a layer")

    assert check(with_config(tmp_path, edit), scratch).output_shape == [1]


def test_a_sequential_without_layers_has_no_output_shape(tmp_path: Path, scratch: Path) -> None:
    def edit(config: Config) -> None:
        config["config"]["layers"] = []

    assert check(with_config(tmp_path, edit, "sequential"), scratch).output_shape is None
