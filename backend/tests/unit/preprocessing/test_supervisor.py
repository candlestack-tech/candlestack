"""The indicator blocks against the supervisor's own indicators: from his candles they give his
``indicators`` (research #118). His data is not public, so these tests run only where
``CANDLESTACK_SUPERVISOR_DATA`` names a local copy of it, one folder per stock (the layout of
``tests/unit/engine/test_supervisor.py``, with his indicators next to his candles)::

    <CANDLESTACK_SUPERVISOR_DATA>/AAPL/candles.parquet
                                      /indicators.parquet

Never commit that data. Without the variable the tests are skipped, as in CI. From
``backend/`` in Git Bash (in PowerShell, set ``$env:CANDLESTACK_SUPERVISOR_DATA`` first)::

    CANDLESTACK_SUPERVISOR_DATA=/data uv run pytest tests/unit/preprocessing/test_supervisor.py -v

Every stock folder with both files is checked, each of his columns a test of its own. His rows
are labelled by the candle's close in ``unix`` seconds, and his volume is ``volume_base``. A
column is compared on every row after the block's warm-up (his first rows differ for the
recursive indicators, research #118) with the tolerance of the research: ``rtol`` 1e-6 and
``atol`` 1e-8, and the 0/1 ``squeeze`` and ``is_last_bar`` exactly. Two exceptions, both float
noise:

- A recursive indicator weighs its first candles under 1e-6 once its warm-up ends, and his
  started elsewhere (on a longer history, or another first candle), so on the first
  ``EARLY_ROWS`` rows after the warm-up it may differ from his by that much: ``rtol`` 1e-4,
  ``atol`` 1e-6 there (seen: up to 2e-5 of the value, within the first 40 rows).
- ``squeeze`` compares a Bollinger band with the channel's edge; where they are equal but for
  the last bits of a float (within ``TIE`` of the close), his 0 or 1 may be the other one.

His ``is_last_bar`` and the ``end_bar`` of his candles mark the last candle of each session.
The data module marks it from Alpaca's calendar, which a test cannot ask, so here the calendar
is made from the days of his candles, 09:30-16:00 New York, with the early closes below at
13:00.
"""

import functools
import os
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.data import Timeframe, parse_calendar, session_last
from candlestack.preprocessing import CATALOG

DATA = os.environ.get("CANDLESTACK_SUPERVISOR_DATA", "")
ROOT = Path(DATA)
FILES = ("candles", "indicators")
HALF_HOUR = 1800  # his timeframe: the shift from his close labels to our open labels
# His columns of the 25 x 23 input that are computed on the candles, with the block and the
# parameters that give each (the defaults are his).
COLUMNS: dict[str, tuple[str, dict[str, Any]]] = {
    "bb_bbh": ("bollinger-high@1", {}),
    "bb_bbl": ("bollinger-low@1", {}),
    "kch_high": ("keltner-high@1", {}),
    "kch_low": ("keltner-low@1", {}),
    "squeeze": ("squeeze@1", {}),
    "rsi": ("rsi@1", {}),
    "roc": ("roc@1", {}),
    "tsi": ("tsi@1", {}),
    "atr": ("atr@1", {}),
    "adx": ("adx@1", {}),
    "aroon_up": ("aroon-up@1", {}),
    "aroon": ("aroon@1", {}),
    "aroon_down": ("aroon-down@1", {}),
    "sma": ("sma@1", {}),
    "ema20": ("ema@1", {"window": 20}),
    "ema50": ("ema@1", {"window": 50}),
    "macd_diff": ("macd-diff@1", {}),
    "is_last_bar": ("session-last-candle@1", {}),
}
EXACT = {"squeeze", "is_last_bar"}
RECURSIVE = {"rsi", "tsi", "atr", "adx", "ema20", "ema50", "macd_diff"}
EARLY_ROWS = 64
TIE = 1e-12
HIGH, LOW, CLOSE = pl.col("high"), pl.col("low"), pl.col("close")
# The NYSE early closes (13:00 New York) of his years, 2016 to August 2026, public exchange
# facts; 2026 has none before November.
EARLY_CLOSES = frozenset(
    date.fromisoformat(day)
    for day in (
        "2016-11-25",
        "2017-07-03",
        "2017-11-24",
        "2018-07-03",
        "2018-11-23",
        "2018-12-24",
        "2019-07-03",
        "2019-11-29",
        "2019-12-24",
        "2020-11-27",
        "2020-12-24",
        "2021-11-26",
        "2022-11-25",
        "2023-07-03",
        "2023-11-24",
        "2024-07-03",
        "2024-11-29",
        "2024-12-24",
        "2025-07-03",
        "2025-11-28",
        "2025-12-24",
    )
)

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


