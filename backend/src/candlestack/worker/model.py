"""Loads an uploaded Keras file as safely as Keras allows (docs/architecture.md, "Models").

The upload's check read the file without loading it and let through only built-in Keras
objects of its allowlist, and the user's own registered objects where they only shape training
(``*_regularizer``, ``*_constraint``). Loading is the second line:

- the model's ``compile_config`` (optimizer, loss, metrics), which the check does not look at,
  is removed from a copy of the file, and the copy is loaded with ``compile=False``, so nothing
  of it is ever deserialized;
- ``safe_mode=True`` refuses a ``Lambda`` layer's pickled Python;
- the user's regularizers and constraints become stubs that do nothing (a regularizer adds no
  loss, a constraint returns its input), as running a model needs neither;
- any other object Keras does not know fails the load.
"""

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any, override

import keras

CONFIG = "config.json"
_COMPILE_CONFIG = "compile_config"
_TRAINING_ONLY = ("_regularizer", "_constraint")
# The size of config.json the upload's check allows.
_CONFIG_MAX = 1024 * 1024


class _NoRegularizer(keras.regularizers.Regularizer):
    """Stands in for a user's regularizer: training only, so it adds nothing."""

    @override
    def __call__(self, x: Any) -> Any:
        return keras.ops.zeros(())

    @override
    def get_config(self) -> dict[str, Any]:
        return {}

    @classmethod
    @override
    def from_config(cls, config: dict[str, Any]) -> "_NoRegularizer":
        return cls()


class _NoConstraint(keras.constraints.Constraint):
    """Stands in for a user's constraint: training only, so the weights stay as they are."""

    @override
    def __call__(self, w: Any) -> Any:
        return w

    @override
    def get_config(self) -> dict[str, Any]:
        return {}

    @classmethod
    @override
    def from_config(cls, config: dict[str, Any]) -> "_NoConstraint":
        return cls()


def load_keras(path: Path, scratch: Path) -> keras.Model:
    """The model of the ``.keras`` file at ``path``; its copy goes to ``scratch``, which the
    caller removes.

    Raises:
        Exception: Whatever Keras raises on a file it cannot load (the caller says so to the
            user), or ``ValueError`` when the file is no Keras file.
    """
    copy = scratch / "model.keras"
    config = _copy_without_compile_config(path, copy)
    return keras.saving.load_model(
        copy, custom_objects=training_only_stubs(config), compile=False, safe_mode=True
    )


def _copy_without_compile_config(path: Path, copy: Path) -> dict[str, Any]:
    """Copies the zip member by member, ``config.json`` without its ``compile_config``;
    returns that config."""
    config: dict[str, Any] | None = None
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(copy, "w") as target:
        for info in source.infolist():
            if info.filename == CONFIG:
                if info.file_size > _CONFIG_MAX:
                    raise ValueError(f"{CONFIG} is larger than a model's")
                config = json.loads(source.read(info))
                if not isinstance(config, dict):
                    raise ValueError(f"{CONFIG} holds no model")
                config.pop(_COMPILE_CONFIG, None)
                target.writestr(CONFIG, json.dumps(config))
                continue
            with source.open(info) as reading, target.open(info.filename, "w") as writing:
                shutil.copyfileobj(reading, writing)
    if config is None:
        raise ValueError(f"The file has no {CONFIG}")
    return config


# What stands in for a user's object: a regularizer or a constraint that does nothing.
Stub = type[_NoRegularizer] | type[_NoConstraint]


def training_only_stubs(config: dict[str, Any]) -> dict[str, Stub]:
    """A stub for each user's registered object (``package>name``) under a
    ``*_regularizer`` or ``*_constraint`` key of the architecture, by its registered name.

    Walks the JSON with a stack of its own, as the upload's check does: a deeply nested file
    cannot exhaust Python's.
    """
    stubs: dict[str, Stub] = {}
    stack: list[Any] = [config]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(node)
            continue
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            stub = _stub_for(key, value)
            if stub is not None:
                stubs[value["registered_name"]] = stub
            else:
                stack.append(value)
    return stubs


def _stub_for(key: object, value: object) -> Stub | None:
    if not isinstance(key, str) or not isinstance(value, dict):
        return None
    registered = value.get("registered_name")
    if not isinstance(registered, str) or ">" not in registered:
        return None
    if key.endswith(_TRAINING_ONLY[0]):
        return _NoRegularizer
    if key.endswith(_TRAINING_ONLY[1]):
        return _NoConstraint
    return None
