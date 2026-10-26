"""Renko bricks and the rows of three bricks of the BLESK models (research #263).

A brick is built on the close of each candle. Its height is ``atr_multiple`` times the ATR of
the candle that starts it (``atr@1``), and it never changes afterwards. The first brick lies
on the mid price of the first candle, ``(high + low) / 2``, without a direction. On each later
candle the close is compared with the current brick ``[bottom, top]``: inside it (edges
included) nothing happens; above ``top`` the new brick is the cell of a grid of the candle's
brick height, anchored at ``top``, that holds the close, and the whole cells jumped over are
counted in ``skipped``; below ``bottom`` the same downwards, ``skipped`` negative. One candle
makes at most one brick, a reversal needs no more than a continuation, and an overnight gap is
like any other move. All of it is float64 (research #263, R10).

A row of the representation is a triple: the two bricks before the current one and the
current brick as it stands at a candle's close. There is one row per candle from the third
brick on, known at that candle's close: the triple of a brick still forming is replaced on the
next candle by the same triple with a later end, until a candle starts another brick and the
last of them is the finished one. The rows give ``row_id``, the number of the current brick,
so the window can tell which rows are finished without looking at a later candle than its own
(``blocks.window``).

The bricks depend on every candle since the first one the block gets: each is anchored at the
one before. The same candles always give the same bricks, but candles that start elsewhere
(a backtest of another period) give other bricks, which do not line up again for years.
"""

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from candlestack.preprocessing import indicators as ind
from candlestack.preprocessing.blocks import ROW_ID

# Bricks of a triple: the two finished ones before the current one, and the current one.
BRICKS_PER_ROW = 3
# About how many candles one brick takes: 1 + 2 m for a brick of m ATRs. On the 30-minute
# candles of the 521 stocks of the BLESK data (2016 to 2026) a brick of 0.6 ATR takes 1.7
# candles on average and under 1.8 on 95 % of them, a brick of 0.2 ATR 1.2, of 3 ATR 6.3; the
# rule is above the 95 % of every multiple in between, so a warm-up is rather too long than
# too short.
CANDLES_PER_BRICK = 1.0
CANDLES_PER_BRICK_PER_ATR = 2.0


