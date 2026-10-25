"""The model's input from candles and a manifest: representation, features, normalization,
window, each step the block the manifest names."""

import math
from dataclasses import dataclass
from typing import Literal

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


@dataclass(frozen=True, slots=True)
class WarmUp:
    """How many candles before the first sample a manifest needs.

    ``kind`` is ``exact`` when every candle is a row (``candles@1``): then ``candles`` is what
    the build drops. With rows that span several candles (Renko, Kagi) how many candles the
    window's rows take is known only from the candles themselves, so it is an ``estimate``: the
    run loads more and reports how many the warm-up used (``ModelInput.dropped``).
    """

    candles: int
    kind: Literal["exact", "estimate"]


def warm_up(manifest: Manifest) -> WarmUp:
    """The candles before the first sample: the longest warm-up of the representation and of a
    feature with its normalization (all counted in candles), then the window's earlier rows.

    Raises:
        KeyError: A block is not in the catalog (``check_manifest`` says so first).
    """
    spec = manifest.input
    rep = CATALOG[spec.representation.id]
    rep_params = rep.params.model_validate(spec.representation.params)
    longest = max(
        rep.warm_up(rep_params),
        *(
            _warm_up(feature.id, feature.params) + _warm_up(feature.norm.id, feature.norm.params)
            for feature in spec.features
        ),
    )
    rows = CATALOG[WINDOW].warm_up(spec.window)
    if rep.candles_per_row is None:
        return WarmUp(candles=longest + rows, kind="exact")
    return WarmUp(
        candles=longest + math.ceil(rows * rep.candles_per_row(rep_params)), kind="estimate"
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
        ValueError: A column is missing or not numeric, a value is missing or not finite, or a
            ``ts`` is not after the one before it.
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
    # A NaN or infinite price would pass for a feature's warm-up and be dropped without a word.
    if not frame.select(pl.all_horizontal(pl.col(CANDLE_COLUMNS).is_finite())).to_series().all():
        raise ValueError("A candle has a value that is not a finite number")
    if (frame["ts"].diff().drop_nulls() <= 0).any():
        raise ValueError("Each candle's ts must be after the one before it")
    return frame
