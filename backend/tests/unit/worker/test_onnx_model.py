"""Running an ONNX file in the job's process: the predictions of the raw-candles model of
tests/fixtures/onnx against the same arithmetic in numpy, and files onnxruntime must refuse."""

from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from candlestack.worker.onnx_model import load_onnx, session_options

FIXTURE = Path(__file__).parents[2] / "fixtures" / "onnx" / "raw_candles.onnx"


def reference(samples: np.ndarray) -> np.ndarray:
    """What the raw-candles model computes: the candles flattened, times its weights, plus its
    bias, through tanh."""
    weights = {
        tensor.name: numpy_helper.to_array(tensor)
        for tensor in onnx.load(FIXTURE).graph.initializer
    }
    flat = samples.reshape(len(samples), -1).astype(np.float64)
    return np.tanh(flat @ weights["weights"] + weights["bias"])


@pytest.fixture
def samples() -> np.ndarray:
    rng = np.random.default_rng(5)
    return (100 + rng.random((70, 25, 5)) * 10).astype(np.float32)


@pytest.mark.parametrize("batch_size", [1024, 16, 1])
def test_the_predictions_are_the_models_arithmetic(
    samples: np.ndarray, batch_size: int, tmp_path: Path
) -> None:
    model = load_onnx(FIXTURE, tmp_path)

    output = model.predict(samples, batch_size=batch_size)

    assert (output.shape, output.dtype) == ((70, 1), np.float32)
    np.testing.assert_allclose(output, reference(samples), atol=1e-5)
    assert np.ptp(output) > 0.001  # the samples give different scores


def test_samples_of_another_type_are_given_as_float32(samples: np.ndarray, tmp_path: Path) -> None:
    model = load_onnx(FIXTURE, tmp_path)

    output = model.predict(samples.astype(np.float64).tolist(), batch_size=32)

    np.testing.assert_allclose(output, reference(samples), atol=1e-5)


def test_the_session_runs_on_one_thread() -> None:
    options = session_options()

    assert (options.intra_op_num_threads, options.inter_op_num_threads) == (1, 1)


def test_external_data_next_to_the_file_is_not_found(tmp_path: Path) -> None:
    # The upload's check refuses such a file; were one kept, the copy the worker loads stands
    # alone, so the data next to the original is out of its reach.
    models, scratch = tmp_path / "models", tmp_path / "scratch"
    models.mkdir()
    scratch.mkdir()
    model = onnx.load(FIXTURE)
    onnx.save_model(
        model,
        models / "model.onnx",
        save_as_external_data=True,
        all_tensors_to_one_file=True,
        location="weights.bin",
        size_threshold=0,
    )
    assert (models / "weights.bin").exists()

    with pytest.raises(Exception, match="external"):
        load_onnx(models / "model.onnx", scratch)


def test_a_model_of_two_inputs_is_refused(tmp_path: Path) -> None:
    graph = helper.make_graph(
        [helper.make_node("Add", ["a", "b"], ["output"])],
        "two",
        [
            helper.make_tensor_value_info("a", TensorProto.FLOAT, ["batch", 1]),
            helper.make_tensor_value_info("b", TensorProto.FLOAT, ["batch", 1]),
        ],
        [helper.make_tensor_value_info("output", TensorProto.FLOAT, ["batch", 1])],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 17)])
    model.ir_version = 8
    path = tmp_path / "two.onnx"
    onnx.save_model(model, path)

    with pytest.raises(ValueError, match="one input and one output"):
        load_onnx(path, tmp_path)
