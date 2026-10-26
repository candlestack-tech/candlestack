"""Runs an uploaded ONNX file with onnxruntime (docs/architecture.md, "Models").

The upload's check refused external data, operators of other domains than the standard ones
and models without one float32 input and one float32 output. Loading is the second line:

- the file is copied alone into the job's scratch directory and loaded from there, so a
  tensor that still named another file (external data) would find none;
- the session uses onnxruntime's own CPU operators only: no library of custom operators is
  registered, so a node of a domain onnxruntime does not know fails the load;
- one thread for the operators and one between them, as the container has one CPU, and
  nothing else of onnxruntime's runs in the background.
"""

import shutil
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort

PROVIDERS = ["CPUExecutionProvider"]
# onnxruntime's warnings and errors only: its log goes to the container's.
_LOG_WARNING = 2


class OnnxModel:
    """A loaded ONNX model; ``predict`` answers like a Keras model's."""

    def __init__(self, session: ort.InferenceSession) -> None:
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("The model has not one input and one output")
        self._session = session
        self._input = inputs[0].name
        self._output = outputs[0].name

    def predict(self, x: Any, *, batch_size: int, verbose: int = 0) -> np.ndarray:  # noqa: ARG002 (Keras' signature)
        """The model's output for the samples `x`, run `batch_size` at a time."""
        samples = np.asarray(x, dtype=np.float32)
        batches = [
            np.asarray(
                self._session.run([self._output], {self._input: samples[at : at + batch_size]})[0]
            )
            for at in range(0, len(samples), batch_size)
        ]
        return np.concatenate(batches)


def session_options() -> ort.SessionOptions:
    """The settings of every session: one thread, run in order, quiet."""
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.log_severity_level = _LOG_WARNING
    return options


def load_onnx(path: Path, scratch: Path) -> OnnxModel:
    """The model of the ``.onnx`` file at ``path``; its copy goes to ``scratch``, which the
    caller removes.

    Raises:
        Exception: Whatever onnxruntime raises on a file it cannot load (the caller says so to
            the user), or ``ValueError`` when the model has not one input and one output.
    """
    copy = scratch / "model.onnx"
    shutil.copyfile(path, copy)
    return OnnxModel(
        ort.InferenceSession(str(copy), sess_options=session_options(), providers=PROVIDERS)
    )
