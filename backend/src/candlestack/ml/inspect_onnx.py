"""The static check of an uploaded ONNX file: what the file holds, read without running it.

An ``.onnx`` file is one protobuf message (``ModelProto``): the graph of standard operators,
its weights as tensors, and the type and shape of every input and output. Reading it runs no
code from it, but onnxruntime would load weights from other files the model names (external
data) and run operators of other domains that a library registers (custom operators), so the
check refuses both and accepts only what the worker runs. Two checks, in order, as for Keras:

- ``opens``: the file parses as an ONNX model with a graph, keeps every tensor inside the file
  (no external data), uses operators of the standard domains only (``ai.onnx``, ``ai.onnx.ml``;
  no custom domain, no local function of one), has one float32 input and one float32 output,
  each with every axis but the first (the batch, which must be dynamic) of a fixed size, and
  passes the ONNX checker (``onnx.checker``, without shape inference).
- ``weights``: the graph holds weights (initializers or constant tensors), and no tensor
  declares more values than it stores.

The input and output shapes without the batch axis are what the manifest is compared with
(``check_with_model``), as the Keras check reads them. Nothing runs the model here: the trial
run is the worker's (docs/preprocessing.md, checks 4 and 5).

It runs in the same child process with the same limits as the Keras check (``sandbox.py``,
``inspect.main``), which imports this module, and so onnx, after its limits are set. The model
is parsed twice, never at once: by protobuf for what is read here, which is let go before the
ONNX checker parses the file itself, so a file of 50 MB stays within the child's memory.
"""

import math
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import onnx
from google.protobuf.message import DecodeError
from onnx import (
    GraphProto,
    ModelProto,
    NodeProto,
    TensorProto,
    TensorShapeProto,
    ValueInfoProto,
)

from candlestack.ml.inspect import CheckFailed, ModelFacts

# The operator domains onnxruntime runs without a library of custom operators: the default
# one (written as "" or "ai.onnx") and the classical machine learning one (scikit-learn).
DOMAINS = frozenset({"", "ai.onnx", "ai.onnx.ml"})
# The largest size of an axis: an int32, which a shape keeps exactly in JSON for the app too.
AXIS_MAX = 2**31 - 1
# The most axes an input or output may have: as many as a manifest's shape (8).
AXES_MAX = 8
_SHOWN_MAX = 60

_EXPORT_AGAIN = "Export the model to ONNX again and upload that file."
_BROKEN = (
    "The file is not a readable ONNX model. It may be damaged or not a .onnx file at all. "
    + _EXPORT_AGAIN
)
_INVALID = "The file is not a valid ONNX model: the ONNX checker refused it. " + _EXPORT_AGAIN
_EXTERNAL = (
    "The model keeps its weights in separate files (external data). CandleStack takes one "
    "file: save the model with onnx.save_model(model, path, save_as_external_data=False) and "
    "upload that file."
)
_UNREADABLE_WEIGHTS = "The weights of the model cannot be read. " + _EXPORT_AGAIN
_NO_WEIGHTS = "The model holds no weights. Export the model after training and upload that file."


def inspect_onnx(path: Path) -> ModelFacts:
    """Checks the ONNX file at `path` without running anything from it and returns what it
    holds.

    Raises:
        CheckFailed: A check failed; its ``detail`` tells the user why.
    """
    try:
        model = onnx.load(path, load_external_data=False)
    except (DecodeError, RecursionError, ValueError) as error:
        raise _opens(_BROKEN) from error
    if not model.ir_version or not model.HasField("graph"):
        raise _opens(_BROKEN)
    graphs = list(_graphs(model))
    _check_tensors_inside(graphs)
    _check_domains(model, graphs)
    input_shape, output_shape = _interface(model.graph)
    parameters = _parameters(graphs)
    # The parsed model holds the weights: it goes before the checker parses the file again.
    del model, graphs
    try:
        onnx.checker.check_model(path)
    except (onnx.checker.ValidationError, ValueError, RuntimeError) as error:
        raise _opens(_INVALID) from error
    if parameters == 0:
        raise _weights(_NO_WEIGHTS)
    return ModelFacts(
        keras_version=None,
        parameters=parameters,
        input_shape=input_shape,
        output_shape=output_shape,
    )


