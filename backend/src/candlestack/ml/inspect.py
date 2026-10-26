"""The static check of an uploaded Keras file: what the file holds, read without loading it.

A ``.keras`` file is a zip of ``config.json`` (the architecture), ``metadata.json`` (the Keras
version) and ``model.weights.h5`` (the weights, HDF5), with optional ``assets/``. Loading it
with Keras could run code from the file (a ``Lambda`` layer is pickled Python, a custom layer
is the user's class, a ``function`` object may name any Keras function), so the API never
imports Keras: it reads the JSON and the HDF5 metadata only, and accepts only what it knows to
be safe. Two checks, in order:

- ``opens``: a well-formed zip of a Functional or Sequential model whose every serialized
  object is a built-in Keras class or function of the allowlist (``allowlist.py``). The
  ``*_regularizer`` and ``*_constraint`` objects, which only shape training, may also be the
  user's own registered objects, which the worker replaces with stubs; never a Keras object off
  the allowlist or an unknown function. The model's ``compile_config`` (optimizer, loss,
  metrics) is not checked at all: the worker removes it before it loads the file, so nothing
  in it runs.
- ``weights``: the weights file reads as HDF5 and holds weights of the layers (not only the
  optimizer's state).

It runs in a child process with resource limits (``sandbox.py``), as ``main``: a damaged file
can then crash or exhaust only that process. Only the standard library, the allowlist and h5py
(imported when the weights are read, after the limits are set). The same process checks an
ONNX file (``inspect_onnx.py``, imported after the limits too).
"""

import json
import math
import re
import shutil
import struct
import sys
import unicodedata
import zipfile
import zlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from candlestack.ml.allowlist import CLASSES, FUNCTIONS

MIB = 1024 * 1024
CONFIG = "config.json"
METADATA = "metadata.json"
WEIGHTS = "model.weights.h5"
_MEMBERS = frozenset({CONFIG, METADATA, WEIGHTS})
_ASSETS = "assets/"
# The zip's index is read before anything else, so it is bounded first: a real model has a
# handful of files and assets.
ENTRIES_MAX = 1000
CENTRAL_DIRECTORY_MAX = MIB
JSON_MAX = MIB
# How many HDF5 objects the weights may have; a real model has a few per layer.
HDF5_OBJECTS_MAX = 100_000
# Postgres' bigint, the column the count goes to.
_PARAMETERS_MAX = 2**63 - 1
_KERAS_VERSION_MAX = 32

_MODEL_CLASSES = frozenset({"keras.src.models.functional.Functional", "keras.Sequential"})
# The model's compile settings, which the worker removes before it loads the file.
_COMPILE_CONFIG = "compile_config"
# Keys whose objects only shape training: the user's registered objects are stubbed there by
# the worker.
_TRAINING_ONLY_SUFFIXES = ("_regularizer", "_constraint")
# Values Keras serializes as objects of its own, without a module.
_INTERNAL = frozenset(
    {"__keras_tensor__", "__tensor__", "__numpy__", "__bytes__", "__ellipsis__", "__slice__"}
)
# Function names Keras looks up in its modules of built-ins (module ``builtins``).
_BUILTIN_FUNCTIONS = frozenset(name.rsplit(".", 1)[1] for name in FUNCTIONS)
# Layers whose output has the shape of their input.
_SHAPE_KEEPING = frozenset(
    {
        "Activation",
        "ActivityRegularization",
        "AlphaDropout",
        "BatchNormalization",
        "Dropout",
        "ELU",
        "GaussianDropout",
        "GaussianNoise",
        "GroupNormalization",
        "Identity",
        "LayerNormalization",
        "LeakyReLU",
        "Masking",
        "PReLU",
        "ReLU",
        "Softmax",
        "UnitNormalization",
    }
)
# The end of central directory record: its signature, then 18 bytes, then a comment of up to
# 64 KiB; zip64 adds a locator of 20 bytes before it.
_EOCD = b"PK\x05\x06"
_EOCD_SIZE = 22
_ZIP64_LOCATOR = b"PK\x06\x07"
_ZIP64_LOCATOR_SIZE = 20
_TAIL = 64 * 1024 + _EOCD_SIZE
# What a damaged or foreign zip raises while it is read.
_ZIP_ERRORS = (
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    NotImplementedError,
    RuntimeError,
    ValueError,
    struct.error,
)
# What h5py raises on a file that is not HDF5 or is damaged inside.
_HDF5_ERRORS = (OSError, KeyError, RuntimeError, ValueError, TypeError)
_SHOWN_MAX = 60

