"""The Kagi line and its rows by hand, rule by rule: a few closes with a reversal amount of 1,
so every price is a whole or exact number."""

import math

import numpy as np
import polars as pl
import pytest

from candlestack.preprocessing import CATALOG
from candlestack.preprocessing import indicators as ind
from candlestack.preprocessing.kagi import (
    Kagi,
    Segments,
    candles_per_row,
    kagi_rows,
    rows,
    segments,
)


def made(closes: list[float], sizes: list[float] | None = None) -> Segments:
    """The line of candles with these closes and an amount of 1 each, or ``sizes``."""
    amounts = np.ones(len(closes)) if sizes is None else np.array(sizes)
    return segments(np.array(closes), amounts)


def nan_list(values: np.ndarray) -> list[float | None]:
    """The values with NaN as None, so lists of them compare."""
    return [None if math.isnan(value) else value for value in values.tolist()]


def test_the_line_starts_at_the_first_close_without_a_direction() -> None:
    # 10.5 and 9.5 are half the amount away from the start price 10: no segment yet.
    result = made([10.0, 10.5, 9.5])

    assert result.start.tolist() == [-1, -1, -1]
    assert nan_list(result.direction) == [None] * 3
    assert nan_list(result.yang) == [None] * 3
    assert result.origin.tolist() == result.extreme.tolist() == [10.0] * 3
    assert result.number.tolist() == [-1, -1, -1]
    assert rows(np.arange(3, dtype=np.int64), result).is_empty()


def test_the_first_segment_goes_up_from_the_start_price_and_is_yang() -> None:
    result = made([10.0, 10.5, 11.0])

    # The first close at least the amount above 10 starts it, from 10 to that close.
    assert result.start.tolist() == [-1, -1, 2]
    assert nan_list(result.direction) == [None, None, 1.0]
    assert result.origin.tolist()[2] == 10.0
    assert result.extreme.tolist()[2] == 11.0
    assert nan_list(result.yang) == [None, None, 1.0]


def test_the_first_segment_goes_down_from_the_start_price_and_is_yin() -> None:
    result = made([10.0, 9.5, 9.0])

    assert result.start.tolist() == [-1, -1, 2]
    assert nan_list(result.direction) == [None, None, -1.0]
    assert (result.origin[2], result.extreme[2]) == (10.0, 9.0)
    assert nan_list(result.yang) == [None, None, 0.0]


def test_a_close_beyond_the_extreme_extends_the_segment() -> None:
    # Up from 10: 12.5 extends it, 12 is less than the amount back, 13 extends it again.
    result = made([10.0, 11.0, 12.5, 12.0, 13.0])

    assert result.start.tolist() == [-1, 1, 1, 1, 1]
    assert result.extreme.tolist() == [10.0, 11.0, 12.5, 12.5, 13.0]
    assert result.origin.tolist()[1:] == [10.0] * 4


def test_a_close_the_amount_back_from_the_extreme_reverses_the_line() -> None:
    # The top 13, then 12: exactly the amount below it.
    result = made([10.0, 11.0, 13.0, 12.0])

    assert result.start.tolist() == [-1, 1, 1, 3]
    assert nan_list(result.direction) == [None, 1.0, 1.0, -1.0]
    # The new segment runs from the old extreme to the close.
    assert (result.origin[3], result.extreme[3]) == (13.0, 12.0)


def test_a_close_just_under_the_amount_back_reverses_nothing() -> None:
    result = made([10.0, 11.0, 13.0, 12.0625])

    assert result.start.tolist() == [-1, 1, 1, 1]
    assert result.extreme.tolist()[3] == 13.0


def test_a_close_far_back_is_one_reversal() -> None:
    # 8 amounts below the top: one new segment down to the close, not eight.
    result = made([10.0, 11.0, 13.0, 5.0])

    assert result.number.tolist() == [-1, 0, 0, 1]
    assert (result.origin[3], result.extreme[3], result.direction[3]) == (13.0, 5.0, -1.0)


def test_a_gap_is_a_move_like_any_other() -> None:
    """Candles carry no time here: the first candle of a day after an overnight gap is
    compared with the line like the next candle of the same day."""
    result = made([10.0, 9.0, 8.0, 15.0, 30.0])

    assert result.start.tolist() == [-1, 1, 1, 3, 3]
    assert result.origin.tolist()[3:] == [8.0, 8.0]
    assert result.extreme.tolist()[3:] == [15.0, 30.0]