def _opens(detail: str) -> CheckFailed:
    return CheckFailed("opens", detail)


def _weights(detail: str) -> CheckFailed:
    return CheckFailed("weights", detail)


def _graphs(model: ModelProto) -> Iterator[GraphProto]:
    """The model's graph, every graph nested in one (the branches of ``If``, the bodies of
    ``Loop`` and ``Scan``) and those of its training info. Walks them with a stack of its own,
    as protobuf bounds how deep they nest."""
    stack = [model.graph]
    for info in model.training_info:
        stack += [info.initialization, info.algorithm]
    while stack:
        graph = stack.pop()
        yield graph
        for node in graph.node:
            for attribute in node.attribute:
                if attribute.HasField("g"):
                    stack.append(attribute.g)
                stack.extend(attribute.graphs)


def _nodes(model: ModelProto, graphs: list[GraphProto]) -> Iterator[NodeProto]:
    """Every node: of the graphs and of the model's local functions."""
    for graph in graphs:
        yield from graph.node
    for function in model.functions:
        yield from function.node


def _tensors(graphs: list[GraphProto]) -> Iterator[TensorProto]:
    """Every tensor the graphs hold: their initializers (sparse ones as their values and
    indices) and the tensors of their nodes' attributes (``Constant`` among them)."""
    for graph in graphs:
        yield from graph.initializer
        for sparse in graph.sparse_initializer:
            yield from (sparse.values, sparse.indices)
        for node in graph.node:
            for attribute in node.attribute:
                if attribute.HasField("t"):
                    yield attribute.t
                yield from attribute.tensors
                for sparse in (
                    *([attribute.sparse_tensor] if attribute.HasField("sparse_tensor") else []),
                    *attribute.sparse_tensors,
                ):
                    yield from (sparse.values, sparse.indices)


def _check_tensors_inside(graphs: list[GraphProto]) -> None:
    """Fails when a tensor keeps its data in another file: onnxruntime would read that file,
    any file the model names."""
    for tensor in _tensors(graphs):
        if tensor.data_location == TensorProto.EXTERNAL or len(tensor.external_data):
            raise _opens(_EXTERNAL)


def _check_domains(model: ModelProto, graphs: list[GraphProto]) -> None:
    """Fails unless the model imports and its nodes and functions use the standard domains
    only."""
    used = [entry.domain for entry in model.opset_import]
    used += [function.domain for function in model.functions]
    used += [node.domain for node in _nodes(model, graphs)]
    custom = next((domain for domain in used if domain not in DOMAINS), None)
    if custom is not None:
        shown = _shown(custom) or "a domain without a name"
        raise _opens(
            f'The model uses operators of the domain "{shown}" (custom operators), which '
            "CandleStack does not run. Export the model with the standard ONNX operators "
            "only and upload it again."
        )


def _interface(graph: GraphProto) -> tuple[list[int], list[int]]:
    """The shapes of one input sample and of one output without the batch axis.

    Raises:
        CheckFailed: Not one input and one output, each a float32 tensor with a dynamic batch
            axis and every other axis of a fixed size.
    """
    # Before IR version 4 the initializers were listed among the inputs too.
    weights = {tensor.name for tensor in graph.initializer}
    inputs = [value for value in graph.input if value.name not in weights]
    if len(inputs) != 1:
        raise _opens(
            f"The model has {len(inputs)} inputs. CandleStack takes models with one input: "
            "the window of candles."
        )
    if len(graph.output) != 1:
        raise _opens(
            f"The model has {len(graph.output)} outputs. CandleStack takes models with one "
            "output: the score of each sample."
        )
    return _sample_shape(inputs[0], "input"), _sample_shape(graph.output[0], "output")


