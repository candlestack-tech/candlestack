"""The Renko bricks and their triples by hand, rule by rule (research #263): a few closes with a
brick height of 1, so every edge is a whole number."""

import math

import numpy as np
import polars as pl
import pytest

from candlestack.preprocessing import CATALOG
from candlestack.preprocessing import indicators as ind
from candlestack.preprocessing.renko import (
    Brick,
    Bricks,
    RenkoTriples,
    bricks,
    candles_per_row,
    renko_rows,
    triples,
)

NAN = math.nan


def made(closes: list[float], *, first: tuple[float, float] = (10.5, 9.5), size: float = 1.0):
    """The bricks of candles with these closes and one brick height; the first candle's high
    and low are ``first``, the later ones' do not count."""
    count = len(closes)
    high = np.array([first[0], *closes[1:]])
    low = np.array([first[1], *closes[1:]])
    return bricks(np.array(closes), high, low, np.full(count, size))


def edges(result: Bricks) -> list[tuple[float, float]]:
    return list(zip(result.bottom.tolist(), result.top.tolist(), strict=True))


def test_the_first_brick_lies_on_the_first_candle_s_mid_price() -> None:
    # The mid price, not the close: (10.5 + 9.5) / 2 = 10.
    result = made([10.4], first=(10.5, 9.5))

    assert edges(result) == [(10.0, 11.0)]
    assert result.start.tolist() == [0]
    assert math.isnan(result.direction[0])
    assert result.skipped.tolist() == [0]


def test_a_close_inside_the_brick_or_on_its_edge_makes_no_brick() -> None:
    # The high and low do not count, and an edge belongs to the brick.
    result = made([10.0, 10.5, 11.0, 10.0])

    assert edges(result) == [(10.0, 11.0)] * 4
    assert result.start.tolist() == [0, 0, 0, 0]
    assert result.finished.tolist() == [0, 0, 0, 0]


def test_a_close_above_the_top_makes_the_next_brick_up() -> None:
    result = made([10.0, 11.5, 12.5])

    assert edges(result) == [(10.0, 11.0), (11.0, 12.0), (12.0, 13.0)]
    assert result.direction.tolist()[1:] == [1.0, 1.0]
    assert result.skipped.tolist() == [0, 0, 0]
    assert result.start.tolist() == [0, 1, 2]


def test_a_reversal_needs_only_a_close_below_the_bottom() -> None:
    # No two bricks to reverse: one close under the bottom of a rising brick is enough.
    result = made([10.0, 11.5, 10.5])

    assert edges(result) == [(10.0, 11.0), (11.0, 12.0), (10.0, 11.0)]
    assert result.direction.tolist()[1:] == [1.0, -1.0]


def test_a_move_of_several_bricks_is_one_brick_that_skips_the_cells_between() -> None:
    # 13.7 is 2.7 above the top 11: two whole cells skipped, the brick is the third.
    up = made([10.0, 13.7])
    # 6.2 is 3.8 below the bottom 10: three cells skipped.
    down = made([10.0, 6.2])

    assert edges(up)[1] == (13.0, 14.0)
    assert up.skipped.tolist() == [0, 2]
    assert edges(down)[1] == (6.0, 7.0)
    assert (down.direction[1], down.skipped[1]) == (-1.0, -3)


def test_a_gap_is_a_move_like_any_other() -> None:
    """Candles carry no time here: the first candle of a day after an overnight gap is
    compared with the brick like the next candle of the same day."""
    result = made([10.0, 10.5, 15.5, 15.2])

    assert edges(result)[2:] == [(15.0, 16.0), (15.0, 16.0)]
    assert result.skipped.tolist() == [0, 0, 4, 4]


def test_a_close_on_a_grid_line_above_the_top_starts_the_cell_above_it() -> None:
    # 13 is two heights above the top 11: the cell [13, 14], two cells skipped.
    result = made([10.0, 13.0])

    assert edges(result)[1] == (13.0, 14.0)
    assert result.skipped[1] == 2


def test_the_brick_keeps_the_height_of_the_candle_that_started_it() -> None:
    sizes = np.array([1.0, 2.0, 5.0, 5.0, 5.0])
    closes = np.array([10.0, 11.5, 12.5, 13.9, 18.5])

    result = bricks(closes, np.array([10.5, *closes[1:]]), np.array([9.5, *closes[1:]]), sizes)

    # The brick of candle 1 is 2 high and stays so under the height of 5 of candle 2; the
    # close 13.9 of candle 3 is 0.9 above it, under one height of 5: the brick [13, 18].
    assert edges(result) == [(10.0, 11.0), (11.0, 13.0), (11.0, 13.0), (13.0, 18.0), (18.0, 23.0)]
    assert result.skipped.tolist() == [0, 0, 0, 0, 0]


def test_the_rows_of_a_brick_repeat_its_direction_and_skipped_cells() -> None:
    result = made([10.0, 13.7, 13.2])

    assert result.skipped.tolist() == [0, 2, 2]
    assert result.direction.tolist()[1:] == [1.0, 1.0]


def test_finished_is_the_last_candle_of_a_brick_and_never_the_last_candle() -> None:
    result = made([10.0, 10.5, 11.5, 11.7, 12.5])

    assert result.start.tolist() == [0, 0, 2, 2, 4]
    assert result.finished.tolist() == [0, 1, 0, 1, 0]
    assert result.number.tolist() == [0, 0, 1, 1, 2]


