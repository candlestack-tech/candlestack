"""Builds the small .keras files the upload checks are tested with.

Keras is not a dependency of the backend, so this runs in a throwaway container from the
repository root:

    docker run --rm -v "$PWD/backend/tests/fixtures/keras:/out" python:3.13-slim sh -c \
        "pip install --quiet keras==3.15.1 jax==0.11.2 \
         && KERAS_BACKEND=jax python /out/make_models.py"

Each file is a few kilobytes; the seed keeps the weights the same between runs. To build one
file only, call its function instead (`python -c "import make_models; make_models.raw_candles()"`
from `/out`), so the other files stay as they are.
"""

import sys
from pathlib import Path

import keras
import numpy as np
from keras import layers

OUT = Path(__file__).parent
keras.utils.set_random_seed(7)


def fit(
    model: keras.Model, inputs: list[np.ndarray] | np.ndarray, metrics: list | None = None
) -> None:
    """A few steps of training, so the files hold trained weights, as uploads should."""
    model.compile(optimizer="adam", loss="mse", metrics=metrics)
    size = len(inputs[0]) if isinstance(inputs, list) else len(inputs)
    model.fit(inputs, np.random.rand(size, 1), epochs=1, batch_size=8, verbose=0)


@keras.saving.register_keras_serializable(package="Custom")
class OrthogonalL2Regularizer(keras.regularizers.Regularizer):
    """A regularizer of the user's own, as the supervisor's models have: training only."""

    def __init__(self, l2reg: float = 1e-4) -> None:
        self.l2reg = l2reg

    def __call__(self, weights):
        return self.l2reg * keras.ops.sum(keras.ops.square(weights))

    def get_config(self) -> dict:
        return {"l2reg": self.l2reg}


@keras.saving.register_keras_serializable(package="Custom")
class RankIC(keras.metrics.Mean):
    """A metric of the user's own, as the supervisor's models have: it only reports training."""

    def update_state(self, y_true, y_pred, sample_weight=None):
        return super().update_state(keras.ops.mean(y_true * y_pred), sample_weight)


@keras.saving.register_keras_serializable(package="Custom")
class Scale(layers.Layer):
    """A layer of the user's own: Python code a load would run."""

    def call(self, inputs):
        return inputs * 2.0


def windows(count: int = 32) -> np.ndarray:
    return np.random.rand(count, 25, 23).astype("float32")


def functional() -> None:
    """The shape of the supervisor's models: 25 x 23 in, one value out."""
    inputs = keras.Input((25, 23))
    x = layers.BatchNormalization()(inputs)
    x = layers.Flatten()(x)
    x = layers.Dense(16, activation="relu")(x)
    x = layers.Dropout(0.1)(x)
    model = keras.Model(inputs, layers.Dense(1)(x))
    fit(model, windows())
    model.save(OUT / "functional.keras")


def sequential() -> None:
    model = keras.Sequential(
        [
            keras.Input((30, 5)),
            layers.Flatten(),
            layers.Dense(8),
            layers.Dense(3, activation="softmax"),
        ]
    )
    fit(model, np.random.rand(32, 30, 5).astype("float32"))
    model.save(OUT / "sequential.keras")


def raw_candles() -> None:
    """The model of raw_candles.manifest.json, the manifest without preprocessing: the last 25
    candles (open, high, low, close, volume) in, one score out. The Normalization layer scales
    the raw prices inside the model, which the manifest therefore leaves as they are."""
    candles = (100 + np.random.rand(64, 25, 5) * 10).astype("float32")
    normalization = layers.Normalization(axis=-1)
    normalization.adapt(candles)
    inputs = keras.Input((25, 5))
    x = layers.Flatten()(normalization(inputs))
    x = layers.Dense(8, activation="relu")(x)
    model = keras.Model(inputs, layers.Dense(1, activation="tanh")(x))
    fit(model, candles)
    model.save(OUT / "raw_candles.keras")


def custom_regularizer() -> None:
    """A user's own regularizer and metric only shape and report training; the model runs
    without them."""
    inputs = keras.Input((25, 23))
    x = layers.Flatten()(inputs)
    x = layers.Dense(8, kernel_regularizer=OrthogonalL2Regularizer(1e-4))(x)
    model = keras.Model(inputs, layers.Dense(1)(x))
    fit(model, windows(), metrics=[RankIC()])
    model.save(OUT / "custom_regularizer.keras")


def lambda_layer() -> None:
    inputs = keras.Input((25, 23))
    x = layers.Flatten()(inputs)
    x = layers.Lambda(lambda t: t * 2.0)(x)
    model = keras.Model(inputs, layers.Dense(1)(x))
    model.save(OUT / "lambda_layer.keras")


def custom_layer() -> None:
    inputs = keras.Input((25, 23))
    x = Scale()(layers.Flatten()(inputs))
    model = keras.Model(inputs, layers.Dense(1)(x))
    model.save(OUT / "custom_layer.keras")


def no_weights() -> None:
    """Layers only, nothing to learn: the file holds no weights."""
    inputs = keras.Input((25, 23))
    model = keras.Model(inputs, layers.Activation("tanh")(layers.Flatten()(inputs)))
    model.save(OUT / "no_weights.keras")


def two_inputs() -> None:
    """Two inputs: no single input shape to show."""
    a, b = keras.Input((25, 23)), keras.Input((10,))
    x = layers.Concatenate()([layers.Flatten()(a), b])
    model = keras.Model([a, b], layers.Dense(1)(x))
    fit(model, [windows(), np.random.rand(32, 10).astype("float32")])
    model.save(OUT / "two_inputs.keras")


def operations() -> None:
    """Operations of keras.ops between the layers (they are saved as objects of their own), and
    a compile config with a schedule, a loss and a metric of Keras."""
    inputs = keras.Input((25, 23))
    x = keras.ops.reshape(keras.ops.add(inputs, 1.0), (-1, 575))
    x = layers.Dense(4, activation="gelu")(x[:, :100])
    model = keras.Model(inputs, layers.Dense(1)(x))
    schedule = keras.optimizers.schedules.ExponentialDecay(0.01, 10, 0.9)
    model.compile(
        optimizer=keras.optimizers.Adam(schedule),
        loss=keras.losses.Huber(),
        metrics=[keras.metrics.RootMeanSquaredError()],
    )
    model.fit(windows(), np.random.rand(32, 1), epochs=1, batch_size=8, verbose=0)
    model.save(OUT / "operations.keras")


if __name__ == "__main__":
    for build in (
        operations,
        functional,
        sequential,
        raw_candles,
        custom_regularizer,
        lambda_layer,
        custom_layer,
        no_weights,
        two_inputs,
    ):
        build()
    for path in sorted(OUT.glob("*.keras")):
        sys.stdout.write(f"{path.name}: {path.stat().st_size} bytes\n")
