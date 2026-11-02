"""The Kagi line on the close, and its rows: the current segment at each candle.

The line is built on the close of each candle, in float64. It starts at the first candle's
close without a direction, and its first segment gets one on the first candle whose close is
at least the reversal amount away from that price, up or down: it runs from the start price
to that close. On each later candle the close is compared with the current segment's extreme
``x`` (the top of a segment going up): a close beyond it extends the segment (``x = close``);
a close at least the amount back from it reverses the line, a new segment from ``x`` to the
close, started at this candle; anything between changes nothing. One candle makes at most one
reversal, and an overnight gap is like any other move.

The amount is ``amount`` times the ATR (``atr@1``) of the candle that starts the segment, fixed
for the segment, or ``amount`` percent of the segment's extreme as it stands. While the line
has no direction the candle that would start the first segment is the current one. A candle
whose amount is not a positive number (an ATR of 0, of candles that never moved) reverses
nothing.

The line is thick (yang) or thin (yin): the first segment is yang when it goes up. A finished
segment going up leaves its top as the last shoulder, one going down its bottom as the last
waist. The line turns yang when a segment going up rises above the last shoulder and yin when
one going down falls below the last waist, and keeps its thickness otherwise, across a
reversal too.

A row of the representation is the current segment as it stands at a candle's close, one row
per candle from the first segment on, known at that candle's close. The rows of one segment
share its ``row_id``, and the next segment's first row tells the window that the one before
finished (``blocks.window``), as for the Renko triples.

The line depends on every candle since the first one the block gets: each segment starts at
the extreme of the one before. The same candles always give the same line, but candles that
start elsewhere (a backtest of another period) can give another.
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from candlestack.preprocessing import indicators as ind
from candlestack.preprocessing.blocks import ROW_ID

# About how many candles one segment takes: 4 + 4 m + 5 m^2 for a reversal of m ATRs, as a
# random walk takes four times as long to go twice as far. On the 30-minute candles of the 521
# stocks of the BLESK data (2016 to 2026) a segment of 1 ATR takes 8.5 candles on average, under
# 8.9 on 95 % of the stocks and under 11.1 in 95 % of the runs of 24 segments, one of 3 ATRs
# 36, 39 and 50; the rule is above the 95 % of those runs for every amount from 0.25 to 10 ATRs
# and 0.25 to 5 percent, so a warm-up is rather too long than too short.
CANDLES_PER_SEGMENT = 4.0
CANDLES_PER_ATR = 4.0
CANDLES_PER_ATR_SQUARED = 5.0
# A percent counts as two ATRs: the ATR of those candles is about half a percent of the close
# (the median of the stocks; 0.36 % to 0.81 % on 90 % of them).
ATRS_PER_PERCENT = 2.0


class Kagi(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    reversal: Literal["atr", "percent"] = Field(
        default="atr",
        description=(
            "How the reversal amount is measured: in ATRs of the candle that starts a segment, "
            "or in percent of the segment's extreme"
        ),
    )
    amount: float = Field(
        default=1.0, gt=0, le=10, description="The reversal amount, in ATRs or in percent"
    )
    atr_window: ind.Window = Field(default=14, description="Candles in the ATR, for atr")


@dataclass(frozen=True, eq=False)
class Segments:
    """The current segment after each candle, one entry per candle in their order.

    - ``start``: the index of the candle that started the segment, -1 before the first;
    - ``origin``: the price it starts from, the extreme of the segment before (the first
      candle's close for the first segment and before it);
    - ``extreme``: its extreme as it stands, the price the line has reached;
    - ``direction``: 1 going up, -1 going down, NaN before the first segment;
    - ``yang``: 1 when the line is thick, 0 when it is thin, NaN before the first segment.
    """

    start: np.ndarray
    origin: np.ndarray
    extreme: np.ndarray
    direction: np.ndarray
    yang: np.ndarray

    @property
    def number(self) -> np.ndarray:
        """The number of each candle's segment, from 0 for the first; -1 before it."""
        new = np.diff(self.start, prepend=-1) != 0
        return np.cumsum(new & (self.start >= 0), dtype=np.int64) - 1


@dataclass(slots=True)
class _Line:
    """The line as it stands: its current segment and the last shoulder and waist."""

    start: int
    origin: float
    extreme: float
    direction: float = math.nan
    yang: float = math.nan
    shoulder: float = math.nan
    waist: float = math.nan


def _move(line: _Line, candle: int, close: float, amount: float) -> None:
    """The line after a candle's close, with the amount of its current segment."""
    d, x = line.direction, line.extreme
    if math.isnan(d):
        if amount > 0 and abs(close - x) >= amount:
            line.direction = 1.0 if close > x else -1.0
            line.extreme, line.start = close, candle
            line.yang = 1.0 if close > x else 0.0
    elif d * (close - x) > 0:
        line.extreme = close
    elif amount > 0 and d * (x - close) >= amount:
        if d > 0:
            line.shoulder = x
        else:
            line.waist = x
        line.origin, line.extreme, line.direction, line.start = x, close, -d, candle
    if line.direction > 0 and line.extreme > line.shoulder:
        line.yang = 1.0
    elif line.direction < 0 and line.extreme < line.waist:
        line.yang = 0.0


def segments(close: np.ndarray, size: np.ndarray, *, percent: bool = False) -> Segments:
    """The Kagi line of candles with the reversal amount ``size`` of each: in price
    (``amount`` times its ATR), or with ``percent`` in percent of the extreme. A segment
    reverses by the amount of the candle that started it.
    """
    count = len(close)
    start = np.full(count, -1, dtype=np.int64)
    origin = np.empty(count)
    extreme = np.empty(count)
    direction = np.full(count, np.nan)
    yang = np.full(count, np.nan)
    if not count:
        return Segments(start, origin, extreme, direction, yang)
    closes, sizes = close.astype(np.float64).tolist(), size.astype(np.float64).tolist()
    line = _Line(start=-1, origin=closes[0], extreme=closes[0])
    origin[0] = extreme[0] = closes[0]
    for i in range(1, count):
        # Without a segment yet, this candle would start the first one.
        amount = sizes[line.start if line.start >= 0 else i]
        if percent:
            amount = amount * line.extreme / 100
        _move(line, i, closes[i], amount)
        start[i], origin[i], extreme[i] = line.start, line.origin, line.extreme
        direction[i], yang[i] = line.direction, line.yang
    return Segments(start, origin, extreme, direction, yang)


def rows(ts: np.ndarray, made: Segments) -> pl.DataFrame:
    """The rows of the line: the current segment at each candle from the first segment on.

    - ``ts``: the candle, at whose close the row is known;
    - ``row_id``: the number of the segment, from 0;
    - ``start``: the ``ts`` of the candle that started the segment;
    - ``direction``: 1.0 going up, -1.0 going down;
    - ``from``, ``to``: the price it starts from and its extreme at the candle;
    - ``yang``: 1.0 when the line is thick at the candle, 0.0 when it is thin.
    """
    at = np.flatnonzero(made.start >= 0)
    return pl.DataFrame(
        {
            "ts": ts[at],
            ROW_ID: made.number[at],
            "start": ts[made.start[at]],
            "direction": made.direction[at],
            "from": made.origin[at],
            "to": made.extreme[at],
            "yang": made.yang[at],
        },
        schema={
            "ts": pl.Int64,
            ROW_ID: pl.Int64,
            "start": pl.Int64,
            "direction": pl.Float64,
            "from": pl.Float64,
            "to": pl.Float64,
            "yang": pl.Float64,
        },
    )


def kagi_rows(frame: pl.DataFrame, size: np.ndarray, *, percent: bool = False) -> pl.DataFrame:
    """The rows of the candles in ``frame`` (``ts``, close) with the reversal amount ``size``
    of each candle (``segments``), the line starting on its first candle."""
    made = segments(frame["close"].to_numpy(), size, percent=percent)
    return rows(frame["ts"].cast(pl.Int64).to_numpy(), made)


def kagi(frame: pl.DataFrame, params: Kagi) -> pl.DataFrame:
    """``kagi@1``: the rows of a Kagi line on the close that reverses after ``amount`` ATRs
    (``atr@1`` of ``atr_window`` candles) of the candle that starts a segment, or ``amount``
    percent of its extreme.

    In ATRs the line starts on the first candle whose ATR is out of its warm-up, so an amount
    is ``amount`` times the ``atr`` feature of its candle, whatever candle the frame starts on;
    in percent on the frame's first candle.
    """
    if params.reversal == "percent":
        return kagi_rows(frame, np.full(frame.height, params.amount), percent=True)
    first = ind.atr_warm_up(ind.Atr(window=params.atr_window))
    atr = ind.atr(frame, ind.Atr(window=params.atr_window)).slice(first).to_numpy()
    return kagi_rows(frame.slice(first), params.amount * atr)


def direction(rows: pl.DataFrame, _params: BaseModel) -> pl.Series:
    """``kagi-direction@1``: the direction of each row's segment, 1 or -1."""
    return rows["direction"]


def length(rows: pl.DataFrame, _params: BaseModel) -> pl.Series:
    """``kagi-length@1``: how far each row's segment has gone, ``to / from - 1``, negative
    going down."""
    return rows["to"] / rows["from"] - 1


def yang(rows: pl.DataFrame, _params: BaseModel) -> pl.Series:
    """``kagi-yang@1``: 1 where the line is thick (yang) at the row's candle, 0 where thin."""
    return rows["yang"]


def candles_per_row(params: Kagi) -> float:
    """About how many candles one finished row takes: one segment."""
    atrs = params.amount * (ATRS_PER_PERCENT if params.reversal == "percent" else 1.0)
    return CANDLES_PER_SEGMENT + CANDLES_PER_ATR * atrs + CANDLES_PER_ATR_SQUARED * atrs**2


def warm_up(params: Kagi) -> int:
    """The candles before the first row: the ATR's warm-up for ``atr``, then the first
    segment's."""
    first = ind.atr_warm_up(ind.Atr(window=params.atr_window)) if params.reversal == "atr" else 0
    return first + math.ceil(candles_per_row(params))