class RenkoTriples(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    atr_multiple: float = Field(
        default=0.6, gt=0, le=10, description="The height of a brick in ATRs"
    )
    atr_window: ind.Window = Field(default=14, description="Candles in the ATR")
    atr_at: Literal["brick_start"] = Field(
        default="brick_start",
        description="The candle whose ATR sizes a brick: the one that starts it",
    )


@dataclass(frozen=True, slots=True)
class Brick:
    """One brick: its edges, its direction (NaN for the first) and the cells it skipped."""

    bottom: float
    top: float
    direction: float
    skipped: int


@dataclass(frozen=True, eq=False)
class Bricks:
    """The current brick after each candle, one entry per candle in their order.

    - ``start``: the index of the candle that started the brick, -1 for a brick started
      before the first candle (``bricks(after=...)``);
    - ``bottom``, ``top``: its edges;
    - ``direction``: 1 when it rose from the brick before, -1 when it fell, NaN for the first;
    - ``skipped``: the whole cells the close jumped over to reach it, negative when it fell.
    """

    start: np.ndarray
    bottom: np.ndarray
    top: np.ndarray
    direction: np.ndarray
    skipped: np.ndarray

    @property
    def number(self) -> np.ndarray:
        """The number of each candle's brick, from 0 for the brick of the first candle."""
        new = np.diff(self.start, prepend=self.start[:1]) != 0
        return np.cumsum(new, dtype=np.int64)

    @property
    def finished(self) -> np.ndarray:
        """1 where the next candle starts another brick, else 0, as the BLESK data has it.

        It looks one candle ahead, so no input reads it: the window tells the finished rows
        from the next row it sees (``blocks.window``).
        """
        return np.append(self.start[1:] != self.start[:-1], False).astype(np.int64)


def bricks(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    size: np.ndarray,
    *,
    after: Brick | None = None,
) -> Bricks:
    """The bricks of candles with the brick height ``size`` of each (``atr_multiple`` times
    its ATR): the current brick after each candle. The first brick lies on the first candle's
    mid price; with ``after``, the candles continue that brick instead, one started before
    them.

    A candle whose height is not a positive number (an ATR of 0, of candles that never moved)
    starts no brick.
    """
    count = len(close)
    start = np.zeros(count, dtype=np.int64)
    bottom = np.empty(count)
    top = np.empty(count)
    direction = np.full(count, np.nan)
    skipped = np.zeros(count, dtype=np.int64)
    if not count:
        return Bricks(start, bottom, top, direction, skipped)
    closes, sizes = close.astype(np.float64).tolist(), size.astype(np.float64).tolist()
    if after is None:
        b = (float(high[0]) + float(low[0])) / 2
        t = b + sizes[0] if sizes[0] > 0 else b
        d, k, first, begin = math.nan, 0, 0, 1
        bottom[0], top[0] = b, t
    else:
        b, t, d, k, first, begin = after.bottom, after.top, after.direction, after.skipped, -1, 0
    for i in range(begin, count):
        c, s = closes[i], sizes[i]
        if s > 0:
            if c > t:
                k = math.floor((c - t) / s)
                b = t + k * s
                t = b + s
                d, first = 1.0, i
            elif c < b:
                k = math.floor((b - c) / s)
                t = b - k * s
                b = t - s
                d, k, first = -1.0, -k, i
        start[i], bottom[i], top[i], direction[i], skipped[i] = first, b, t, d, k
    return Bricks(start, bottom, top, direction, skipped)


def triples(ts: np.ndarray, made: Bricks) -> pl.DataFrame:
    """The rows of three bricks: one per candle from the third brick on.

    - ``ts``: the candle, at whose close the row is known;
    - ``row_id``: the number of its current brick;
    - ``start``: the ``ts`` of the candle that started the first of the three bricks;
    - ``direction``: half the direction of the middle brick plus a quarter of the current
      one's, so -0.75, -0.25, 0.25 or 0.75;
    - ``bottom``, ``top``: the lowest bottom and the highest top of the three.
    """
    number = made.number
    first_candle = np.flatnonzero(np.diff(number, prepend=-1) != 0)
    rows = np.flatnonzero(number >= BRICKS_PER_ROW - 1)
    b0 = first_candle[number[rows] - 2]
    b1 = first_candle[number[rows] - 1]
    three = (b0, b1, rows)
    return pl.DataFrame(
        {
            "ts": ts[rows],
            ROW_ID: number[rows],
            "start": ts[b0],
            "direction": 0.5 * made.direction[b1] + 0.25 * made.direction[rows],
            "bottom": np.minimum.reduce([made.bottom[brick] for brick in three]),
            "top": np.maximum.reduce([made.top[brick] for brick in three]),
        },
        schema={
            "ts": pl.Int64,
            ROW_ID: pl.Int64,
            "start": pl.Int64,
            "direction": pl.Float64,
            "bottom": pl.Float64,
            "top": pl.Float64,
        },
    )


def renko_rows(frame: pl.DataFrame, size: np.ndarray) -> pl.DataFrame:
    """The triples of the candles in ``frame`` (``ts``, high, low, close) with the brick height
    ``size`` of each candle, the bricks starting on its first candle."""
    made = bricks(
        frame["close"].to_numpy(), frame["high"].to_numpy(), frame["low"].to_numpy(), size
    )
    return triples(frame["ts"].cast(pl.Int64).to_numpy(), made)


def renko_triples(frame: pl.DataFrame, params: RenkoTriples) -> pl.DataFrame:
    """``renko-triples@1``: the rows of three Renko bricks, each brick ``atr_multiple`` ATRs
    high (``atr@1`` of ``atr_window`` candles) at the candle that starts it.

    The bricks start on the first candle whose ATR is out of its warm-up, so a brick's height
    is ``atr_multiple`` times the ``atr`` feature of its first candle, whatever candle the
    frame starts on.
    """
    first = ind.atr_warm_up(ind.Atr(window=params.atr_window))
    atr = ind.atr(frame, ind.Atr(window=params.atr_window)).slice(first).to_numpy()
    return renko_rows(frame.slice(first), params.atr_multiple * atr)


def direction(rows: pl.DataFrame, _params: BaseModel) -> pl.Series:
    """``renko-direction@1``: the direction of each row's triple (``triples``)."""
    return rows["direction"]


def candles_per_row(params: RenkoTriples) -> float:
    """About how many candles one finished row takes: one brick."""
    return CANDLES_PER_BRICK + CANDLES_PER_BRICK_PER_ATR * params.atr_multiple


def warm_up(params: RenkoTriples) -> int:
    """The candles before the first row: the ATR's warm-up, then the first two bricks."""
    first = ind.atr_warm_up(ind.Atr(window=params.atr_window))
    return first + math.ceil((BRICKS_PER_ROW - 1) * candles_per_row(params))
