"""Every block by hand on a few candles."""

import math
from typing import Literal

import numpy as np
import polars as pl
import pytest

from candlestack.preprocessing import CATALOG
from candlestack.preprocessing.blocks import BLESK_RULES, ROW_ID, NoParams, Samples
from candlestack.preprocessing.manifest import RESERVED_NAMES, Window

NONE = NoParams()
# Three 30-minute candles, labelled by their open time; volume as the data module gives it.
CANDLES = pl.DataFrame(
    {
        "ts": [0, 1800, 3600],
        "open": [10.0, 11.0, 12.0],
        "high": [11.5, 12.5, 13.5],
        "low": [9.5, 10.5, 11.5],
        "close": [11.0, 12.0, 13.0],
        "volume": [100.0, 200.0, 300.0],
    }
)


def rows(*features: list[float | None], ts: list[int] | None = None) -> pl.DataFrame:
    """Rows of the window: ``ts`` and the features ``f0``, ``f1``, ..."""
    count = len(features[0])
    return pl.DataFrame(
        {
            "ts": ts if ts is not None else [index * 1800 for index in range(count)],
            **{f"f{index}": values for index, values in enumerate(features)},
        },
        schema_overrides={f"f{index}": pl.Float64 for index in range(len(features))},
    )


def window(
    frame: pl.DataFrame, length: int, last: Literal["closed", "current"] = "closed"
) -> Samples:
    return CATALOG["window@1"].run(frame, Window(length=length, last=last))


def test_candles_makes_every_candle_a_row_known_at_its_own_close() -> None:
    result = CATALOG["candles@1"].run(CANDLES.with_columns(trades=pl.lit(5)), NONE)

    # The row of a candle keeps its ts (its open time): it is known at that candle's close.
    assert result.equals(CANDLES)


@pytest.mark.parametrize("name", ["open", "high", "low", "close", "volume"])
def test_a_candle_column_is_that_column_of_each_candle(name: str) -> None:
    result = CATALOG[f"{name}@1"].run(CANDLES, NONE)

    assert result.dtype == pl.Float64
    assert result.to_list() == CANDLES[name].to_list()


def test_a_candle_column_is_float64_from_integers() -> None:
    result = CATALOG["volume@1"].run(CANDLES.with_columns(pl.col("volume").cast(pl.Int64)), NONE)

    assert result.dtype == pl.Float64
    assert result.to_list() == [100.0, 200.0, 300.0]


@pytest.mark.parametrize(
    ("block", "expected"),
    [
        ("rel-open@1", [10 / 11 - 1, 11 / 12 - 1, 12 / 13 - 1]),
        ("rel-high@1", [11.5 / 11 - 1, 12.5 / 12 - 1, 13.5 / 13 - 1]),
        ("rel-low@1", [9.5 / 11 - 1, 10.5 / 12 - 1, 11.5 / 13 - 1]),
        ("rel-close@1", [None, 12 / 11 - 1, 13 / 12 - 1]),
    ],
)
def test_a_relative_price_is_over_the_close(block: str, expected: list[float | None]) -> None:
    assert CATALOG[block].run(CANDLES, NONE).to_list() == expected


def test_a_relative_price_is_float64_from_integers() -> None:
    whole = CANDLES.with_columns(pl.col("open", "close").cast(pl.Int64))

    assert CATALOG["rel-open@1"].run(whole, NONE).dtype == pl.Float64
    assert CATALOG["rel-close@1"].run(whole, NONE).to_list() == [None, 12 / 11 - 1, 13 / 12 - 1]


def test_the_close_s_change_needs_the_previous_candle() -> None:
    assert CATALOG["rel-close@1"].warm_up(NONE) == 1


def test_the_last_candle_of_a_session_is_one_and_the_others_zero() -> None:
    marked = CANDLES.with_columns(session_last=pl.Series([False, True, False]))

    result = CATALOG["session-last-candle@1"].run(marked, NONE)

    assert result.dtype == pl.Float64
    assert result.to_list() == [0.0, 1.0, 0.0]


