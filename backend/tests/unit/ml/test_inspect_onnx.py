"""The static check of uploaded ONNX files, on the raw-candles model of tests/fixtures/onnx and
on small models built here, hostile ones among them: external data, custom operators, more
than one input, other types, shapes the check cannot take, weights that are not there."""

import json
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper, numpy_helper

from candlestack.ml import inspect
from candlestack.ml.inspect import CheckFailed, ModelFacts
from candlestack.ml.inspect_onnx import AXIS_MAX, inspect_onnx

FIXTURES = Path(__file__).parents[2] / "fixtures" / "onnx"
EXPORT_AGAIN = "Export the model to ONNX again and upload that file."

# An axis as onnx.helper takes it: a size, a name (dynamic) or None (unknown).
Shape = list[int | str | None]


def tensor(
    name: str, elem_type: int = TensorProto.FLOAT, shape: Shape | None = None
) -> onnx.ValueInfoProto:
    return helper.make_tensor_value_info(
        name, elem_type, ["batch", 25, 5] if shape is None else shape
    )


def weights(name: str = "weights", shape: tuple[int, ...] = (5, 1)) -> TensorProto:
    return numpy_helper.from_array(np.full(shape, 0.1, dtype=np.float32), name)


def build(
    *,
    nodes: list[onnx.NodeProto] | None = None,
    inputs: list[onnx.ValueInfoProto] | None = None,
    outputs: list[onnx.ValueInfoProto] | None = None,
    initializers: list[TensorProto] | None = None,
    opsets: list[onnx.OperatorSetIdProto] | None = None,
) -> onnx.ModelProto:
    """A model of the mean of each feature over the window, times weights: [batch, 25, 5] in,
    [batch, 1] out; each part can be replaced."""
    graph = helper.make_graph(
        nodes
        or [
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["output"]),
        ],
        "model",
        [tensor("candles")] if inputs is None else inputs,
        [tensor("output", shape=["batch", 1])] if outputs is None else outputs,
        [weights()] if initializers is None else initializers,
    )
    model = helper.make_model(graph, opset_imports=opsets or [helper.make_opsetid("", 17)])
    model.ir_version = 8
    return model


def save(tmp_path: Path, model: onnx.ModelProto) -> Path:
    path = tmp_path / "model.onnx"
    path.write_bytes(model.SerializeToString())
    return path


def failure(tmp_path: Path, model: onnx.ModelProto | bytes) -> CheckFailed:
    path = tmp_path / "model.onnx"
    path.write_bytes(model if isinstance(model, bytes) else model.SerializeToString())
    with pytest.raises(CheckFailed) as failed:
        inspect_onnx(path)
    return failed.value


def opens_fails(tmp_path: Path, model: onnx.ModelProto | bytes) -> str:
    failed = failure(tmp_path, model)
    assert failed.check == "opens"
    return failed.detail


def test_the_raw_candles_model_passes() -> None:
    assert inspect_onnx(FIXTURES / "raw_candles.onnx") == ModelFacts(None, 128, [25, 5], [1])


def test_a_small_model_passes(tmp_path: Path) -> None:
    facts = inspect_onnx(save(tmp_path, build()))

    assert facts == ModelFacts(None, 5, [25, 5], [1])


def test_initializers_listed_among_the_inputs_are_no_inputs(tmp_path: Path) -> None:
    # As files of IR version 3 list them.
    model = build(inputs=[tensor("candles"), tensor("weights", shape=[5, 1])])

    assert inspect_onnx(save(tmp_path, model)).input_shape == [25, 5]


def test_the_classical_machine_learning_domain_passes(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["scaled"]),
            helper.make_node("Normalizer", ["scaled"], ["output"], domain="ai.onnx.ml", norm="MAX"),
        ],
        opsets=[helper.make_opsetid("", 17), helper.make_opsetid("ai.onnx.ml", 3)],
    )

    assert inspect_onnx(save(tmp_path, model)).output_shape == [1]


def test_weights_in_a_constant_count(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("Constant", [], ["weights"], value=weights(shape=(5, 1))),
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["output"]),
        ],
        initializers=[],
    )

    assert inspect_onnx(save(tmp_path, model)).parameters == 5


