"""Builds the small .onnx file the upload check and the worker are tested with.

onnx is a dependency of the backend, so this runs from `backend/`:

    uv run python tests/fixtures/onnx/make_models.py

`raw_candles.onnx` takes the last 25 candles as they are (25 x 5, the manifest
`keras/raw_candles.manifest.json` of the Keras model) and gives one score in (-1, 1): the
candles flattened, times weights, plus a bias, through tanh. The weights are small and seeded,
so the file is the same between runs. A model that a hostile upload would be is built in the
tests themselves (tests/unit/ml/test_inspect_onnx.py).
"""

from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

OUT = Path(__file__).parent
ROWS, FEATURES = 25, 5
# The release of the standard operators the file imports, and its IR version: what
# onnxruntime has run for a long time.
OPSET = 17
IR_VERSION = 8


def raw_candles() -> onnx.ModelProto:
    rng = np.random.default_rng(7)
    weights = (rng.normal(0, 1, (ROWS * FEATURES, 1)) / 20_000).astype(np.float32)
    bias = np.array([0.05], dtype=np.float32)
    graph = helper.make_graph(
        [
            helper.make_node("Reshape", ["candles", "flat_shape"], ["flat"]),
            helper.make_node("MatMul", ["flat", "weights"], ["sum"]),
            helper.make_node("Add", ["sum", "bias"], ["score"]),
            helper.make_node("Tanh", ["score"], ["output"]),
        ],
        "raw_candles",
        [helper.make_tensor_value_info("candles", TensorProto.FLOAT, ["batch", ROWS, FEATURES])],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["batch", 1])],
        [
            numpy_helper.from_array(np.array([-1, ROWS * FEATURES], dtype=np.int64), "flat_shape"),
            numpy_helper.from_array(weights, "weights"),
            numpy_helper.from_array(bias, "bias"),
        ],
    )
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", OPSET)], producer_name="candlestack-tests"
    )
    model.ir_version = IR_VERSION
    onnx.checker.check_model(model)
    return model


if __name__ == "__main__":
    onnx.save_model(raw_candles(), OUT / "raw_candles.onnx")