_SAVE_AGAIN = 'Save the model again with model.save("model.keras") and upload that file.'
_BROKEN = (
    "The file is not a readable Keras model. It may be damaged or not a .keras file at all. "
    + _SAVE_AGAIN
)
_TOO_MANY_FILES = "The file holds far more files than a Keras model has. " + _SAVE_AGAIN
_UNREADABLE_WEIGHTS = f"The weights of the model ({WEIGHTS}) cannot be read. " + _SAVE_AGAIN
_NO_WEIGHTS = "The file holds the layers only. Save the model after training and upload that file."


class CheckFailed(Exception):
    """A check failed: ``check`` names it (``opens`` or ``weights``), ``detail`` tells the user
    what is wrong with the file and what to do, in plain words."""

    def __init__(self, check: str, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ModelFacts:
    """What the check read from a file that passed it; an ONNX file has no Keras version."""

    keras_version: str | None
    parameters: int
    input_shape: list[int] | None
    output_shape: list[int] | None


def inspect_keras(path: Path, scratch: Path, *, file_max: int) -> ModelFacts:
    """Checks the Keras file at `path` without running anything from it and returns what it
    holds. `file_max` is the largest upload; the weights are unpacked to `scratch` (at most
    twice that) to be read, and the caller removes that file.

    Raises:
        CheckFailed: A check failed; its ``detail`` tells the user why.
    """
    _check_index(path)
    try:
        with zipfile.ZipFile(path) as archive:
            _check_members(archive, file_max)
            keras_version = _keras_version(_read_json(archive, METADATA))
            config = _read_json(archive, CONFIG)
            _check_architecture(config)
            # zipfile reads no more than the size the archive declares, which is capped above.
            with archive.open(WEIGHTS) as source, scratch.open("wb") as target:
                shutil.copyfileobj(source, target)
    except _ZIP_ERRORS as error:
        raise _opens(_BROKEN) from error
    parameters = count_parameters(scratch)
    layers = _layers(config)
    return ModelFacts(
        keras_version=keras_version,
        parameters=parameters,
        input_shape=_input_shape(layers),
        output_shape=_output_shape(config, layers),
    )


def _opens(detail: str) -> CheckFailed:
    return CheckFailed("opens", detail)


def _weights(detail: str) -> CheckFailed:
    return CheckFailed("weights", detail)


def _check_index(path: Path) -> None:
    """Fails unless the zip's index (its central directory) is small, read from the end of
    central directory record before zipfile reads the index whole: a few kilobytes of zip can
    declare a million entries. Zip64 is refused, no model needs it."""
    with path.open("rb") as file:
        size = file.seek(0, 2)
        file.seek(max(0, size - _TAIL))
        tail = file.read()
    at = tail.rfind(_EOCD)
    if at < 0 or len(tail) - at < _EOCD_SIZE:
        raise _opens(_BROKEN)
    _, _, on_disk, entries, directory_size, directory_at, _ = struct.unpack(
        "<HHHHIIH", tail[at + 4 : at + _EOCD_SIZE]
    )
    zip64 = (
        0xFFFF in (on_disk, entries)
        or 0xFFFFFFFF in (directory_size, directory_at)
        or tail[max(0, at - _ZIP64_LOCATOR_SIZE) : at].startswith(_ZIP64_LOCATOR)
    )
    if zip64 or entries > ENTRIES_MAX or directory_size > CENTRAL_DIRECTORY_MAX:
        raise _opens(_TOO_MANY_FILES)


def _check_members(archive: zipfile.ZipFile, file_max: int) -> None:
    """Fails unless the zip holds the three files of a Keras model, nothing outside it and
    nothing but assets besides, and unpacks to a size a model may have."""
    infos = archive.infolist()
    if len(infos) > ENTRIES_MAX:
        raise _opens(_TOO_MANY_FILES)
    for info in infos:
        name = info.filename
        if (
            name.startswith(("/", "\\"))
            or re.match(r"^[A-Za-z]:", name)
            or ".." in PurePosixPath(name.replace("\\", "/")).parts
        ):
            raise _opens("The file holds a path that points outside the model. " + _SAVE_AGAIN)
        if name not in _MEMBERS and not name.startswith(_ASSETS):
            raise _opens("The file holds files a Keras model does not have. " + _SAVE_AGAIN)
    missing = sorted(_MEMBERS - {info.filename for info in infos})
    if missing:
        raise _opens(
            f"The file has no {missing[0]}, so it is not a whole Keras model. " + _SAVE_AGAIN
        )
    # A zip bomb unpacks to far more than it weighs; no real model comes near these.
    if (
        sum(info.file_size for info in infos) > 4 * file_max
        or archive.getinfo(WEIGHTS).file_size > 2 * file_max
    ):
        raise _opens(
            f"The file unpacks to more than {4 * file_max // MIB} MB, more than any model "
            "CandleStack takes. Upload a smaller model."
        )


def _read_json(archive: zipfile.ZipFile, member: str) -> dict[str, Any]:
    """The JSON object in `member`."""
    if archive.getinfo(member).file_size > JSON_MAX:
        raise _opens(
            f"The {member} of the file is larger than {JSON_MAX // MIB} MB, more than any model "
            "CandleStack takes. Upload a smaller model."
        )
    try:
        document = json.loads(archive.read(member))
    except (ValueError, RecursionError):  # not JSON, not UTF-8, or nested beyond reason
        document = None
    if not isinstance(document, dict):
        raise _opens(f"The {member} of the file is not valid JSON. " + _SAVE_AGAIN)
    return document


def _keras_version(metadata: dict[str, Any]) -> str:
    version = metadata.get("keras_version")
    if not isinstance(version, str) or not version or len(version) > _KERAS_VERSION_MAX:
        raise _opens(
            "The file does not say which Keras version saved it. Save the model again with "
            'Keras 3 (model.save("model.keras")) and upload that file.'
        )
    return version


def _is_keras_module(module: object) -> bool:
    return isinstance(module, str) and (module == "keras" or module.startswith("keras."))


def _check_architecture(config: dict[str, Any]) -> None:
    """Fails unless `config` is a Functional or Sequential Keras model without custom code."""
    if f"{config.get('module')}.{config.get('class_name')}" not in _MODEL_CLASSES or not isinstance(
        config.get("config"), dict
    ):
        raise _opens(
            "The file holds no Functional or Sequential Keras model. A model of your own class "
            "cannot be checked without running its code. Build the model with keras.Model "
            "(inputs, outputs) or keras.Sequential and upload it again."
        )
    loaded = {key: value for key, value in config.items() if key != _COMPILE_CONFIG}
    if (detail := _custom_code(loaded)) is not None:
        raise _opens(detail)


def _custom_code(config: dict[str, Any]) -> str | None:
    """What tells the user about the first object of the architecture that is not on the
    allowlist, None without any.

    Walks the JSON with a stack of its own: a deeply nested file cannot exhaust Python's.
    Each entry is a node, the layer it belongs to and whether it only shapes training.
    """
    stack: list[tuple[Any, str | None, bool]] = [(config, None, False)]
    while stack:
        node, layer, training_only = stack.pop()
        if isinstance(node, list):
            stack.extend((item, layer, training_only) for item in reversed(node))
            continue
        if not isinstance(node, dict):
            continue
        verdict = _verdict(node, training_only=training_only)
        if verdict is _STUB:
            continue
        if verdict is not None:
            where = f'The layer "{layer}"' if layer else "The model"
            return (
                f"{where} uses custom code ({verdict}), which CandleStack does not run. "
                "Rebuild the model with the built-in Keras layers only and upload it again."
            )
        for key, value in reversed(node.items()):
            skipped = training_only or key.endswith(_TRAINING_ONLY_SUFFIXES)
            if key == "layers" and isinstance(value, list):
                stack.extend(
                    (item, _shown(_layer_name(item)) or layer, skipped) for item in value[::-1]
                )
            else:
                stack.append((value, layer, skipped))
    return None


class _Stub:
    """The verdict on a user's registered object where only training uses it."""


_STUB = _Stub()


def _verdict(node: dict[str, Any], *, training_only: bool) -> str | _Stub | None:
    """None for a dict that is no serialized object or one of the allowlist, ``_STUB`` for the
    user's registered object where only training uses it (not walked further), else what to
    name in the detail."""
    class_name = node.get("class_name")
    if class_name is None:
        return None
    module = node.get("module")
    registered = node.get("registered_name")
    shown = _shown(class_name) or "an object without a name"
    if class_name == "function":
        name = node.get("config")
        allowed = (
            isinstance(name, str)
            and registered in (None, "function")
            and (
                (module == "builtins" and name in _BUILTIN_FUNCTIONS)
                or f"{module}.{name}" in FUNCTIONS
            )
        )
        return None if allowed else (_shown(name) or "a function")
    if module is None and registered is None and class_name in _INTERNAL:
        return None
    if _is_keras_module(module):
        allowed = f"{module}.{class_name}" in CLASSES and registered in (None, class_name)
        return None if allowed else shown
    # The user's own object, registered with Keras as ``package>name``.
    if training_only and isinstance(registered, str) and ">" in registered:
        return _STUB
    return shown


def _shown(value: object) -> str | None:
    """`value` as a detail may show it: a string without control or format characters, cut
    short."""
    if not isinstance(value, str):
        return None
    kept = "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cf"})
    return kept.strip()[:_SHOWN_MAX] or None


