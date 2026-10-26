"""The indicator blocks against the supervisor's own indicators: from his candles they give his
``indicators`` (research #118). His data is not public, so these tests run only where
``CANDLESTACK_SUPERVISOR_DATA`` names a local copy of it, one folder per stock (the layout of
``tests/unit/engine/test_supervisor.py``, with his indicators next to his candles)::

    <CANDLESTACK_SUPERVISOR_DATA>/AAPL/candles.parquet
                                      /indicators.parquet

Never commit that data. Without the variable the tests are skipped, as in CI. From
``backend/`` in Git Bash (in PowerShell, set ``$env:CANDLESTACK_SUPERVISOR_DATA`` first)::

    CANDLESTACK_SUPERVISOR_DATA=/data uv run pytest tests/unit/preprocessing/test_supervisor.py -v

His ``rel_*`` columns are in his ``normalized.parquet``, which a stock folder may also have
(``<CANDLESTACK_SUPERVISOR_DATA>/AAPL/normalized.parquet``); they are checked where it is, and
so is ``blesk-norm@1`` on his indicators. Where a folder has his ``all_triples.parquet``, the
whole BLESK NO-VOL manifest, of the @2 blocks, is built on his candles and its 23 normalized
features compared with his triples (``test_the_blesk_input_is_his_normalized_triples``; read
the counts with ``-rP``).

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

His recursive indicators are TA-Lib's (research #265): the @2 blocks, started as TA-Lib does,
give them on every row from his first candle, with no allowance for the first rows
(``test_the_2_block_gives_his_column_from_his_first_candle``).

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
from candlestack.preprocessing import CATALOG, build_input, parse_manifest

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
# His price-independent prices, in his normalized.parquet (research #117): plain features of
# the candle, not normalized any further.
RELATIVE = {
    "rel_open": "rel-open@1",
    "rel_high": "rel-high@1",
    "rel_low": "rel-low@1",
    "rel_close": "rel-close@1",
}
# His recursive columns, each with the @2 block, started as TA-Lib does, that gives it.
TALIB: dict[str, tuple[str, dict[str, Any]]] = {
    "rsi": ("rsi@2", {}),
    "tsi": ("tsi@2", {}),
    "atr": ("atr@2", {}),
    "adx": ("adx@2", {}),
    "ema20": ("ema@2", {"window": 20}),
    "ema50": ("ema@2", {"window": 50}),
    "macd_diff": ("macd-diff@2", {}),
}
EXACT = {"squeeze", "is_last_bar"}
RECURSIVE = set(TALIB)
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


@pytest.mark.parametrize("column", list(TALIB))
@pytest.mark.parametrize("stock", STOCKS)
def test_the_2_block_gives_his_column_from_his_first_candle(stock: str, column: str) -> None:
    """The @2 block on his candles gives his column on every one of his rows, from the first:
    his first candle with every indicator is past each block's warm-up."""
    block_id, given = TALIB[column]
    block = CATALOG[block_id]
    frame = candles(stock)
    ours = frame.select("unix", ours=block.run(frame, block.params.model_validate(given)))
    his_rows = indicators(stock).select("unix", his=column)
    rows = his_rows.join(ours, on="unix", how="left")
    a, b = rows["ours"].to_numpy(), rows["his"].to_numpy()

    within = np.isclose(a, b, rtol=1e-6, atol=1e-8)
    # Aggregates only, never his values.
    assert within.all(), (
        f"{stock} {column}: {int((~within).sum())} of {rows.height} rows differ, "
        f"first at row {int(np.argmin(within))}, max |diff| {float(np.nanmax(np.abs(a - b))):.3g}"
    )


@pytest.mark.parametrize("column", list(RELATIVE))
@pytest.mark.parametrize("stock", STOCKS)
def test_the_block_gives_his_relative_price(stock: str, column: str) -> None:
    normalized = ROOT / stock / "normalized.parquet"
    if not normalized.is_file():
        pytest.skip(f"{stock} has no normalized.parquet")
    block = CATALOG[RELATIVE[column]]
    frame = candles(stock)
    ours = frame.select("unix", ours=block.run(frame, block.params()))
    rows = ours.slice(block.warm_up(block.params())).join(
        pl.read_parquet(normalized, columns=["unix", column]).rename({column: "his"}),
        on="unix",
        how="inner",
    )
    if column == "rel_close":
        # His first row has no previous close in his normalized data and holds 1 instead.
        rows = rows.slice(1)
    assert rows.height > 0, f"{stock} has no row of {column} after the warm-up"
    a, b = rows["ours"].to_numpy(), rows["his"].to_numpy()

    within = np.isclose(a, b, rtol=1e-6, atol=1e-8)
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


