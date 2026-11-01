"""ENGINE_VERSION goes up with every change to the rules: a hash of the engine's output on a
fixed example is recorded per version, so a change of the output under the same version fails.
"""

import hashlib
import json
import re
from typing import Any

import polars as pl
import pytest

from candlestack.engine import ENGINE_VERSION, Settings, backtest

# The sha256 of output_json() for each ENGINE_VERSION. Keep the old entries: they record what
# each version produced.
OUTPUT_SHA256 = {
    # 0.1.0 on the 40-candle example of its time; 0.2.0 extended the example; 0.3.0 added the
    # two runs with a drawdown stop; 0.4.0 added the two runs at half the capital.
    "0.1.0": "c5fe0ee75777b3d191cdbad159ff1de43e83766eff772bb7409a723bdc9261c9",
    "0.2.0": "d6ca5c67715702cd7cfafadcf9ab8a361b3083be7ffbb5b9c0d99129df3401b0",
    "0.3.0": "454c3c7c0aa7431e7b15e3aa125781f29367f331ad93c677e50f69be94c1c9d8",
    "0.4.0": "8bc8dd779aca6f866ddcb5f06a1cbbef7e7f358a2b907b4b7db0a1addb14f3c7",
}

INPUT_SCHEMA = {
    "ts": pl.Int64,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "signal": pl.Float64,
}
START = 1_704_205_800  # 2024-01-02 14:30 UTC

# 50 half-hour candles (open, high, low, close, signal). Both fills, with a 5% stop, a 5 bps fee
# and 2 bps of slippage, trade the same story: a long flipped to a short, a long stopped out on
# its low, a short that gaps out at the open, two more flips, a long closed by the signal on a
# candle that opens beyond its stop, a short stopped out on its high and a change on the last
# row. With next_open the order closes that long at the open (rule 2), the short's stop is on
# its entry candle, where only a touch counts (rule 4), and the last change is never filled, so
# the long ends at the last close; signal_close closes it on the last row.
EXAMPLE = pl.DataFrame(
    [
        (START + 1800 * row, *candle)
        for row, candle in enumerate(
            [
                (100.0, 101.0, 99.0, 100.0, 0),
                (100.0, 101.5, 99.5, 101.0, 0),
                (101.0, 102.0, 100.5, 101.5, 1),  # go long
                (101.8, 103.0, 101.2, 102.6, 1),
                (102.6, 104.0, 102.0, 103.5, 1),
                (103.5, 104.2, 102.8, 103.0, 1),
                (103.0, 103.4, 101.5, 102.0, -1),  # flip to short
                (101.6, 102.2, 100.4, 100.8, -1),
                (100.8, 101.0, 99.0, 99.5, -1),
                (99.5, 100.2, 98.6, 99.0, -1),
                (99.0, 99.4, 97.8, 98.2, 0),  # go flat
                (98.4, 99.0, 97.9, 98.6, 0),
                (98.6, 99.3, 98.1, 99.0, 1),  # go long: the stop is about 94.1-94.3
                (99.2, 100.0, 98.8, 99.6, 1),
                (99.6, 99.8, 96.5, 97.0, 1),
                (97.0, 97.4, 93.5, 94.0, 1),  # the low reaches the stop
                (94.0, 95.0, 93.6, 94.8, 1),  # still long: no new decision
                (94.8, 95.2, 94.1, 94.5, 0),
                (94.5, 95.0, 94.0, 94.3, -1),  # go short: the stop is about 98.9-99.0
                (94.2, 94.6, 93.2, 93.5, -1),
                (93.5, 94.0, 92.6, 93.0, -1),
                (99.8, 100.5, 99.2, 100.0, -1),  # opens beyond the stop: out at the open
                (100.0, 100.8, 99.5, 100.4, -1),
                (100.4, 100.9, 99.9, 100.2, 0),
                (100.2, 100.6, 99.6, 100.0, 1),  # go long
                (100.3, 101.5, 100.1, 101.2, 1),
                (101.2, 102.8, 101.0, 102.5, 1),
                (102.5, 103.0, 101.9, 102.2, 1),
                (102.2, 102.6, 100.8, 101.0, -1),  # flip to short
                (100.7, 101.0, 99.4, 99.8, -1),
                (99.8, 100.1, 98.7, 99.0, -1),
                (99.0, 99.5, 98.2, 98.6, 0),  # go flat
                (98.6, 99.2, 98.3, 99.0, 0),
                (99.0, 99.6, 98.7, 99.4, -1),  # go short
                (99.3, 99.8, 98.5, 98.8, -1),
                (98.8, 99.1, 97.6, 97.9, -1),
                (97.9, 98.4, 97.2, 97.5, -1),
                (97.5, 97.9, 96.8, 97.2, 1),  # flip to long: the stop is about 92.3-92.4
                (97.2, 97.6, 96.5, 96.9, 1),
                (96.9, 97.3, 96.4, 97.0, 0),  # go flat
                (91.0, 91.5, 90.0, 90.5, 0),  # next_open: out at this open, beyond the stop
                (90.5, 91.0, 90.0, 90.8, 0),
                (90.8, 91.2, 90.4, 91.0, -1),  # go short: the stop is about 95.5
                (91.0, 96.0, 90.6, 95.0, -1),  # the high reaches it (next_open: entry candle)
                (95.0, 95.5, 94.5, 95.2, -1),  # still short: no new decision
                (95.2, 95.6, 94.8, 95.0, 0),
                (95.0, 95.4, 94.6, 95.3, 1),  # go long
                (95.4, 96.0, 95.0, 95.8, 1),
                (95.8, 96.2, 95.4, 96.0, 1),
                (96.0, 96.5, 95.7, 96.3, 0),  # go flat on the last row
            ]
        )
    ],
    schema=INPUT_SCHEMA,
    orient="row",
)
SETTINGS = [
    Settings(fill="signal_close", stop_loss=0.05, fee_bps=5, slippage_bps=2),
    Settings(fill="next_open", stop_loss=0.05, fee_bps=5, slippage_bps=2),
]
IDS = ["signal_close", "next_open"]
# The same two runs with a 2% drawdown stop: it closes the long decided on row 12 on row 14,
# before the stop would on row 15, and the run stays flat from there.
DRAWDOWN_SETTINGS = [settings.model_copy(update={"drawdown_stop": 0.02}) for settings in SETTINGS]
# The first two runs at half the capital.
HALF_SETTINGS = [settings.model_copy(update={"position_size": 0.5}) for settings in SETTINGS]


