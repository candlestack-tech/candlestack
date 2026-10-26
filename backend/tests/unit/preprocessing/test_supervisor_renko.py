"""The Renko blocks against the supervisor's own bricks and triples (research #263). His data is
not public, so these tests run only where ``CANDLESTACK_SUPERVISOR_DATA`` names a local copy
of it, one folder per stock, as ``test_supervisor.py``::

    <CANDLESTACK_SUPERVISOR_DATA>/AAPL/candles.parquet
                                      /indicators.parquet
                                      /all_renkos.parquet
                                      /all_triples.parquet

Never commit that data. Without the variable the tests are skipped, as in CI. From
``backend/`` in Git Bash (in PowerShell, set ``$env:CANDLESTACK_SUPERVISOR_DATA`` first)::

    CANDLESTACK_SUPERVISOR_DATA=/data uv run pytest \\
        tests/unit/preprocessing/test_supervisor_renko.py -n 4 -p no:cacheprovider -rP

His rows are labelled by the candle's close in ``unix`` seconds, ours by its open, half an
hour before. Every stock folder with the four files is checked:

- **The rules** (``test_the_bricks_are_his`` and ``test_the_triples_are_his``): the brick loop
  on his candles with his ATR (``indicators.atr``) from his first ``indicators`` candle gives
  his ``all_renkos`` (start, bottom, top, direction, skipped, finished) and, from those
  bricks, the structural columns of his ``all_triples`` (start, end, direction, finished,
  bottom, top and the OHLCV of the start and end candles); his 28 normalized features are
  #275's. His edges are float32, so they are compared within ``rtol`` 1e-6; everything else
  exactly. Two known exceptions, each handled narrowly:

  - **A close on a grid line** (research #263, R10): where a close lies on a line of the grid
    of the current brick within float rounding (``ON_THE_LINE`` of the close), his arithmetic
    and our float64 may pick the cells on either side, as on COST 2023-08-30 11:00. At such a
    candle, and only there, the test takes his choice (the cells he skipped, from our brick
    before it) and goes on with our loop from it; every other row must then be his.
  - **His missing first row** (R9): on some stocks, ABNB among them, his ``all_renkos`` has no
    row for his first candle, though the next rows name it as their brick's start. Only that
    one row may be missing.

- **Our own ATR** (``test_how_many_stocks_line_up_with_our_own_atr``): the block as a backtest
  runs it, on his candles from their first one with ``atr@1``, against his triples. Bricks
  depend on the whole history and the two ATRs differ in their warm-up, so this is a report,
  not a check: how many stocks line up with his for good, and from which year. Read it with
  ``-rP``.

Only counts are reported, never his values.
"""

import collections
import os
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.preprocessing import CATALOG
from candlestack.preprocessing.renko import Brick, Bricks, bricks, triples

DATA = os.environ.get("CANDLESTACK_SUPERVISOR_DATA", "")
ROOT = Path(DATA)
FILES = ("candles", "indicators", "all_renkos", "all_triples")
HALF_HOUR = 1800  # his timeframe: the shift from his close labels to our open labels
MULTIPLE = 0.6  # his bricks: 0.6 x ATR(14)
# A close this close to a line of the brick's grid, relative to the close, is on it: a few
# float32 steps (1.2e-7), as his edges are stored.
ON_THE_LINE = 1e-6
RTOL = 1e-6
OHLCV = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume_base"}

pytestmark = pytest.mark.skipif(
    not DATA, reason="CANDLESTACK_SUPERVISOR_DATA does not name the supervisor's data"
)


def stock_folders() -> list[str]:
    if not DATA or not ROOT.is_dir():
        return []
    return sorted(
        path.name
        for path in ROOT.iterdir()
        if path.is_dir()
        and not path.name.startswith("_")
        and all((path / f"{name}.parquet").is_file() for name in FILES)
    )


STOCKS = stock_folders()


def his(stock: str, name: str, columns: list[str] | None = None) -> pl.DataFrame:
    return pl.read_parquet(ROOT / stock / f"{name}.parquet", columns=columns)


def his_candles(stock: str) -> pl.DataFrame:
    """His candles with his ATR, from his first ``indicators`` candle."""
    return his(stock, "indicators", ["unix", *OHLCV.values(), "atr"]).sort("unix")


def on_the_line(close: float, before: Brick, size: float) -> bool:
    """Whether the close lies on a line of the grid of the brick before it, within float
    rounding: its edges, and the lines a whole number of heights beyond them."""
    beyond = (
        close - before.top if close > (before.bottom + before.top) / 2 else before.bottom - close
    )
    cells = max(round(beyond / size), 0)
    return abs(beyond - cells * size) <= ON_THE_LINE * abs(close)