def test_a_segment_keeps_the_amount_of_the_candle_that_started_it() -> None:
    sizes = [9.0, 1.0, 5.0, 5.0, 1.0, 1.0]

    result = made([10.0, 11.0, 12.0, 11.0, 12.5, 16.0], sizes)

    # Without a segment the current candle's amount counts: 1 at candle 1, not 9. The segment
    # up from candle 1 reverses by its 1 at candle 3, whose 5 the segment down then keeps:
    # 12.5 is 1.5 above its bottom 11, and 16 is 5.
    assert result.start.tolist() == [-1, 1, 1, 3, 3, 5]
    assert nan_list(result.direction) == [None, 1.0, 1.0, -1.0, -1.0, 1.0]
    assert result.origin.tolist() == [10.0, 10.0, 10.0, 12.0, 12.0, 11.0]
    assert result.extreme.tolist() == [10.0, 11.0, 12.0, 11.0, 11.0, 16.0]


def test_an_amount_of_zero_reverses_nothing() -> None:
    """Candles that never moved have an ATR of 0: no reversal by no amount, but a close beyond
    the extreme still extends the segment."""
    sizes = [1.0, 0.0, 3.0, 1.0, 0.0, 1.0, 1.0, 1.0]

    result = made([10.0, 12.0, 12.0, 11.0, 10.0, 7.0, 12.0, 6.0], sizes)

    # Candle 1 is 2 away with an amount of 0 and candle 2 2 away with 3; candle 3 starts the
    # segment up. Candle 4 reverses it and starts a segment down of the amount 0, which
    # extends to 7 and to 6 but never turns up again.
    assert result.start.tolist() == [-1, -1, -1, 3, 4, 4, 4, 4]
    assert result.extreme.tolist()[4:] == [10.0, 7.0, 7.0, 6.0]
    assert nan_list(result.direction)[3:] == [1.0, -1.0, -1.0, -1.0, -1.0]


def test_an_amount_in_percent_is_of_the_extreme_as_it_stands() -> None:
    closes = [200.0, 201.0, 202.0, 400.0, 396.5, 396.0, 399.5, 300.0, 303.0]

    result = segments(np.array(closes), np.full(len(closes), 1.0), percent=True)

    # 1 % of the start price 200 is 2, so 202 starts the segment up. 1 % of its top 400 is 4:
    # 396.5 is 3.5 below it, 396 reverses. 1 % of 396 is 3.96: 399.5 is 3.5 above. At the
    # bottom 300 the amount is 3, so 303 reverses.
    assert result.start.tolist() == [-1, -1, 2, 2, 2, 5, 5, 5, 8]
    assert result.extreme.tolist() == [
        200.0,
        200.0,
        202.0,
        400.0,
        400.0,
        396.0,
        396.0,
        300.0,
        303.0,
    ]
    assert result.origin.tolist()[8] == 300.0