def test_the_last_candle_of_a_session_needs_the_marks() -> None:
    with pytest.raises(ValueError, match="do not mark the last candle of each session"):
        CATALOG["session-last-candle@1"].run(CANDLES, NONE)


def test_none_gives_the_column_as_it_is() -> None:
    column = pl.Series("close", [1.5, None, -2.0])
    close = pl.Series([10.0, 20.0, 40.0])

    assert CATALOG["none@1"].run(column, close, "close@1", NONE).equals(column)


def blesk_norm(feature: str, column: list[float | None]) -> list[float | None]:
    """``blesk-norm@1`` on ``column`` of ``feature``, the closes of the rows' candles 10, 20, 40."""
    close = pl.Series([10.0, 20.0, 40.0])
    return CATALOG["blesk-norm@1"].run(pl.Series(column), close, feature, NONE).to_list()


@pytest.mark.parametrize(
    "feature",
    [
        "bollinger-high@1",
        "bollinger-low@1",
        "keltner-high@1",
        "keltner-low@1",
        "sma@1",
        "ema@1",
        "ema@2",
    ],
)
def test_blesk_norm_takes_a_price_level_in_percent_from_the_close(feature: str) -> None:
    assert blesk_norm(feature, [11.0, 19.0, None]) == pytest.approx([10.0, -5.0, None])


@pytest.mark.parametrize(
    ("feature", "expected"),
    [
        ("rsi@1", [20.0, -50.0, 50.0]),
        ("rsi@2", [20.0, -50.0, 50.0]),
        ("aroon-up@1", [20.0, -50.0, 50.0]),
        ("aroon-down@1", [20.0, -50.0, 50.0]),
        ("adx@1", [45.0, -25.0, 75.0]),
        ("adx@2", [45.0, -25.0, 75.0]),
    ],
)
def test_blesk_norm_centers_an_oscillator(feature: str, expected: list[float]) -> None:
    assert blesk_norm(feature, [70.0, 0.0, 100.0]) == expected


@pytest.mark.parametrize("feature", ["atr@1", "atr@2"])
def test_blesk_norm_takes_the_atr_in_percent_of_the_close(feature: str) -> None:
    assert blesk_norm(feature, [0.5, 1.0, 1.0]) == pytest.approx([5.0, 5.0, 2.5])


@pytest.mark.parametrize(
    "feature",
    [
        "squeeze@1",
        "roc@1",
        "tsi@1",
        "tsi@2",
        "aroon@1",
        "macd-diff@1",
        "macd-diff@2",
        "renko-direction@1",
        "session-last-candle@1",
        "rel-open@1",
        "rel-high@1",
        "rel-low@1",
        "rel-close@1",
    ],
)
def test_blesk_norm_keeps_a_feature_free_of_the_price_level(feature: str) -> None:
    assert blesk_norm(feature, [0.75, None, -0.25]) == [0.75, None, -0.25]


def test_blesk_norm_has_a_rule_for_each_feature_it_names() -> None:
    assert CATALOG["blesk-norm@1"].normalizes == frozenset(BLESK_RULES)
    assert all(CATALOG[feature].step == "feature" for feature in BLESK_RULES)


@pytest.mark.parametrize("feature", ["open@1", "close@1", "volume@1", "macd-signal@1"])
def test_blesk_norm_refuses_a_feature_without_a_rule(feature: str) -> None:
    with pytest.raises(ValueError, match=f"blesk-norm@1 has no rule for {feature}"):
        blesk_norm(feature, [1.0, 2.0, 3.0])