@functools.lru_cache(maxsize=1)
def candles(stock: str) -> pl.DataFrame:
    """His candles as the data module gives them, with the mark of each session's last one."""
    frame = pl.read_parquet(ROOT / stock / "candles.parquet")
    frame = frame.select(
        "unix",
        "end_bar",
        ts=pl.col("unix").cast(pl.Int64) - HALF_HOUR,
        open="open",
        high="high",
        low="low",
        close="close",
        volume=pl.col("volume_base").cast(pl.Float64),
        day=pl.col("date").dt.date(),
    ).sort("ts")
    sessions = parse_calendar(
        {"date": day.isoformat(), "open": "09:30", "close": _close(day)}
        for day in frame["day"].unique()
    )
    return frame.with_columns(session_last(frame, Timeframe.M30, sessions))


def _close(day: date) -> str:
    return "13:00" if day in EARLY_CLOSES else "16:00"


def squeeze_tie() -> pl.Expr:
    """Whether the Bollinger bands of ``squeeze@1``'s defaults lie on the channel's edges
    within ``TIE`` of the close, where float noise decides the squeeze. Both share the mean
    close of 20 candles, so the bands touch the edges where 2 deviations equal 1.5 mean true
    ranges."""
    before = CLOSE.shift(1)
    true_range = pl.max_horizontal(HIGH - LOW, (HIGH - before).abs(), (LOW - before).abs())
    gap = 2 * CLOSE.rolling_std(20, ddof=0) - 1.5 * true_range.rolling_mean(20)
    return (gap.abs() < TIE * CLOSE).fill_null(value=False)


@functools.lru_cache(maxsize=1)
def indicators(stock: str) -> pl.DataFrame:
    return pl.read_parquet(ROOT / stock / "indicators.parquet", columns=["unix", *COLUMNS])


def test_some_stock_has_his_candles_and_indicators() -> None:
    assert STOCKS, f"No folder in {ROOT} has both {', '.join(f'{name}.parquet' for name in FILES)}"


@pytest.mark.parametrize("column", list(COLUMNS))
@pytest.mark.parametrize("stock", STOCKS)
def test_the_block_gives_his_column(stock: str, column: str) -> None:
    block_id, given = COLUMNS[column]
    block = CATALOG[block_id]
    params = block.params.model_validate(given)
    frame = candles(stock)
    ours = frame.select("unix", ours=block.run(frame, params), tie=squeeze_tie())
    rows = ours.slice(block.warm_up(params)).join(
        indicators(stock).select("unix", his=column), on="unix", how="inner"
    )
    assert rows.height > 0, f"{stock} has no row of {column} after the warm-up"
    a, b = rows["ours"].to_numpy(), rows["his"].to_numpy()

    if column == "squeeze":
        within = (a == b) | rows["tie"].to_numpy()
    elif column in EXACT:
        within = a == b
    else:
        within = np.isclose(a, b, rtol=1e-6, atol=1e-8)
    if column in RECURSIVE:
        early = np.isclose(a[:EARLY_ROWS], b[:EARLY_ROWS], rtol=1e-4, atol=1e-6)
        within[:EARLY_ROWS] |= early
    # Aggregates only, never his values.
    assert within.all(), (
        f"{stock} {column}: {int((~within).sum())} of {rows.height} rows differ, "
        f"max |diff| {float(np.nanmax(np.abs(a - b))):.3g}"
    )


@pytest.mark.parametrize("stock", STOCKS)
def test_the_last_candle_of_each_session_is_his_end_bar(stock: str) -> None:
    frame = candles(stock)
    block = CATALOG["session-last-candle@1"]

    ours = block.run(frame, block.params())
    his = frame["end_bar"].cast(pl.Float64)

    # Aggregates only, never his values.
    differ = int((ours != his).sum())
    assert differ == 0, f"{stock}: {differ} of {frame.height} candles differ in end_bar"
    assert ours.sum() == frame["day"].n_unique(), f"{stock}: not one last candle a day"