def test_the_line_turns_yang_above_the_last_shoulder_and_yin_below_the_last_waist() -> None:
    closes = [10.0, 12.0, 14.0, 12.0, 11.0, 13.0, 13.5, 12.0, 10.0, 11.5, 14.0]

    result = made(closes)

    # Up to 14 (yang); down to 11, the shoulder 14 and no waist yet (yang); up to 13.5, the
    # waist 11 and under the shoulder (yang); down to 10, under the waist 11 (yin); up to
    # 11.5, under the shoulder 13.5 (yin across the reversal); 14, above it (yang).
    assert result.number.tolist() == [-1, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    assert nan_list(result.yang) == [None, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 1.0]


def test_a_reversal_beyond_the_last_shoulder_turns_the_line_at_its_candle() -> None:
    # Down to 8 (yin); up to 9, no shoulder yet (yin); down to 7.5, under the waist 8 (yin);
    # up to 10 at once, above the shoulder 9 (yang).
    result = made([10.0, 8.0, 9.0, 7.5, 10.0])

    assert result.number.tolist() == [-1, 0, 1, 2, 3]
    assert nan_list(result.yang) == [None, 0.0, 0.0, 0.0, 1.0]


def test_no_candles_make_no_line() -> None:
    result = made([])

    assert result.start.size == 0
    assert rows(np.array([], dtype=np.int64), result).is_empty()


def test_a_row_is_the_current_segment_at_each_candle() -> None:
    result = made([10.0, 10.5, 12.0, 13.0, 12.0, 11.0, 13.5])
    ts = np.arange(7, dtype=np.int64) * 1800

    made_rows = rows(ts, result)

    # One row per candle from the first segment on; the rows of a forming segment share its
    # row_id, and the next segment's first row tells that it finished.
    assert made_rows.columns == ["ts", "row_id", "start", "direction", "from", "to", "yang"]
    assert made_rows["ts"].to_list() == [3600, 5400, 7200, 9000, 10800]
    assert made_rows["row_id"].to_list() == [0, 0, 1, 1, 2]
    assert made_rows["start"].to_list() == [3600, 3600, 7200, 7200, 10800]
    assert made_rows["direction"].to_list() == [1.0, 1.0, -1.0, -1.0, 1.0]
    assert made_rows["from"].to_list() == [10.0, 10.0, 13.0, 13.0, 11.0]
    assert made_rows["to"].to_list() == [12.0, 13.0, 12.0, 11.0, 13.5]
    assert made_rows["yang"].to_list() == [1.0, 1.0, 1.0, 1.0, 1.0]


def test_the_features_are_the_direction_length_and_thickness_of_the_segment() -> None:
    made_rows = rows(np.arange(6, dtype=np.int64), made([10.0, 8.0, 9.0, 7.5, 10.0, 12.0]))

    def feature(block: str) -> list[float]:
        return CATALOG[block].run(made_rows, CATALOG[block].params()).to_list()

    assert feature("kagi-direction@1") == [-1.0, 1.0, -1.0, 1.0, 1.0]
    # The extreme over the start, minus 1: 8 / 10 - 1, 9 / 8 - 1, ...
    assert feature("kagi-length@1") == pytest.approx([-0.2, 0.125, 7.5 / 9 - 1, 10 / 7.5 - 1, 0.6])
    assert feature("kagi-yang@1") == [0.0, 0.0, 0.0, 1.0, 1.0]


def frame(count: int) -> pl.DataFrame:
    """Candles that rise by 1 every candle with a true range of 2: an ATR of 2."""
    return pl.DataFrame(
        {
            "ts": [row * 1800 for row in range(count)],
            "open": [100.0 + row for row in range(count)],
            "high": [101.0 + row for row in range(count)],
            "low": [99.0 + row for row in range(count)],
            "close": [100.0 + row for row in range(count)],
            "volume": [1.0] * count,
        }
    )


def test_the_block_in_atrs_starts_the_line_where_the_atr_leaves_its_warm_up() -> None:
    block = CATALOG["kagi@1"]
    params = Kagi(atr_window=2)
    first = ind.atr_warm_up(ind.Atr(window=2))
    candles = frame(first + 6)

    made_rows = block.run(candles, params)

    # An amount of 1 x 2 from candle `first`, at its close: the close rising by 1 a candle
    # starts the segment up two candles later, which then goes on rising.
    expected = kagi_rows(candles.slice(first), np.full(6, 2.0))
    assert made_rows.equals(expected)
    assert made_rows["start"].to_list() == [(first + 2) * 1800] * 4
    assert made_rows["from"][0] == 100.0 + first


def test_the_block_in_percent_starts_the_line_on_the_first_candle() -> None:
    candles = frame(10)

    made_rows = CATALOG["kagi@1"].run(candles, Kagi(reversal="percent", amount=1.0))

    # 1 % of the first close, 100, is 1: the next candle starts the segment up.
    assert made_rows.equals(kagi_rows(candles, np.full(10, 1.0), percent=True))
    assert made_rows["start"][0] == 1800
    assert made_rows["to"].to_list() == [101.0 + row for row in range(9)]


def test_the_block_on_fewer_candles_than_its_warm_up_gives_no_row() -> None:
    made_rows = CATALOG["kagi@1"].run(frame(10), Kagi())

    assert made_rows.is_empty()
    assert made_rows.schema == {
        "ts": pl.Int64,
        "row_id": pl.Int64,
        "start": pl.Int64,
        "direction": pl.Float64,
        "from": pl.Float64,
        "to": pl.Float64,
        "yang": pl.Float64,
    }


def test_the_same_candles_always_give_the_same_rows() -> None:
    block = CATALOG["kagi@1"]
    candles = frame(300)

    assert block.run(candles, Kagi()).equals(block.run(candles.clone(), Kagi()))


@pytest.mark.parametrize(
    ("params", "per_row", "warm_up"),
    [
        # ATR(14) leaves its warm-up at candle 209; a segment of 1 ATR takes about 13 candles.
        ({}, 13.0, 209 + 13),
        ({"amount": 3.0, "atr_window": 10}, 61.0, 149 + 61),
        ({"amount": 0.5}, 7.25, 209 + 8),
        # A percent counts as two ATRs, and the line starts on the first candle.
        ({"reversal": "percent"}, 32.0, 32),
        ({"reversal": "percent", "amount": 0.25}, 7.25, 8),
    ],
)
def test_the_warm_up_is_the_atr_s_then_the_first_segment(
    params: dict[str, object], per_row: float, warm_up: int
) -> None:
    block = CATALOG["kagi@1"]
    kagi = Kagi.model_validate(params)
    names = {"ceil": math.ceil, **kagi.model_dump()}

    assert candles_per_row(kagi) == pytest.approx(per_row)
    assert block.warm_up(kagi) == warm_up
    # The rule the app shows counts the same in both modes.
    assert eval(block.warm_up_rule, {"__builtins__": {}}, names) == warm_up


@pytest.mark.parametrize(
    "params",
    [{"amount": 0}, {"amount": 10.5}, {"atr_window": 1}, {"reversal": "points"}, {"size": 1}],
)
def test_parameters_out_of_range_are_refused(params: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        Kagi.model_validate(params)