def test_the_window_gives_the_last_rows_up_to_each_row() -> None:
    frame = rows([1.0, 2.0, 3.0, 4.0], [10.0, 20.0, 30.0, 40.0])

    made = window(frame, 3)

    assert made.samples.dtype == np.float32
    assert made.samples.shape == (2, 3, 2)
    # Rows in time order, features in their order.
    assert made.samples.tolist() == [
        [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0]],
        [[2.0, 20.0], [3.0, 30.0], [4.0, 40.0]],
    ]
    # Each sample is known at the close of its last row's candle.
    assert made.ts.to_list() == [3600, 5400]
    assert made.dropped == 2


def test_a_window_of_one_row_is_every_row() -> None:
    made = window(rows([1.0, 2.0]), 1)

    assert made.samples.tolist() == [[[1.0]], [[2.0]]]
    assert made.ts.to_list() == [0, 1800]
    assert made.dropped == 0


def test_the_window_drops_the_incomplete_rows_at_the_start() -> None:
    # A feature still in its warm-up: null, NaN or infinite, never a sample.
    frame = rows([None, math.nan, 1.0, 2.0, 3.0], [math.inf, 0.0, 0.5, 0.25, 0.125])

    made = window(frame, 2)

    assert made.samples.tolist() == [[[1.0, 0.5], [2.0, 0.25]], [[2.0, 0.25], [3.0, 0.125]]]
    assert made.ts.to_list() == [5400, 7200]
    assert made.dropped == 3


def test_the_window_rounds_to_float32() -> None:
    made = window(rows([0.1]), 1)

    assert made.samples[0, 0, 0] == np.float32(0.1)


@pytest.mark.parametrize(
    "frame",
    [rows([1.0, 2.0]), rows([None, None, None]), rows([]), rows([None, 1.0, 2.0])],
    ids=["too-few", "all-incomplete", "empty", "too-few-after-warm-up"],
)
def test_rows_without_a_full_window_give_no_sample(frame: pl.DataFrame) -> None:
    made = window(frame, 3)

    assert made.samples.shape == (0, 3, 1)
    assert made.samples.dtype == np.float32
    assert made.ts.is_empty()
    assert made.dropped == frame.height


def test_a_hole_after_the_warm_up_is_an_error() -> None:
    frame = rows([1.0, 2.0, 3.0, 4.0], [1.0, 2.0, None, 4.0])

    with pytest.raises(
        ValueError, match="A feature is missing or not finite after the warm-up, at ts 3600"
    ):
        window(frame, 2)


def forming(row_id: list[int], *features: list[float | None]) -> pl.DataFrame:
    """Rows that span several candles: ``ts``, ``row_id`` and the features ``f0``, ..."""
    frame = rows(*features) if features else rows([float(row) for row in range(len(row_id))])
    return frame.insert_column(1, pl.Series(ROW_ID, row_id, dtype=pl.Int64))


def test_the_current_window_is_the_last_finished_rows_and_the_row_still_forming() -> None:
    # Rows 0 and 1 are one row at two candles, so row 0 is not finished; 1, 2 and 4 are.
    frame = forming([0, 0, 1, 2, 2, 3])

    made = window(frame, 3, "current")

    # Row 3 is the first with two finished rows before it (1 and 2); row 4 is still the row of
    # 3, so its sample replaces 3 by 4 on the same finished rows; row 5 has 4 finished too.
    assert made.samples[:, :, 0].tolist() == [[1.0, 2.0, 3.0], [1.0, 2.0, 4.0], [2.0, 4.0, 5.0]]
    assert made.ts.to_list() == [5400, 7200, 9000]
    assert made.dropped == 3
    assert made.samples.dtype == np.float32


def test_a_current_window_of_one_row_is_every_row_as_it_stands() -> None:
    made = window(forming([0, 0, 1], [1.0, 2.0, 3.0]), 1, "current")

    assert made.samples.tolist() == [[[1.0]], [[2.0]], [[3.0]]]
    assert made.dropped == 0


def test_the_current_window_counts_finished_rows_from_the_first_complete_one() -> None:
    frame = forming([0, 1, 2, 3, 3], [None, 1.0, 2.0, 3.0, 4.0])

    made = window(frame, 2, "current")

    # Row 0 is finished but incomplete; row 1 is the first finished row that counts.
    assert made.samples[:, :, 0].tolist() == [[1.0, 2.0], [2.0, 3.0], [2.0, 4.0]]
    assert made.dropped == 2