def _layer_name(layer: object) -> object:
    """The name of a serialized layer: on the layer in Keras 3, in its config before."""
    if not isinstance(layer, dict):
        return None
    if "name" in layer:
        return layer["name"]
    config = layer.get("config")
    return config.get("name") if isinstance(config, dict) else None


def count_parameters(weights: Path) -> int:
    """The number of weights of the layers in the HDF5 file `weights`: the sizes of the
    datasets under ``layers``, not ``optimizer`` (the optimizer's state). Reads the metadata
    of the datasets, never their data, follows hard links only (a soft or external link could
    point anywhere, a file on the server included) and visits at most ``HDF5_OBJECTS_MAX``
    objects.

    Raises:
        CheckFailed: The file cannot be read as HDF5, has links or too many objects, or holds
            no weights.
    """
    import h5py  # noqa: PLC0415 (in the check's process only, after its limits are set)

    try:
        with h5py.File(weights, "r") as file:
            parameters = _sizes_under(file, "layers", h5py)
    except _HDF5_ERRORS as error:
        raise _weights(_UNREADABLE_WEIGHTS) from error
    if parameters == 0:
        raise _weights(_NO_WEIGHTS)
    if parameters > _PARAMETERS_MAX:
        raise _weights(_UNREADABLE_WEIGHTS)
    return parameters


