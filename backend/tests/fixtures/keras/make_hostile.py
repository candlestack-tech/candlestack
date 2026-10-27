"""Builds the hostile .keras files the upload check must refuse, into ``hostile/``.

Each is a real model of this folder (``raw_candles.keras``, the model of the manifest without
preprocessing, and ``lambda_layer.keras``) taken apart
and put together again with one harmful change, so the check meets what an attacker would
upload rather than a broken file. Only the standard library is needed, so this runs from
``backend/``:

    uv run python tests/fixtures/keras/make_hostile.py

The files are small: the bombs compress their zeros with bzip2, which zip allows and zipfile
reads, so a few hundred bytes declare hundreds of megabytes. To build one file only, call its
function instead, so the other files stay as they are.
"""

import json
import struct
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
OUT = HERE / "hostile"
MIB = 1024 * 1024
# The largest upload the API takes (``FILE_MAX_BYTES``): the bombs unpack past four times it.
FILE_MAX = 50 * MIB
# A symlink in a zip: the file type of Unix in the high bits of its external attributes.
_SYMLINK = 0o120777 << 16


def members(name: str = "raw_candles") -> dict[str, bytes]:
    with zipfile.ZipFile(HERE / f"{name}.keras") as archive:
        return {info.filename: archive.read(info) for info in archive.infolist()}


def config(name: str = "raw_candles") -> dict[str, Any]:
    return json.loads(members(name)["config.json"])


def member(name: str, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    """A member with a fixed date and system, so a file built again is the same bytes on any OS
    (zipfile takes the system from the OS that writes the file)."""
    info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
    info.create_system = 3  # Unix, as Keras on Linux writes it, on any OS that builds the file
    info.compress_type = compress_type
    return info


def write(name: str, files: Mapping[str, bytes | tuple[zipfile.ZipInfo, bytes]]) -> None:
    """Writes ``hostile/<name>.keras``: plain members deflated as Keras does, or a ZipInfo with
    its own attributes and content."""
    OUT.mkdir(exist_ok=True)
    with zipfile.ZipFile(OUT / f"{name}.keras", "w") as archive:
        for filename, content in files.items():
            info, data = content if isinstance(content, tuple) else (member(filename), content)
            archive.writestr(info, data)


def with_config(edited: dict[str, Any]) -> dict[str, bytes]:
    return {**members(), "config.json": json.dumps(edited).encode()}


def dense(model: dict[str, Any]) -> dict[str, Any]:
    return next(layer for layer in model["config"]["layers"] if layer["class_name"] == "Dense")


def zip_bomb() -> None:
    """The weights unpack to 201 MiB of zeros, past four times the largest upload."""
    files = members()
    info = member("model.weights.h5", zipfile.ZIP_BZIP2)
    write("zip_bomb", {**files, info.filename: (info, bytes(4 * FILE_MAX + MIB))})


def zip64() -> None:
    """A model whose zip has the end records of zip64, which no model needs: the end of central
    directory points to a zip64 record that holds the real counts."""
    write("zip64", members())
    path = OUT / "zip64.keras"
    content = path.read_bytes()
    at = content.rfind(b"PK\x05\x06")
    entries, size, offset = struct.unpack("<HII", content[at + 10 : at + 20])
    record = struct.pack(
        "<4sQHHIIQQQQ", b"PK\x06\x06", 44, 45, 45, 0, 0, entries, entries, size, offset
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, at, 1)
    end = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 0xFFFF, 0xFFFF, size, offset, 0)
    path.write_bytes(content[:at] + record + locator + end)


def path_traversal() -> None:
    """An asset whose name climbs out of the folder it would be unpacked to."""
    write("path_traversal", {**members(), "assets/../../../tmp/evil.py": b"print(1)"})


def symlink() -> None:
    """An asset that is a symlink to a file of the server: unpacked by a tool that keeps links,
    reading the asset would read that file."""
    info = member("assets/vocabulary.txt")
    info.external_attr = _SYMLINK
    write("symlink", {**members(), info.filename: (info, b"/etc/passwd")})


def huge_config() -> None:
    """A config.json past 1 MB: a real config padded with spaces, which deflate to almost
    nothing."""
    text = json.dumps(config()) + " " * (2 * MIB)
    write("huge_config", {**members(), "config.json": text.encode()})


def nested_lambda() -> None:
    """A Lambda layer (pickled Python) hidden in a model nested in the model, one level below
    the layers the check sees first."""
    inner = config("lambda_layer")
    inner["name"] = "encoder"
    inner["config"]["name"] = "encoder"
    outer = config()
    outer["config"]["layers"].insert(1, inner)
    write("nested_lambda", with_config(outer))


def keras_function(module: str, name: str) -> dict[str, Any]:
    """A function as Keras serializes one by name, for example a layer's activation."""
    return {"module": module, "class_name": "function", "config": name, "registered_name": None}


def get_file() -> None:
    """The activation of a layer is ``keras.utils.get_file``, which downloads any URL."""
    edited = config()
    dense(edited)["config"]["activation"] = keras_function("keras.utils", "get_file")
    write("get_file", with_config(edited))


def unsafe_deserialization() -> None:
    """The activation is ``keras.config.enable_unsafe_deserialization``: called while the model
    loads, it would let a later Lambda layer run."""
    edited = config()
    dense(edited)["config"]["activation"] = keras_function(
        "keras.config", "enable_unsafe_deserialization"
    )
    write("unsafe_deserialization", with_config(edited))


if __name__ == "__main__":
    for build in (
        zip_bomb,
        zip64,
        path_traversal,
        symlink,
        huge_config,
        nested_lambda,
        get_file,
        unsafe_deserialization,
    ):
        build()
    for path in sorted(OUT.glob("*.keras")):
        sys.stdout.write(f"{path.name}: {path.stat().st_size} bytes\n")