# blesk-norm@1 (research #262): his normalized columns from his indicators and his close. The
# rel_* columns are features already (above), which blesk-norm@1 keeps as they are.
NORMALIZED = {column: block for column, (block, _) in COLUMNS.items()}


@pytest.mark.parametrize("column", list(NORMALIZED))
@pytest.mark.parametrize("stock", STOCKS)
def test_blesk_norm_gives_his_normalized_column(stock: str, column: str) -> None:
    normalized = ROOT / stock / "normalized.parquet"
    if not normalized.is_file():
        pytest.skip(f"{stock} has no normalized.parquet")
    block = CATALOG["blesk-norm@1"]
    given = pl.read_parquet(ROOT / stock / "indicators.parquet", columns=["unix", "close", column])
    ours = given.select(
        "unix", ours=block.run(given[column], given["close"], NORMALIZED[column], block.params())
    )
    rows = ours.join(
        pl.read_parquet(normalized, columns=["unix", column]).rename({column: "his"}),
        on="unix",
        how="inner",
    )
    assert rows.height == given.height, f"{stock} {column}: rows of his not in both files"
    a, b = rows["ours"].to_numpy(), rows["his"].to_numpy()

    within = np.isclose(a, b, rtol=1e-6, atol=1e-8)
    # Aggregates only, never his values.
    assert within.all(), (
        f"{stock} {column}: {int((~within).sum())} of {rows.height} rows differ, "
        f"max |diff| {float(np.nanmax(np.abs(a - b))):.3g}"
    )


# The BLESK NO-VOL input (docs/preprocessing.md): his 23 features in their order, each block
# with his parameters (the defaults but the EMAs') and blesk-norm@1; the recursive indicators
# are the @2 blocks, started as TA-Lib does, as his are.
BLESK_FEATURES: dict[str, tuple[str, dict[str, Any]]] = {
    **{column: TALIB.get(column, COLUMNS[column]) for column in list(COLUMNS)[:-1]},
    "direction": ("renko-direction@1", {}),
    **{column: (block, {}) for column, block in RELATIVE.items()},
    "is_last_bar": COLUMNS["is_last_bar"],
}
# His bricks: 0.6 x TA-Lib's ATR of 14 candles at the candle that starts each brick, the first
# brick on his first candle with every indicator, the candle 49.
RENKO = {"atr_multiple": 0.6, "atr_window": 14, "atr_at": "brick_start", "first_brick": 49}
BLESK_NO_VOL: dict[str, Any] = {
    "version": 1,
    "input": {
        "timeframe": "30m",
        "session": "regular",
        "representation": {"id": "renko-triples@2", "params": RENKO},
        "features": [
            {"name": name, "id": block, "params": params, "norm": {"id": "blesk-norm@1"}}
            for name, (block, params) in BLESK_FEATURES.items()
        ],
        "window": {"length": 25, "last": "current"},
    },
    "signature": {
        "input": {"shape": [25, 23], "dtype": "float32"},
        "output": {"shape": [1], "dtype": "float32", "meaning": "score"},
    },
}
# blesk-norm@1 turns a price level into percent of the close, so the indicator tests' rtol
# 1e-6 of a price level is 1e-4 there. Seen: on stretches of one close (a stock not yet listed,
# or suspended) his Bollinger bands lie up to 2.1e-5 off the close, the float noise of his
# standard deviation, where ours are on it.
PERCENT = {"bb_bbh", "bb_bbl", "kch_high", "kch_low", "sma", "ema20", "ema50", "atr"}
PERCENT_ATOL = 1e-4