def _sizes_under(file: Any, name: str, h5py: Any) -> int:
    """The total size of the datasets under the group `name` of `file`, 0 without one.

    Raises:
        CheckFailed: A soft or external link, or too many objects.
    """
    if name not in file:
        return 0
    top = _hard_linked(file, name, h5py)
    total = 0
    visited = 0
    groups = [top] if isinstance(top, h5py.Group) else []
    while groups:
        for key in (group := groups.pop()):
            visited += 1
            if visited > HDF5_OBJECTS_MAX:
                raise _weights(_UNREADABLE_WEIGHTS)
            item = _hard_linked(group, key, h5py)
            if isinstance(item, h5py.Group):
                groups.append(item)
            elif isinstance(item, h5py.Dataset):
                total += item.size
    return total


def _hard_linked(group: Any, key: str, h5py: Any) -> Any:
    """The object `key` of `group`, when a hard link names it.

    Raises:
        CheckFailed: A soft or external link names it.
    """
    if not isinstance(group.get(key, getlink=True), h5py.HardLink):
        raise _weights(_UNREADABLE_WEIGHTS)
    return group[key]


def _layers(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The model's own layers (not those of models nested in it)."""
    layers = config["config"].get("layers")
    if not isinstance(layers, list):
        return []
    return [layer for layer in layers if isinstance(layer, dict)]


def _sample_shape(shape: object) -> list[int] | None:
    """A shape with its batch dimension first, without it; None unless every other dimension
    is a known size."""
    if not isinstance(shape, list) or len(shape) < 2:
        return None
    dims = shape[1:]
    if all(isinstance(dim, int) and not isinstance(dim, bool) and dim > 0 for dim in dims):
        return dims
    return None


def _input_shape(layers: list[dict[str, Any]]) -> list[int] | None:
    """The shape of one input sample, when the model has exactly one input."""
    inputs = [layer for layer in layers if layer.get("class_name") == "InputLayer"]
    if len(inputs) != 1 or not isinstance(config := inputs[0].get("config"), dict):
        return None
    return _sample_shape(config.get("batch_shape", config.get("batch_input_shape")))


def _output_layer(config: dict[str, Any], layers: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The layer that makes the model's one output: a Sequential's last, a Functional's
    ``output_layers`` when it names one layer."""
    if config["class_name"] == "Sequential":
        last = layers[-1] if layers else None
        return last if last is not None and last.get("class_name") != "InputLayer" else None
    outputs = config["config"].get("output_layers")
    # One output is [layer, node, tensor], or a list of just that; more are a longer list.
    if isinstance(outputs, list) and len(outputs) == 1:
        outputs = outputs[0]
    if not isinstance(outputs, list) or len(outputs) != 3 or not isinstance(outputs[0], str):
        return None
    named = [layer for layer in layers if _layer_name(layer) == outputs[0]]
    return named[0] if len(named) == 1 else None


def _output_shape(config: dict[str, Any], layers: list[dict[str, Any]]) -> list[int] | None:
    """The shape of one output, from the input shape the output layer was built with; None
    for a layer whose output shape is not plain from it."""
    layer = _output_layer(config, layers)
    build = layer.get("build_config") if layer is not None else None
    shape = _sample_shape(build.get("input_shape")) if isinstance(build, dict) else None
    if layer is None or shape is None:
        return None
    kind = layer.get("class_name")
    if kind == "Dense":
        settings = layer.get("config")
        units = settings.get("units") if isinstance(settings, dict) else None
        if isinstance(units, int) and not isinstance(units, bool) and units > 0:
            return [*shape[:-1], units]
        return None
    if kind == "Flatten":
        return [math.prod(shape)]
    return shape if kind in _SHAPE_KEEPING else None


def limit_resources(resource: Any, *, file_max: int) -> None:
    """Limits this process (Linux): memory, CPU time and the size of a file it writes (the
    weights unpacked); past them the kernel stops it, which fails the check. No core dump: a
    crash must not write the process' memory to the disk."""
    for limit, value in (
        # What the backend's container has left next to the app (infra/README.md, "Memory").
        (resource.RLIMIT_DATA, 192 * MIB),
        (resource.RLIMIT_CPU, 15),
        (resource.RLIMIT_FSIZE, 2 * file_max),
        (resource.RLIMIT_CORE, 0),
    ):
        resource.setrlimit(limit, (value, value))


def _inspect(path: Path, scratch: Path, *, file_max: int, framework: str) -> ModelFacts:
    if framework == "onnx":
        # onnx and protobuf only in a check of an ONNX file, after the limits are set.
        from candlestack.ml.inspect_onnx import inspect_onnx  # noqa: PLC0415

        return inspect_onnx(path)
    return inspect_keras(path, scratch, file_max=file_max)


def main(argv: Sequence[str], *, limits: bool) -> int:
    """The check's process: ``<file> <scratch> <file_max> <framework>`` (``keras`` or
    ``onnx``); writes one JSON object to stdout, ``{"facts": {...}}`` or
    ``{"failed": {"check": ..., "detail": ...}}``."""
    path, scratch, file_max, framework = Path(argv[0]), Path(argv[1]), int(argv[2]), argv[3]
    # Only the check's own process is limited (`limits`), never one that imports this module
    # to call it, such as the tests.
    if limits and sys.platform == "linux":
        import resource  # noqa: PLC0415 (Linux only)

        limit_resources(resource, file_max=file_max)
    try:
        facts = _inspect(path, scratch, file_max=file_max, framework=framework)
        result: dict[str, Any] = {"facts": asdict(facts)}
    except CheckFailed as failed:
        result = {"failed": {"check": failed.check, "detail": failed.detail}}
    sys.stdout.write(json.dumps(result))
    return 0