@pytest.mark.parametrize(
    "content",
    [b"", b"\x00\x01\x02", b"PK\x03\x04 a zip, as a .keras file", b"\xff" * 64],
    ids=["empty", "garbage", "zip", "ones"],
)
def test_a_file_that_is_no_onnx_model_fails_opens(content: bytes, tmp_path: Path) -> None:
    assert opens_fails(tmp_path, content) == (
        "The file is not a readable ONNX model. It may be damaged or not a .onnx file at all. "
        + EXPORT_AGAIN
    )


def test_a_model_without_a_graph_fails_opens(tmp_path: Path) -> None:
    model = onnx.ModelProto(ir_version=8)

    assert opens_fails(tmp_path, model).startswith("The file is not a readable ONNX model.")


def test_a_model_the_checker_refuses_fails_opens(tmp_path: Path) -> None:
    # A node reads a value nothing makes.
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["nowhere", "weights"], ["output"]),
        ]
    )

    assert opens_fails(tmp_path, model) == (
        "The file is not a valid ONNX model: the ONNX checker refused it. " + EXPORT_AGAIN
    )


def external(tensor: TensorProto) -> TensorProto:
    """`tensor` with its data in another file, as onnx saves a large model."""
    tensor.ClearField("raw_data")
    tensor.data_location = TensorProto.EXTERNAL
    # A path out of the model's directory, which onnx's own helper would refuse to write.
    for key, value in (("location", "../../../etc/passwd"), ("offset", "0"), ("length", "20")):
        entry = tensor.external_data.add()
        entry.key, entry.value = key, value
    return tensor


def test_external_data_fails_opens(tmp_path: Path) -> None:
    detail = opens_fails(tmp_path, build(initializers=[external(weights())]))

    assert detail.startswith("The model keeps its weights in separate files (external data).")


def test_external_data_in_a_nested_graph_fails_opens(tmp_path: Path) -> None:
    branch = helper.make_graph(
        [helper.make_node("Constant", [], ["picked"], value=external(weights(shape=(1,))))],
        "branch",
        [],
        [helper.make_tensor_value_info("picked", TensorProto.FLOAT, [1])],
    )
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["output"]),
            helper.make_node("Constant", [], ["yes"], value=helper.make_tensor("y", 9, [], [1])),
            helper.make_node("If", ["yes"], ["unused"], then_branch=branch, else_branch=branch),
        ]
    )

    assert opens_fails(tmp_path, model).startswith("The model keeps its weights in separate")


def test_an_external_data_entry_alone_fails_opens(tmp_path: Path) -> None:
    held = weights()
    entry = held.external_data.add()
    entry.key, entry.value = "location", "weights.bin"

    assert opens_fails(tmp_path, build(initializers=[held])).startswith("The model keeps")


def test_a_custom_operator_fails_opens(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node(
                "FusedMatMul", ["means", "weights"], ["output"], domain="com.microsoft"
            ),
        ],
        opsets=[helper.make_opsetid("", 17), helper.make_opsetid("com.microsoft", 1)],
    )

    assert opens_fails(tmp_path, model) == (
        'The model uses operators of the domain "com.microsoft" (custom operators), which '
        "CandleStack does not run. Export the model with the standard ONNX operators only and "
        "upload it again."
    )


def test_a_node_of_an_unknown_domain_fails_opens_even_unimported(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("Steal", ["means", "weights"], ["output"], domain="evil\u202e.ops"),
        ]
    )

    assert opens_fails(tmp_path, model).startswith(
        'The model uses operators of the domain "evil.ops"'
    )


def test_a_local_function_fails_opens(tmp_path: Path) -> None:
    function = helper.make_function(
        "local",
        "Mean",
        ["x"],
        ["y"],
        [helper.make_node("ReduceMean", ["x"], ["y"], axes=[1], keepdims=0)],
        [helper.make_opsetid("", 17)],
    )
    model = build(
        nodes=[
            helper.make_node("Mean", ["candles"], ["means"], domain="local"),
            helper.make_node("MatMul", ["means", "weights"], ["output"]),
        ],
        opsets=[helper.make_opsetid("", 17), helper.make_opsetid("local", 1)],
    )
    model.functions.append(function)

    assert opens_fails(tmp_path, model).startswith('The model uses operators of the domain "local"')


def scaled(function: onnx.FunctionProto) -> onnx.ModelProto:
    """The small model with its MatMul replaced by `function`, a local function of the
    standard domain named Scale."""
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("Scale", ["means"], ["output"]),
        ]
    )
    model.functions.append(function)
    return model