def test_the_current_window_needs_as_many_finished_rows() -> None:
    made = window(forming([0, 0, 0, 1]), 3, "current")

    assert made.samples.shape == (0, 3, 1)
    assert made.dropped == 4


def test_the_current_window_never_reads_whether_its_own_row_is_finished() -> None:
    """Whether a row is finished shows only on the next candle: a sample ending at a row is the
    same whether the next row goes on with it or starts another."""
    going_on = window(forming([0, 1, 2, 2]), 2, "current")
    finished = window(forming([0, 1, 2, 3]), 2, "current")

    assert going_on.samples[:2].tolist() == finished.samples[:2].tolist()


def by_hand(frame: pl.DataFrame, length: int) -> list[list[list[float]]]:
    """The samples of complete rows one by one: the ``length - 1`` finished rows before each
    row, then the row (every row is finished without ``row_id``)."""
    values = frame.drop("ts", ROW_ID, strict=False).to_numpy().astype(np.float32).tolist()
    ids = frame[ROW_ID].to_list() if ROW_ID in frame.columns else list(range(frame.height))
    finished = [row for row in range(frame.height - 1) if ids[row] != ids[row + 1]]
    samples = []
    for row in range(frame.height):
        earlier = [done for done in finished if done < row][-(length - 1) :] if length > 1 else []
        if len(earlier) == length - 1:
            samples.append([values[index] for index in [*earlier, row]])
    return samples


@pytest.mark.parametrize("last", ["closed", "current"])
def test_the_samples_built_a_part_at_a_time_are_the_samples_one_by_one(
    last: Literal["closed", "current"],
) -> None:
    rng = np.random.default_rng(3)
    frame = rows(rng.normal(size=300).tolist(), rng.normal(size=300).tolist())
    if last == "current":
        row_id = np.cumsum(rng.random(300) < 0.4)
        frame = frame.insert_column(1, pl.Series(ROW_ID, row_id, dtype=pl.Int64))

    made = window(frame, 7, last)

    expected = by_hand(frame, 7)
    assert len(made) == len(expected) == len(made.ts)
    assert made.samples.tolist() == expected
    for size in (1, 5, 64, len(made) + 1):
        parts = [made.part(start, start + size) for start in range(0, len(made), size)]
        assert all(part.dtype == np.float32 and part.flags.c_contiguous for part in parts)
        assert np.concatenate(parts).tolist() == expected
    # One sample of 7 rows of two float32 features.
    assert made.sample_bytes == 7 * 2 * 4


def test_the_window_needs_the_rule_of_its_rows() -> None:
    with pytest.raises(ValueError, match="last: current needs rows that span several candles"):
        window(rows([1.0]), 1, "current")
    with pytest.raises(ValueError, match="last: closed needs rows finished at their own candle"):
        window(forming([0]), 1, "closed")


@pytest.mark.parametrize(("length", "rows_before"), [(1, 0), (25, 24)])
def test_the_window_needs_length_minus_one_earlier_rows(length: int, rows_before: int) -> None:
    assert CATALOG["window@1"].warm_up(Window(length=length)) == rows_before


@pytest.mark.parametrize(
    "block",
    [
        "candles@1",
        "open@1",
        "high@1",
        "low@1",
        "close@1",
        "volume@1",
        "rel-open@1",
        "rel-high@1",
        "rel-low@1",
        "session-last-candle@1",
        "none@1",
        "blesk-norm@1",
    ],
)
def test_the_candle_blocks_need_no_earlier_candle(block: str) -> None:
    assert CATALOG[block].warm_up(NONE) == 0


def test_no_feature_may_take_the_name_of_the_row_id() -> None:
    assert ROW_ID in RESERVED_NAMES