def his_choice(before: Brick, mine: dict[str, Any], size: float, candle: int) -> Brick:
    """The brick he made at a candle (``mine``, his row of it, with its ``unix`` ``candle``),
    from our brick before it and the cells he skipped: the loop's arithmetic in float64, with
    his number of cells."""
    if mine["start_unix"] != candle:
        return before
    cells = abs(mine["skipped"])
    if mine["direction"] > 0:
        bottom = before.top + cells * size
        return Brick(bottom, bottom + size, 1.0, cells)
    top = before.bottom - cells * size
    return Brick(top - size, top, -1.0, -cells)


def brick_at(made: Bricks, row: int) -> Brick:
    return Brick(
        float(made.bottom[row]),
        float(made.top[row]),
        float(made.direction[row]),
        int(made.skipped[row]),
    )


def differ(made: Bricks, renkos: pl.DataFrame, unix: np.ndarray, offset: int) -> np.ndarray:
    """Where our bricks are not his, by candle; his rows begin at the candle ``offset``.

    ``finished`` is left out: it says whether the next candle starts a brick, so a candle
    where his and our bricks part would show first on the candle before it.
    """
    direction = renkos["direction"].cast(pl.Float64).fill_null(np.nan).to_numpy()
    ours = slice(offset, None)
    same = (
        (unix[made.start[ours]] == renkos["start_unix"].to_numpy())
        & np.isclose(made.bottom[ours], renkos["bottom"].to_numpy(), rtol=RTOL, atol=0)
        & np.isclose(made.top[ours], renkos["top"].to_numpy(), rtol=RTOL, atol=0)
        & (
            (made.direction[ours] == direction)
            | (np.isnan(made.direction[ours]) & np.isnan(direction))
        )
        & (made.skipped[ours] == renkos["skipped"].to_numpy())
    )
    return np.concatenate([np.zeros(offset, dtype=bool), ~same])


def stitched(made: Bricks, row: int, brick: Brick, rest: Bricks, start: int) -> Bricks:
    """``made`` up to the candle before ``row``, ``brick`` (started at ``start``) at it, then
    ``rest``, the loop's bricks of the candles after it."""
    cut = slice(0, row)
    after = np.where(rest.start < 0, start, rest.start + row + 1)
    return Bricks(
        start=np.concatenate([made.start[cut], [start], after]),
        bottom=np.concatenate([made.bottom[cut], [brick.bottom], rest.bottom]),
        top=np.concatenate([made.top[cut], [brick.top], rest.top]),
        direction=np.concatenate([made.direction[cut], [brick.direction], rest.direction]),
        skipped=np.concatenate([made.skipped[cut], [brick.skipped], rest.skipped]),
    )


def rebuilt(stock: str) -> tuple[pl.DataFrame, Bricks, int]:
    """His candles, our bricks of them with his ATR, and on how many closes on a grid line
    the bricks took his choice.

    His ``all_renkos`` is checked on the way: it has a row for every candle but at most the
    first, and our bricks differ from his only from a close on a grid line on.
    """
    candles = his_candles(stock)
    unix = candles["unix"].cast(pl.Int64).to_numpy()
    close, high, low = (candles[name].to_numpy() for name in ("close", "high", "low"))
    size = MULTIPLE * candles["atr"].to_numpy()
    renkos = his(stock, "all_renkos").sort("end_unix")
    missing = set(unix.tolist()) - set(renkos["end_unix"].to_list())
    assert missing <= {int(unix[0])}, f"{stock}: {len(missing)} candles have no row of his"
    assert renkos.height == candles.height - len(missing), f"{stock}: rows of his not ours"
    # Without his first row, our first is checked through the start of his next rows.
    offset = len(missing)
    made = bricks(close, high, low, size)
    choices = 0
    while (wrong := differ(made, renkos, unix, offset)).any():
        row = int(np.argmax(wrong))
        # Aggregates only, never his values.
        why = (
            f"{stock}: {int(wrong.sum())} of {renkos.height} rows differ from row {row} on, "
            "not from a close on a grid line"
        )
        assert row > 0, why
        before = brick_at(made, row - 1)
        assert on_the_line(float(close[row]), before, float(size[row])), why
        assert choices < candles.height, f"{stock}: no end of the closes on a grid line"
        mine = renkos.row(row - offset, named=True)
        choice = his_choice(before, mine, float(size[row]), int(unix[row]))
        start = row if mine["start_unix"] == unix[row] else int(made.start[row - 1])
        later = slice(row + 1, None)
        rest = bricks(close[later], high[later], low[later], size[later], after=choice)
        made = stitched(made, row, choice, rest, start)
        choices += 1
    finished = made.finished[offset:] != renkos["finished"].to_numpy()
    assert not finished.any(), f"{stock}: {int(finished.sum())} rows differ in finished"
    return candles, made, choices