def test_external_data_in_a_local_function_fails_opens(tmp_path: Path) -> None:
    # onnxruntime inlines the function and reads the file the Constant names.
    function = helper.make_function(
        "",
        "Scale",
        ["x"],
        ["y"],
        [
            helper.make_node("Constant", [], ["factors"], value=external(weights())),
            helper.make_node("MatMul", ["x", "factors"], ["y"]),
        ],
        [helper.make_opsetid("", 17)],
    )

    assert opens_fails(tmp_path, scaled(function)).startswith("The model keeps its weights in")


def test_external_data_in_a_default_of_a_local_function_fails_opens(tmp_path: Path) -> None:
    function = helper.make_function(
        "",
        "Scale",
        ["x"],
        ["y"],
        [helper.make_node("MatMul", ["x", "x"], ["y"])],
        [helper.make_opsetid("", 17)],
    )
    function.attribute_proto.append(helper.make_attribute("factors", external(weights())))

    assert opens_fails(tmp_path, scaled(function)).startswith("The model keeps its weights in")


def test_an_opset_a_local_function_imports_fails_opens(tmp_path: Path) -> None:
    function = helper.make_function(
        "",
        "Scale",
        ["x"],
        ["y"],
        [helper.make_node("MatMul", ["x", "x"], ["y"])],
        [helper.make_opsetid("", 17), helper.make_opsetid("com.microsoft", 1)],
    )

    assert opens_fails(tmp_path, scaled(function)).startswith(
        'The model uses operators of the domain "com.microsoft"'
    )


def test_weights_in_a_local_function_count(tmp_path: Path) -> None:
    function = helper.make_function(
        "",
        "Scale",
        ["x"],
        ["y"],
        [
            helper.make_node("Constant", [], ["factors"], value=weights()),
            helper.make_node("MatMul", ["x", "factors"], ["y"]),
        ],
        [helper.make_opsetid("", 17)],
    )
    model = scaled(function)
    del model.graph.initializer[:]

    assert inspect_onnx(save(tmp_path, model)).parameters == 5


def test_a_domain_that_is_not_utf8_fails_opens(tmp_path: Path) -> None:
    model = build()
    model.graph.node[0].domain = "ZZZZ"
    content = model.SerializeToString().replace(b"ZZZZ", b"\xff\xfe\xfd\xfc")

    assert 'domain "a domain without a name"' in opens_fails(tmp_path, content)


def test_an_imported_domain_without_a_name_is_shown_so(tmp_path: Path) -> None:
    model = build(opsets=[helper.make_opsetid("", 17), helper.make_opsetid("\u200b", 1)])

    assert 'domain "a domain without a name"' in opens_fails(tmp_path, model)


def test_two_inputs_fail_opens(tmp_path: Path) -> None:
    model = build(inputs=[tensor("candles"), tensor("news")])

    assert opens_fails(tmp_path, model) == (
        "The model has 2 inputs. CandleStack takes models with one input: the window of candles."
    )


def test_two_outputs_fail_opens(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["output"]),
            helper.make_node("Identity", ["output"], ["copy"]),
        ],
        outputs=[tensor("output", shape=["batch", 1]), tensor("copy", shape=["batch", 1])],
    )

    assert opens_fails(tmp_path, model).startswith("The model has 2 outputs.")


def test_an_int_input_fails_opens(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("Cast", ["candles"], ["floats"], to=TensorProto.FLOAT),
            helper.make_node("ReduceMean", ["floats"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["output"]),
        ],
        inputs=[tensor("candles", TensorProto.INT64)],
    )

    assert opens_fails(tmp_path, model) == (
        "The model's input holds int64 numbers. CandleStack takes a model whose input and "
        "output are float32: export it so and upload it again."
    )


def test_a_double_output_fails_opens(tmp_path: Path) -> None:
    model = build(
        nodes=[
            helper.make_node("ReduceMean", ["candles"], ["means"], axes=[1], keepdims=0),
            helper.make_node("MatMul", ["means", "weights"], ["scores"]),
            helper.make_node("Cast", ["scores"], ["output"], to=TensorProto.DOUBLE),
        ],
        outputs=[tensor("output", TensorProto.DOUBLE, ["batch", 1])],
    )

    assert opens_fails(tmp_path, model).startswith("The model's output holds double numbers.")