def test_a_height_of_zero_starts_no_brick() -> None:
    """Candles that never moved have an ATR of 0: no brick of no height."""
    result = bricks(
        np.array([10.0, 10.0, 12.5]),
        np.array([10.0, 0.0, 0.0]),
        np.array([10.0, 0.0, 0.0]),
        np.array([0.0, 0.0, 1.0]),
    )

    assert edges(result) == [(10.0, 10.0), (10.0, 10.0), (12.0, 13.0)]
    assert result.skipped.tolist() == [0, 0, 2]


def test_the_bricks_can_go_on_from_a_brick_started_before() -> None:
    result = bricks(
        np.array([12.5, 13.5]),
        np.zeros(2),
        np.zeros(2),
        np.ones(2),
        after=Brick(bottom=11.0, top=12.0, direction=1.0, skipped=3),
    )

    assert result.start.tolist() == [0, 1]
    assert edges(result) == [(12.0, 13.0), (13.0, 14.0)]
    assert result.skipped.tolist() == [0, 0]
    inside = bricks(
        np.array([11.5]), np.zeros(1), np.zeros(1), np.ones(1), after=Brick(11, 12, -1, -2)
    )
    assert (inside.start[0], inside.direction[0], inside.skipped[0]) == (-1, -1.0, -2)


def test_no_candles_make_no_brick() -> None:
    result = made([])

    assert result.start.size == 0
    assert triples(np.array([], dtype=np.int64), result).is_empty()


def test_a_triple_is_the_two_bricks_before_and_the_current_one() -> None:
    # Bricks: [10, 11] from 0, up [11, 12] from 1, up [12, 13] from 2 (two candles), down
    # [10, 11] from 4.
    result = made([10.0, 11.5, 12.5, 12.2, 10.6])
    ts = np.arange(5, dtype=np.int64) * 1800

    rows = triples(ts, result)

    # One row per candle from the third brick on, each at its own candle.
    assert rows.columns == ["ts", "row_id", "start", "direction", "bottom", "top"]
    assert rows["ts"].to_list() == [3600, 5400, 7200]
    assert rows["row_id"].to_list() == [2, 2, 3]
    # The first of the three bricks started at candle 0, then at candle 1.
    assert rows["start"].to_list() == [0, 0, 1800]
    # 0.5 x up + 0.25 x up, then 0.5 x up + 0.25 x down.
    assert rows["direction"].to_list() == [0.75, 0.75, 0.25]
    assert rows["bottom"].to_list() == [10.0, 10.0, 10.0]
    assert rows["top"].to_list() == [13.0, 13.0, 13.0]


@pytest.mark.parametrize(
    ("middle", "current", "direction"),
    [(-1, -1, -0.75), (-1, 1, -0.25), (1, -1, 0.25), (1, 1, 0.75)],
)
def test_the_direction_of_a_triple(middle: int, current: int, direction: float) -> None:
    closes = [10.0, 10.0 + 1.5 * middle]
    closes.append(closes[-1] + 2.0 * current if current != middle else closes[-1] + middle)

    rows = triples(np.arange(3, dtype=np.int64), made(closes))

    assert rows["direction"].to_list() == [direction]


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


def test_the_block_starts_the_bricks_where_the_atr_leaves_its_warm_up() -> None:
    block = CATALOG["renko-triples@1"]
    params = RenkoTriples(atr_window=2)
    first = ind.atr_warm_up(ind.Atr(window=2))
    candles = frame(first + 6)

    rows = block.run(candles, params)

    # Bricks of 0.6 x 2 = 1.2 from candle `first`, on its mid price, its close: the close
    # rising by 1 a candle leaves the first brick two candles later, then one brick a candle.
    expected = renko_rows(candles.slice(first), np.full(6, 1.2))
    assert rows.equals(expected)
    assert rows["start"][0] == first * 1800
    assert rows["ts"][0] == (first + 3) * 1800


def test_the_block_on_fewer_candles_than_its_warm_up_gives_no_row() -> None:
    block = CATALOG["renko-triples@1"]

    rows = block.run(frame(10), RenkoTriples())

    assert rows.is_empty()
    assert rows.schema == {
        "ts": pl.Int64,
        "row_id": pl.Int64,
        "start": pl.Int64,
        "direction": pl.Float64,
        "bottom": pl.Float64,
        "top": pl.Float64,
    }


def test_the_same_candles_always_give_the_same_rows() -> None:
    block = CATALOG["renko-triples@1"]
    candles = frame(300)

    assert block.run(candles, RenkoTriples()).equals(block.run(candles.clone(), RenkoTriples()))


def test_the_direction_feature_is_the_triple_s_direction() -> None:
    rows = triples(np.arange(5, dtype=np.int64), made([10.0, 11.5, 12.5, 12.2, 10.6]))

    direction = CATALOG["renko-direction@1"].run(rows, CATALOG["renko-direction@1"].params())

    assert direction.to_list() == [0.75, 0.75, 0.25]


def test_the_warm_up_is_the_atr_s_then_two_bricks_and_a_brick_takes_about_two_candles() -> None:
    block = CATALOG["renko-triples@1"]
    params = RenkoTriples()

    # ATR(14) leaves its warm-up at candle 209; a brick of 0.6 ATR takes about 2.2 candles.
    assert candles_per_row(params) == pytest.approx(2.2)
    assert block.warm_up(params) == 209 + 5
    assert block.warm_up_rule == "15 * atr_window - 1 + ceil(2 * (1 + 2 * atr_multiple))"
    assert candles_per_row(RenkoTriples(atr_multiple=3.0)) == pytest.approx(7.0)


@pytest.mark.parametrize(
    "params",
    [{"atr_multiple": 0}, {"atr_multiple": 11.0}, {"atr_window": 1}, {"atr_at": "brick_end"}],
)
def test_parameters_out_of_range_are_refused(params: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="validation error"):
        RenkoTriples.model_validate(params)
