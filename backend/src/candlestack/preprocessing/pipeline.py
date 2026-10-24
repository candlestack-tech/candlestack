"""The model's input from candles and a manifest: representation, features, normalization,
window, each step the block the manifest names."""

from dataclasses import dataclass

import numpy as np
import polars as pl

from candlestack.preprocessing.blocks import CANDLE_COLUMNS
from candlestack.preprocessing.catalog import CATALOG, WINDOW
from candlestack.preprocessing.checks import check_manifest
from candlestack.preprocessing.manifest import Manifest


@dataclass(frozen=True, eq=False)
class ModelInput:
    """What the model gets, and what it was built from.

    - ``rows``: ``ts`` and one Float64 column per feature, named as in the manifest and
      normalized, one row per row of the representation;
    - ``samples``: float32 of shape ``(samples, window length, features)``;
    - ``ts``: Int64, one per sample, the candle at whose close it is known: its prediction goes
      to that candle (``signals.align`` joins on it as it is);
    - ``dropped``: the rows at the start without a sample, the warm-up the report counts.
    """

    rows: pl.DataFrame
    samples: np.ndarray
    ts: pl.Series
    dropped: int


def warm_up(manifest: Manifest) -> int:
    """How many candles before the first sample the input needs: the representation's, the
    longest of a feature with its normalization, and the window's.

    With ``candles@1`` this is exact. A representation whose rows span several candles will
    need its own count of candles per row for the window.

    Raises:
        KeyError: A block is not in the catalog (``check_manifest`` says so first).
    """
    spec = manifest.input
    rep = CATALOG[spec.representation.id]
    features = [
        _warm_up(feature.id, feature.params) + _warm_up(feature.norm.id, feature.norm.params)
        for feature in spec.features
    ]
    return (
        rep.warm_up(rep.params.model_validate(spec.representation.params))
        + max(features)
        + CATALOG[WINDOW].warm_up(spec.window)
    )


def build_input(candles: pl.DataFrame, manifest: Manifest) -> ModelInput:
    """The model's input from ``candles`` (``ts`` and the columns open, high, low, close and
    volume, as the data module gives them) and ``manifest``.

    Raises:
        ValueError: The manifest cannot run (``check_manifest`` finds a problem), the candles
            break their rules, or a feature has a hole after its warm-up.
    """
    if problems := check_manifest(manifest):
        raise ValueError(
            "The manifest cannot run: " + " ".join(problem.message for problem in problems)
        )
    frame = _checked(candles)
    spec = manifest.input
    rep = CATALOG[spec.representation.id]
    rows: pl.DataFrame = rep.run(frame, rep.params.model_validate(spec.representation.params))
    values = frame.select(
        "ts",
        *(_run(feature.id, frame, feature.params).alias(feature.name) for feature in spec.features),
    )
    rows = rows.select("ts").join(values, on="ts", how="left", maintain_order="left")
    rows = rows.with_columns(
        _run(feature.norm.id, rows[feature.name], feature.norm.params)
        .cast(pl.Float64)
        .alias(feature.name)
        for feature in spec.features
    )
    made = CATALOG[WINDOW].run(rows, spec.window)
    return ModelInput(rows=rows, samples=made.samples, ts=made.ts, dropped=made.dropped)


def _run(block_id: str, data: pl.DataFrame | pl.Series, params: dict[str, object]) -> pl.Series:
    block = CATALOG[block_id]
    return block.run(data, block.params.model_validate(params))


def _warm_up(block_id: str, params: dict[str, object]) -> int:
    block = CATALOG[block_id]
    return block.warm_up(block.params.model_validate(params))


def _checked(candles: pl.DataFrame) -> pl.DataFrame:
    """The candles cast to Int64 ``ts`` and Float64 values.

    Raises:
        ValueError: A column is missing or not numeric, a value is missing, or a ``ts`` is not
            after the one before it.
    """
    missing = [name for name in ("ts", *CANDLE_COLUMNS) if name not in candles.columns]
    if missing:
        raise ValueError(f"The candles have no column {', '.join(missing)}")
    if not candles["ts"].dtype.is_integer():
        raise ValueError(f"The candles' ts must be integers, not {candles['ts'].dtype}")
    wrong = [name for name in CANDLE_COLUMNS if not candles[name].dtype.is_numeric()]
    if wrong:
        raise ValueError(f"The candles' {', '.join(wrong)} must be numbers")
    frame = candles.select(
        pl.col("ts").cast(pl.Int64), *(pl.col(name).cast(pl.Float64) for name in CANDLE_COLUMNS)
    )
    if frame.null_count().sum_horizontal().item():
        raise ValueError("A candle has a missing value")
    if (frame["ts"].diff().drop_nulls() <= 0).any():
        raise ValueError("Each candle's ts must be after the one before it")
    return frame
