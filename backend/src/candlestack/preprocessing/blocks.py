"""The blocks: plain functions on Polars frames, one per step of the input.

Each step has its own form, which the catalog calls it by:

- a representation takes the candles and gives the rows: each row with ``ts``, the candle at
  whose close it is known (labelled by its open time, as every candle, docs/data.md#time), and
  the row's own columns; rows that span several candles also give ``row_id``, the same on the
  rows of one row still forming at successive candles, so its last one is the finished row;
- a feature takes the candles and gives one Float64 value per candle, known at its close; each
  row takes the value of its candle. A feature of the rows (``Block.of_rows``) takes the rows
  of its representation instead and gives one value per row;
- a normalization takes a column of the rows and gives it scaled;
- the window takes the rows and gives the samples.

A block sees only the candles up to the one it computes: no centered windows, no ``shift(-n)``,
no statistics over the whole series (tests/unit/preprocessing/test_look_ahead.py).
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict

from candlestack.preprocessing.manifest import Window

CANDLE_COLUMNS = ("open", "high", "low", "close", "volume")
# The mark of the last candle of each session, which the data module adds to stock candles from
# the exchange calendar (docs/data.md#last-candle-of-a-session): true or false per candle.
SESSION_LAST = "session_last"
# The column of rows that span several candles: rows of one ``row_id`` are one row at
# successive candles, the last of them the finished one. The window reads it from the rows.
ROW_ID = "row_id"


class NoParams(BaseModel):
    """The parameters of a block that takes none."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


@dataclass(frozen=True, eq=False)
class Samples:
    """What the window makes of the rows.

    - ``samples``: float32 of shape ``(samples, length, features)``, the rows of each sample in
      time order, the features in the manifest's order;
    - ``ts``: the ``ts`` of each sample's last row, the candle at whose close it is known;
    - ``dropped``: the rows at the start that end no sample: the incomplete ones (a feature
      still in its warm-up) and the ones before the first full window.
    """

    samples: np.ndarray
    ts: pl.Series
    dropped: int


def candles(frame: pl.DataFrame, _params: NoParams) -> pl.DataFrame:
    """``candles@1``: every candle a row, known at its own close."""
    return frame.select("ts", *CANDLE_COLUMNS)


def candle_column(name: str) -> Callable[[pl.DataFrame, NoParams], pl.Series]:
    """The feature that is the column ``name`` of each candle: ``open@1`` ... ``volume@1``."""

    def feature(frame: pl.DataFrame, _params: NoParams) -> pl.Series:
        return frame[name].cast(pl.Float64)

    return feature


def session_last(frame: pl.DataFrame, _params: NoParams) -> pl.Series:
    """``session-last-candle@1``: 1 on the last candle of its trading session, else 0.

    The mark comes with the candles (``SESSION_LAST``), from the calendar, so it is known when
    the candle closes, early closes included.

    Raises:
        ValueError: The candles have no marks: they are not candles of a session.
    """
    if SESSION_LAST not in frame.columns:
        raise ValueError(
            "The candles do not mark the last candle of each session, which "
            "session-last-candle@1 reads; only stock candles of the regular session do"
        )
    return frame[SESSION_LAST].cast(pl.Float64)


def none(column: pl.Series, _params: NoParams) -> pl.Series:
    """``none@1``: the column as it is."""
    return column


def window(rows: pl.DataFrame, params: Window) -> Samples:
    """``window@1``: for each row, the last ``length`` rows up to it, as one sample.

    The rows are ``ts``, ``row_id`` for rows that span several candles, and the features in
    their order. A row is complete when every feature is a finite number. The incomplete rows
    at the start are a warm-up, dropped and counted; one after a complete row is an error, as
    the model would get a sample with a hole in it.

    ``last: closed`` takes the ``length`` rows up to each row, each finished at its own
    candle's close. ``last: current`` takes, for each row, the ``length - 1`` most recent
    finished rows before it and then the row itself, which may still be forming. A row is
    finished when the next row has another ``row_id``: known at the next row's candle, so a
    sample never reads whether its own last row is finished.

    Raises:
        ValueError: An incomplete row follows a complete one, or the rule does not fit the
            rows: ``closed`` needs rows finished at their candle, ``current`` rows that span
            several candles.
    """
    forming = ROW_ID in rows.columns
    if params.last == "current" and not forming:
        raise ValueError("The window rule last: current needs rows that span several candles")
    if params.last == "closed" and forming:
        raise ValueError("The window rule last: closed needs rows finished at their own candle")
    features = [name for name in rows.columns if name not in ("ts", ROW_ID)]
    complete = rows.select(
        pl.all_horizontal(pl.col(name).is_finite().fill_null(value=False) for name in features)
    ).to_series()
    # The first complete row; with none, every row is warm-up.
    start = int(complete.arg_max() or 0) if complete.any() else rows.height
    rest = complete.slice(start)
    if not rest.all():
        late = rows["ts"][start + int((~rest).arg_max() or 0)]
        raise ValueError(f"A feature is missing or not finite after the warm-up, at ts {late}")
    matrix = rows.select(features).slice(start).to_numpy().astype(np.float32)
    if forming:
        picked = _current(rows[ROW_ID].slice(start).to_numpy(), params.length)
    else:
        picked = _closed(matrix.shape[0], params.length)
    samples = matrix[picked] if picked.size else np.empty((0, params.length, len(features)))
    dropped = rows.height - picked.shape[0]
    return Samples(
        samples=np.ascontiguousarray(samples, dtype=np.float32),
        ts=rows["ts"].slice(dropped),
        dropped=dropped,
    )


def _closed(count: int, length: int) -> np.ndarray:
    """The rows of each sample of ``last: closed``: the ``length`` rows up to each row."""
    if count < length:
        return np.empty((0, length), dtype=np.int64)
    return np.arange(count - length + 1)[:, None] + np.arange(length)


def _current(row_id: np.ndarray, length: int) -> np.ndarray:
    """The rows of each sample of ``last: current``: the ``length - 1`` most recent finished
    rows before a row, then the row. The samples start at the first row that has as many
    finished rows before it."""
    finished = np.flatnonzero(row_id[1:] != row_id[:-1])
    # How many finished rows come before each row: those before row i end before it.
    before = np.searchsorted(finished, np.arange(row_id.size), side="left")
    rows = np.flatnonzero(before >= length - 1)
    earlier = finished[before[rows, None] - (length - 1) + np.arange(length - 1)]
    return np.concatenate([earlier, rows[:, None]], axis=1)


def window_warm_up(params: Window) -> int:
    """The rows before the first full window."""
    return params.length - 1


def no_warm_up(_params: BaseModel) -> int:
    return 0