def as_json(frame: pl.DataFrame) -> dict[str, Any]:
    return {
        "schema": {name: str(dtype) for name, dtype in frame.schema.items()},
        "rows": frame.rows(),
    }


def output_json(settings_list: list[Settings]) -> str:
    """The trades and series of the given runs of EXAMPLE: column names and types, and the rows
    with each float written as its shortest repr, which reads back exactly. The values come
    from IEEE 754 arithmetic in Python, so the text is the same on every platform.

    Not the Parquet bytes: they also depend on how Polars writes the file (compression,
    encodings, statistics), which a Polars update may change while the output stays the same,
    and the test would then ask for a new ENGINE_VERSION without a change to the engine.
    """
    runs = []
    for settings in settings_list:
        result = backtest(EXAMPLE, settings)
        runs.append({"trades": as_json(result.trades), "series": as_json(result.series)})
    return json.dumps(runs)


def test_engine_version_is_major_minor_patch() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", ENGINE_VERSION)


@pytest.mark.parametrize("settings", SETTINGS, ids=IDS)
def test_the_example_has_flips_a_stop_a_gap_and_a_last_row_change_with_costs(
    settings: Settings,
) -> None:
    trades = backtest(EXAMPLE, settings).trades
    flips = trades.filter(
        (pl.col("exit_reason") == "signal")
        & (pl.col("close_ts") == pl.col("open_ts").shift(-1))
        & (pl.col("side") == -pl.col("side").shift(-1))
    )
    last = trades.row(-1, named=True)

    assert {"signal", "stop", "gap"} <= set(trades["exit_reason"])
    assert flips.height == 3
    assert (trades["fees"] > 0).all()
    assert (trades["slippage_cost"] > 0).all()
    assert EXAMPLE["signal"][-1] != EXAMPLE["signal"][-2]
    assert last["close_ts"] == EXAMPLE["ts"][-1]
    assert last["exit_reason"] == ("end" if settings.fill == "next_open" else "signal")


def test_with_next_open_the_example_fills_before_the_stop_and_stops_on_an_entry_candle() -> None:
    trades = backtest(EXAMPLE, SETTINGS[1]).trades
    opens = dict(EXAMPLE.select("ts", "open").rows())
    beyond = [
        trade
        for trade in trades.iter_rows(named=True)
        if trade["exit_reason"] == "signal"
        and trade["side"] * (opens[trade["close_ts"]] - trade["stop_price"]) <= 0
    ]
    on_entry = trades.filter(
        (pl.col("exit_reason") == "stop") & (pl.col("open_ts") == pl.col("close_ts"))
    )

    assert len(beyond) == 1  # closed by its order at an open beyond its stop: no gap (rule 2)
    assert on_entry.height == 1  # stopped on its entry candle, by a touch (rule 4)


@pytest.mark.parametrize("settings", DRAWDOWN_SETTINGS, ids=IDS)
def test_with_a_drawdown_stop_the_example_halts_on_the_long_before_its_stop(
    settings: Settings,
) -> None:
    result = backtest(EXAMPLE, settings)
    last = result.trades.row(-1, named=True)
    halt = EXAMPLE["ts"].to_list().index(last["close_ts"])

    assert result.trades["exit_reason"].to_list() == ["signal", "signal", "drawdown"]
    assert last["side"] == 1
    assert -0.05 < last["return"] < 0  # the stop, 5%, would have cost more
    assert halt == 14
    assert set(result.series["position"][halt:]) == {0.0}
    assert set(result.series["equity"][halt:]) == {result.series["equity"][halt]}
    assert result.series["equity"][halt] > 0


def test_a_position_of_the_whole_capital_gives_the_output_of_engine_0_3_0() -> None:
    digest = hashlib.sha256(output_json([*SETTINGS, *DRAWDOWN_SETTINGS]).encode()).hexdigest()

    assert digest == OUTPUT_SHA256["0.3.0"]


def test_the_output_on_the_example_is_the_one_recorded_for_this_engine_version() -> None:
    runs = [*SETTINGS, *DRAWDOWN_SETTINGS, *HALF_SETTINGS]
    digest = hashlib.sha256(output_json(runs).encode()).hexdigest()
    recorded = OUTPUT_SHA256.get(ENGINE_VERSION)

    assert recorded is not None, (
        f"No output hash is recorded for ENGINE_VERSION {ENGINE_VERSION}. A change of the "
        f"engine's output needs a new ENGINE_VERSION; record its hash in OUTPUT_SHA256 in "
        f'this file: "{ENGINE_VERSION}": "{digest}"'
    )
    assert digest == recorded, (
        f"The engine's output on the example changed, but ENGINE_VERSION is still "
        f"{ENGINE_VERSION}. Bump ENGINE_VERSION in candlestack/engine/version.py and record "
        f'the new hash under the new version in OUTPUT_SHA256: "<new version>": "{digest}"'
    )