def test_some_stock_has_his_bricks_and_triples() -> None:
    assert STOCKS, f"No folder in {ROOT} has {', '.join(f'{name}.parquet' for name in FILES)}"


@pytest.mark.parametrize("stock", STOCKS)
def test_the_bricks_are_his(stock: str) -> None:
    _, made, choices = rebuilt(stock)

    # Aggregates only, never his values.
    print(f"{stock}: {made.start.size} candles, {choices} closes on a grid line")  # noqa: T201


@pytest.mark.parametrize("stock", STOCKS)
def test_the_triples_are_his(stock: str) -> None:
    candles, made, _ = rebuilt(stock)
    unix = candles["unix"].cast(pl.Int64).to_numpy()
    ours = triples(unix, made).rename({"ts": "end_unix", "start": "start_unix"})
    ours = ours.with_columns(
        finished=(pl.col("row_id").shift(-1) != pl.col("row_id")).fill_null(value=False)
    )
    for prefix in ("start", "end"):
        ours = ours.join(
            candles.select(
                pl.col("unix").cast(pl.Int64).alias(f"{prefix}_unix"),
                *(pl.col(column).alias(f"{prefix}_{name}") for name, column in OHLCV.items()),
            ),
            on=f"{prefix}_unix",
            how="left",
        )
    theirs = his(stock, "all_triples").sort("end_unix")

    assert ours.height == theirs.height, f"{stock}: {ours.height} rows, his {theirs.height}"
    same = np.ones(ours.height, dtype=bool)
    for column in (
        "start_unix",
        "end_unix",
        "finished",
        *(f"{p}_{n}" for p in ("start", "end") for n in OHLCV),
    ):
        same &= (
            ours[column].cast(pl.Float64).to_numpy() == theirs[column].cast(pl.Float64).to_numpy()
        )
    for column in ("direction", "bottom", "top"):
        same &= np.isclose(ours[column].to_numpy(), theirs[column].to_numpy(), rtol=RTOL, atol=0)
    # Aggregates only, never his values.
    assert same.all(), f"{stock}: {int((~same).sum())} of {ours.height} rows differ"


def test_how_many_stocks_line_up_with_our_own_atr() -> None:
    """The block on his candles from their first one, its ATR ``atr@1``: from which year each
    stock's triples are his for good. A report, which never fails on the numbers."""
    block = CATALOG["renko-triples@1"]
    params = block.params()
    since: collections.Counter[str] = collections.Counter()
    for stock in STOCKS:
        frame = his(stock, "candles", ["unix", "date", *OHLCV.values()]).sort("unix")
        frame = frame.with_columns(ts=pl.col("unix").cast(pl.Int64) - HALF_HOUR)
        ours = block.run(frame, params)
        theirs = his(stock, "all_triples", ["start_unix", "end_unix", "direction", "bottom", "top"])
        theirs = theirs.select(
            ts=pl.col("end_unix").cast(pl.Int64) - HALF_HOUR,
            his_start=pl.col("start_unix").cast(pl.Int64) - HALF_HOUR,
            his_direction=pl.col("direction").cast(pl.Float64),
            his_bottom=pl.col("bottom").cast(pl.Float64),
            his_top=pl.col("top").cast(pl.Float64),
        )
        rows = ours.join(theirs, on="ts", how="inner").sort("ts")
        same = (
            (rows["start"] == rows["his_start"]).to_numpy()
            & (rows["direction"] == rows["his_direction"]).to_numpy()
            & np.isclose(
                rows["bottom"].to_numpy(), rows["his_bottom"].to_numpy(), rtol=RTOL, atol=0
            )
            & np.isclose(rows["top"].to_numpy(), rows["his_top"].to_numpy(), rtol=RTOL, atol=0)
        )
        if not same.size or not same[-1]:
            since["never"] += 1
            continue
        first = int(same.size - np.argmin(same[::-1])) if not same.all() else 0
        day = frame.filter(pl.col("ts") == rows["ts"][first])["date"][0]
        since[str(day.year)] += 1

    # Aggregates only, never his values.
    print(  # noqa: T201
        f"Our own ATR: of {len(STOCKS)} stocks, the triples line up with his for good from "
        + ", ".join(f"{year}: {count}" for year, count in sorted(since.items()) if year != "never")
        + f"; never: {since['never']}"
    )