def test_an_input_that_is_no_tensor_fails_opens(tmp_path: Path) -> None:
    sequence = helper.make_tensor_sequence_value_info("candles", TensorProto.FLOAT, None)

    assert opens_fails(tmp_path, build(inputs=[sequence])).startswith(
        "The model's input is no tensor."
    )


def test_an_input_without_a_shape_fails_opens(tmp_path: Path) -> None:
    unshaped = helper.make_tensor_value_info("candles", TensorProto.FLOAT, None)

    assert opens_fails(tmp_path, build(inputs=[unshaped])).startswith(
        "The model does not say the shape of its input."
    )


@pytest.mark.parametrize(
    ("shape", "start"),
    [
        (["batch"], "The model's input has 1 axes."),
        (["batch", *[2] * 8], "The model's input has 9 axes."),
        ([1, 25, 5], "The model's batch axis is fixed at 1."),
        (["batch", "window", 5], "The axis 1 of the model's input has no fixed size."),
        (["batch", 25, None], "The axis 2 of the model's input has no fixed size."),
        (["batch", 10**12, 5], f"The axis 1 of the model's input has the size {10**12}."),
        (["batch", 25, 0], "The axis 2 of the model's input has the size 0."),
        (["batch", 25, -5], "The axis 2 of the model's input has the size -5."),
    ],
    ids=["one-axis", "nine-axes", "fixed-batch", "named", "unknown", "huge", "zero", "negative"],
)
def test_an_input_shape_the_check_cannot_take_fails_opens(
    shape: Shape, start: str, tmp_path: Path
) -> None:
    assert opens_fails(tmp_path, build(inputs=[tensor("candles", shape=shape)])).startswith(start)


def test_the_largest_axis(tmp_path: Path) -> None:
    detail = opens_fails(tmp_path, build(inputs=[tensor("candles", shape=["batch", 25, -1])]))

    assert detail.endswith(f"CandleStack takes sizes from 1 to {AXIS_MAX}.")
    assert AXIS_MAX == 2**31 - 1


def test_an_output_of_a_dynamic_size_fails_opens(tmp_path: Path) -> None:
    model = build(outputs=[tensor("output", shape=["batch", "scores"])])

    assert opens_fails(tmp_path, model).startswith(
        "The axis 1 of the model's output has no fixed size."
    )


def test_a_model_without_weights_fails_weights(tmp_path: Path) -> None:
    model = build(
        nodes=[helper.make_node("ReduceMean", ["candles"], ["output"], axes=[2], keepdims=0)],
        outputs=[tensor("output", shape=["batch", 25])],
        initializers=[],
    )

    failed = failure(tmp_path, model)

    assert (failed.check, failed.detail) == (
        "weights",
        "The model holds no weights. Export the model after training and upload that file.",
    )


@pytest.mark.parametrize(
    "dims",
    [[5, 10**9], [-5, -1]],
    ids=["declared-larger-than-stored", "negative"],
)
def test_weights_that_declare_more_than_they_hold_fail_weights(
    dims: list[int], tmp_path: Path
) -> None:
    held = weights()
    held.ClearField("dims")
    held.dims.extend(dims)

    failed = failure(tmp_path, build(initializers=[held]))

    assert (failed.check, failed.detail) == (
        "weights",
        "The weights of the model cannot be read. " + EXPORT_AGAIN,
    )


def test_sparse_weights_count(tmp_path: Path) -> None:
    sparse = helper.make_sparse_tensor(
        numpy_helper.from_array(np.array([0.5, 0.25], dtype=np.float32), "weights"),
        numpy_helper.from_array(np.array([0, 3], dtype=np.int64), "indices"),
        [5, 1],
    )
    model = build(initializers=[])
    model.graph.sparse_initializer.append(sparse)

    assert inspect_onnx(save(tmp_path, model)).parameters == 4


def test_a_damaged_onnx_file_fails_opens(tmp_path: Path) -> None:
    whole = build().SerializeToString()

    assert opens_fails(tmp_path, whole[: len(whole) // 2]).startswith(
        "The file is not a readable ONNX model."
    )


def test_the_check_process_checks_an_onnx_file(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = str(FIXTURES / "raw_candles.onnx")

    assert inspect.main([path, str(tmp_path / "scratch"), "1000", "onnx"], limits=False) == 0

    assert json.loads(capsys.readouterr().out) == {
        "facts": {
            "keras_version": None,
            "parameters": 128,
            "input_shape": [25, 5],
            "output_shape": [1],
        }
    }
