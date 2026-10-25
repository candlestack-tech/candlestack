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
``atol`` 1e-8, and the 0/1 ``squeeze`` exactly.
"""

import functools
import os
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

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
}
EXACT = {"squeeze"}

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
    frame = pl.read_parquet(ROOT / stock / "candles.parquet")
    return frame.select(
        "unix",
        ts=pl.col("unix").cast(pl.Int64) - HALF_HOUR,
        open="open",
        high="high",
        low="low",
        close="close",
        volume=pl.col("volume_base").cast(pl.Float64),
    ).sort("ts")


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
    ours = frame.select("unix").with_columns(ours=block.run(frame, params))
    rows = ours.slice(block.warm_up(params)).join(
        indicators(stock).select("unix", his=column), on="unix", how="inner"
    )
    assert rows.height > 0, f"{stock} has no row of {column} after the warm-up"
    a, b = rows["ours"].to_numpy(), rows["his"].to_numpy()

    within = a == b if column in EXACT else np.isclose(a, b, rtol=1e-6, atol=1e-8)
    # Aggregates only, never his values.
    assert within.all(), (
        f"{stock} {column}: {int((~within).sum())} of {rows.height} rows differ, "
        f"max |diff| {float(np.nanmax(np.abs(a - b))):.3g}"
    )