def _sample_shape(value: ValueInfoProto, name: str) -> list[int]:
    """The shape of one sample of the input or output `value` (`name`), without its batch
    axis.

    Raises:
        CheckFailed: It is no float32 tensor, says no shape, has a fixed batch axis or another
            axis that is not of a fixed size from 1 to ``AXIS_MAX``.
    """
    if value.type.WhichOneof("value") != "tensor_type":
        raise _opens(
            f"The model's {name} is no tensor. CandleStack takes a model whose input and "
            "output are float32 tensors."
        )
    tensor = value.type.tensor_type
    if tensor.elem_type != TensorProto.FLOAT:
        raise _opens(
            f"The model's {name} holds {_type_name(tensor.elem_type)} numbers. CandleStack "
            "takes a model whose input and output are float32: export it so and upload it "
            "again."
        )
    if not tensor.HasField("shape"):
        raise _opens(
            f"The model does not say the shape of its {name}. Export it with every axis but "
            "the batch of a fixed size and upload it again."
        )
    return _sizes(list(tensor.shape.dim), name)


def _sizes(dims: list[TensorShapeProto.Dimension], name: str) -> list[int]:
    """The sizes of the axes `dims` of the input or output `name` after its batch axis.

    Raises:
        CheckFailed: Too few or too many axes, a fixed batch axis or another axis that is not
            of a fixed size from 1 to ``AXIS_MAX``.
    """
    if not 2 <= len(dims) <= AXES_MAX:
        raise _opens(
            f"The model's {name} has {len(dims)} axes. CandleStack takes a model whose input "
            f"and output have a batch axis first and from 1 to {AXES_MAX - 1} axes after it."
        )
    if dims[0].HasField("dim_value"):
        raise _opens(
            f"The model's batch axis is fixed at {dims[0].dim_value}. CandleStack runs many "
            "samples at once: export the model with a dynamic batch axis (dynamic_axes or "
            "dynamic_shapes in torch.onnx.export) and upload it again."
        )
    sizes = []
    for axis, dim in enumerate(dims[1:], start=1):
        if not dim.HasField("dim_value"):
            raise _opens(
                f"The axis {axis} of the model's {name} has no fixed size. CandleStack takes "
                "a model whose axes but the batch all have one: export it so and upload it "
                "again."
            )
        if not 1 <= dim.dim_value <= AXIS_MAX:
            raise _opens(
                f"The axis {axis} of the model's {name} has the size {dim.dim_value}. "
                f"CandleStack takes sizes from 1 to {AXIS_MAX}."
            )
        sizes.append(dim.dim_value)
    return sizes


def _type_name(elem_type: int) -> str:
    try:
        return onnx.helper.tensor_dtype_to_string(elem_type).removeprefix("TensorProto.").lower()
    except (KeyError, ValueError):
        return "unknown"


def _parameters(graphs: list[GraphProto]) -> int:
    """The number of values of the weights: what the shapes of the tensors declare.

    Raises:
        CheckFailed: A tensor declares a negative size or more values than it stores (at most
            two in a byte, of the 4-bit types).
    """
    total = 0
    for tensor in _tensors(graphs):
        if any(dim < 0 for dim in tensor.dims):
            raise _weights(_UNREADABLE_WEIGHTS)
        declared = math.prod(tensor.dims)
        stored = (
            2 * len(tensor.raw_data)
            + 2 * len(tensor.int32_data)
            + len(tensor.float_data)
            + len(tensor.int64_data)
            + len(tensor.double_data)
            + len(tensor.uint64_data)
            + len(tensor.string_data)
        )
        if declared > stored:
            raise _weights(_UNREADABLE_WEIGHTS)
        total += declared
    return total


def _shown(value: str) -> str | None:
    """`value` as a detail may show it: without control or format characters, cut short."""
    kept = "".join(char for char in value if unicodedata.category(char) not in {"Cc", "Cf"})
    return kept.strip()[:_SHOWN_MAX] or None