def his_bricks(bricks: pl.DataFrame, his_rows: pl.DataFrame) -> pl.DataFrame:
    """Whether the three bricks of each of our triples are his: its first candle, bottom and top
    are his, and so are those of every triple since the candle its first brick starts. One row
    alone could match by chance where the bricks are not his (seen once, on SHY)."""
    rows = bricks.join(his_rows, on="ts", how="left")
    same = (
        (rows["start"] == rows["his_start"]).fill_null(value=False).to_numpy()
        & np.isclose(rows["bottom"].to_numpy(), rows["his_bottom"].to_numpy(), rtol=1e-6, atol=0)
        & np.isclose(rows["top"].to_numpy(), rows["his_top"].to_numpy(), rtol=1e-6, atol=0)
    )
    differ = np.concatenate([[0], np.cumsum(~same)])
    since = np.searchsorted(rows["ts"].to_numpy(), rows["start"].to_numpy(), side="left")
    return rows.select("ts", his_bricks=pl.Series(differ[1:] - differ[since] == 0))


def his_triples(triples: Path, names: list[str]) -> pl.DataFrame:
    """His triples by our ``ts``, with his bricks' first candle and edges and his features."""
    return pl.read_parquet(triples).select(
        ts=pl.col("end_unix").cast(pl.Int64) - HALF_HOUR,
        his_start=pl.col("start_unix").cast(pl.Int64) - HALF_HOUR,
        his_bottom=pl.col("bottom").cast(pl.Float64),
        his_top=pl.col("top").cast(pl.Float64),
        **{f"his_{name}": pl.col(name).cast(pl.Float64) for name in names},
    )


@pytest.mark.parametrize("stock", STOCKS)
def test_the_blesk_input_is_his_normalized_triples(stock: str) -> None:
    """The whole BLESK NO-VOL manifest on his candles gives the 23 normalized features of his
    triples at each candle after the warm-up: the 22 of the candle on every row, the direction
    on the rows whose three bricks are his. With TA-Lib's ATR the bricks are his but where a
    close lies on a grid line (research #263, R10), after which they may part from his for a
    while or for good (test_supervisor_renko.py)."""
    triples = ROOT / stock / "all_triples.parquet"
    if not triples.is_file():
        pytest.skip(f"{stock} has no all_triples.parquet")
    frame = candles(stock)
    built = build_input(frame, parse_manifest(BLESK_NO_VOL))
    names = list(BLESK_FEATURES)
    ours = built.rows.filter(pl.all_horizontal(pl.col(names).is_finite()))
    renko = CATALOG["renko-triples@2"]
    bricks = renko.run(frame, renko.params.model_validate(RENKO))
    his_rows = his_triples(triples, names)
    rows = (
        ours.join(his_rows, on="ts", how="inner")
        .join(
            his_bricks(bricks, his_rows.select("ts", "his_start", "his_bottom", "his_top")),
            on="ts",
            how="left",
        )
        .with_columns(tie=pl.col("ts").is_in(frame.filter(squeeze_tie())["ts"].implode()))
    )
    # Our rows are his but before his first, where a close on a grid line made our third brick
    # before his (seen on DE and DOC, on his third candle).
    early = ours.filter(pl.col("ts") < his_rows["ts"].min()).height
    wrong = {
        name: int((~within).sum())
        for name in BLESK_FEATURES
        if not (within := as_his(rows, name)).all()
    }

    # Aggregates only, never his values.
    print(  # noqa: T201
        f"{stock}: {rows.height} of his {his_rows.height} rows, the direction on the "
        f"{int(rows['his_bricks'].sum())} whose bricks are his"
    )
    assert rows.height + early == ours.height, f"{stock}: {ours.height - rows.height} rows not his"
    assert not wrong, f"{stock}: rows that differ, of {rows.height}: {wrong}"


def as_his(rows: pl.DataFrame, name: str) -> np.ndarray:
    """Where our feature ``name`` is his on the rows, within its tolerance (above); the
    direction counts only on the rows whose bricks are his."""
    a, b = rows[name].to_numpy(), rows[f"his_{name}"].to_numpy()
    if name == "squeeze":
        within = (a == b) | rows["tie"].to_numpy()
    elif name in EXACT:
        within = a == b
    else:
        within = np.isclose(a, b, rtol=1e-6, atol=PERCENT_ATOL if name in PERCENT else 1e-8)
    if name == "direction":
        within |= ~rows["his_bricks"].to_numpy()
    return within
