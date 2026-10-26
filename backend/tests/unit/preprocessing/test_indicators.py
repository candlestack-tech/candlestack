"""The indicator blocks by hand: on a few candles for the ones over a window, against plain loops
of the ``ta`` library's formulas for the recursive ones and of TA-Lib's for their @2 versions,
and their warm-ups."""

import itertools
import math
from typing import Any

import numpy as np
import polars as pl
import pytest

from candlestack.preprocessing import CATALOG

# Five 30-minute candles.
CANDLES = pl.DataFrame(
    {
        "ts": [0, 1800, 3600, 5400, 7200],
        "open": [10.0, 10.0, 12.0, 11.0, 11.0],
        "high": [11.0, 13.0, 12.0, 12.0, 15.0],
        "low": [9.0, 10.0, 10.0, 10.0, 11.0],
        "close": [10.0, 12.0, 11.0, 11.0, 14.0],
        "volume": [100.0, 200.0, 300.0, 400.0, 500.0],
    }
)
INDICATORS = [
    block
    for block in CATALOG.values()
    if block.group not in ("Candle", None) and block.of_rows is None
]
# The recursive indicators started as TA-Lib does, with the @1 version each stands for.
TALIB_START = {
    "rsi@2": "rsi@1",
    "tsi@2": "tsi@1",
    "atr@2": "atr@1",
    "adx@2": "adx@1",
    "ema@2": "ema@1",
    "macd-diff@2": "macd-diff@1",
}
# The indicators whose values after the warm-up do not depend on where the candles start.
FROM_ANY_START = [block for block in INDICATORS if block.id not in TALIB_START]


def run(block: str, frame: pl.DataFrame = CANDLES, **params: Any) -> list[float | None]:
    return CATALOG[block].run(frame, CATALOG[block].params.model_validate(params)).to_list()


def wave(rows: int = 80) -> pl.DataFrame:
    """Candles that rise and fall, with ranges and gaps that change from candle to candle."""
    close = [100 + 5 * math.sin(row / 3) + 0.1 * row for row in range(rows)]
    return pl.DataFrame(
        {
            "ts": [row * 1800 for row in range(rows)],
            "open": [close[0], *close[:-1]],
            "high": [value + 1 + (row % 3) * 0.5 for row, value in enumerate(close)],
            "low": [value - 1 - (row % 2) * 0.5 for row, value in enumerate(close)],
            "close": close,
            "volume": [1.0] * rows,
        }
    )


def random_walk(rows: int, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, rows)))
    open_ = close * (1 + rng.normal(0, 0.003, rows))
    return pl.DataFrame(
        {
            "ts": np.arange(rows) * 1800,
            "open": open_,
            "high": np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.003, rows))),
            "low": np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.003, rows))),
            "close": close,
            "volume": np.ones(rows),
        }
    )


def approx(values: list[float | None]) -> list[Any]:
    return [None if value is None else pytest.approx(value, rel=1e-12) for value in values]


def test_the_mean_close() -> None:
    assert run("sma@1", window=2) == approx([None, 11.0, 11.5, 11.0, 12.5])


def test_the_bollinger_bands_take_the_standard_deviation_of_the_population() -> None:
    # Of two closes it is half their difference.
    assert run("bollinger-high@1", window=2, dev=2) == approx([None, 13.0, 12.5, 11.0, 15.5])
    assert run("bollinger-low@1", window=2, dev=2) == approx([None, 9.0, 10.5, 11.0, 9.5])


def test_the_keltner_channel_is_the_original_version() -> None:
    # The means of (4 high - 2 low + close) / 3: 12, 44/3, 13, 13, 52/3, and of
    # (-2 high + 4 low + close) / 3: 8, 26/3, 9, 9, 28/3.
    assert run("keltner-high@1", window=2) == approx([None, 40 / 3, 83 / 6, 13.0, 91 / 6])
    assert run("keltner-low@1", window=2) == approx([None, 25 / 3, 53 / 6, 9.0, 55 / 6])


def test_the_rate_of_change_is_in_percent() -> None:
    assert run("roc@1", window=2) == approx([None, None, 10.0, -100 / 12, 300 / 11])


def test_aroon_is_on_the_close_and_takes_the_first_of_equal_closes() -> None:
    # The windows of three closes: 10, 12, 11; 12, 11, 11 (two lows); 11, 11, 14.
    assert run("aroon-up@1", window=3) == approx([None, None, 200 / 3, 100 / 3, 100.0])
    assert run("aroon-down@1", window=3) == approx([None, None, 100 / 3, 200 / 3, 100 / 3])
    assert run("aroon@1", window=3) == approx([None, None, 100 / 3, -100 / 3, 200 / 3])


def test_aroon_of_fewer_candles_than_its_window_is_warm_up() -> None:
    assert run("aroon@1", CANDLES.head(2), window=3) == [None, None]


def test_the_squeeze_is_1_while_the_bollinger_bands_are_inside_the_keltner_channel() -> None:
    # True ranges 3, 2, 2, 4 from the second candle: means 2.5, 2, 3 from the third. The
    # channel 11.5 +- 1.25, 11 +- 1, 12.5 +- 1.5 holds the bands 10.5..12.5 and 11..11, not
    # 9.5..15.5. Its first value waits for two true ranges of candles with one before them.
    params: dict[str, Any] = {
        "bollinger_window": 2,
        "bollinger_dev": 2,
        "keltner_window": 2,
        "keltner_atr_window": 2,
        "keltner_multiplier": 0.5,
    }

    assert run("squeeze@1", **params) == [None, None, 1.0, 1.0, 0.0]


NAN = math.nan


def ewm(values: list[float], alpha: float) -> list[float]:
    """An EMA as pandas' ``ewm(adjust=False)``: from the first value, past the leading NaNs."""
    out: list[float] = []
    last = NAN
    for value in values:
        if not math.isnan(value):
            last = value if math.isnan(last) else (1 - alpha) * last + alpha * value
        out.append(last)
    return out


def masked(values: list[float], count: int) -> list[float]:
    """``values`` with NaN until ``count`` of them were seen (pandas' ``min_periods``)."""
    seen = 0
    out: list[float] = []
    for value in values:
        seen += not math.isnan(value)
        out.append(value if seen >= count else NAN)
    return out


def ema(values: list[float], window: int) -> list[float]:
    return masked(ewm(values, 2 / (window + 1)), window)


def changes(frame: pl.DataFrame, first: float) -> list[float]:
    close = frame["close"].to_list()
    return [first] + [b - a for a, b in itertools.pairwise(close)]


def wilder(values: list[float], window: int, seed_at: int) -> list[float]:
    """``ta``'s Wilder smoothing: the mean of the ``window`` values up to ``seed_at``, then
    ``(last * (window - 1) + value) / window``."""
    out = [NAN] * seed_at + [sum(values[seed_at - window + 1 : seed_at + 1]) / window]
    for value in values[seed_at + 1 :]:
        out.append((out[-1] * (window - 1) + value) / window)
    return out


def rsi_by_hand(frame: pl.DataFrame, window: int) -> list[float]:
    moves = changes(frame, 0.0)
    up = ewm([max(move, 0.0) for move in moves], 1 / window)
    down = ewm([max(-move, 0.0) for move in moves], 1 / window)
    return [100.0 if d == 0 else 100 - 100 / (1 + u / d) for u, d in zip(up, down, strict=True)]


def tsi_by_hand(frame: pl.DataFrame, slow: int, fast: int) -> list[float]:
    moves = changes(frame, NAN)
    smoothed = ema(ema(moves, slow), fast)
    absolute = ema(ema([abs(move) for move in moves], slow), fast)
    return [100 * s / a for s, a in zip(smoothed, absolute, strict=True)]


def atr_by_hand(frame: pl.DataFrame, window: int) -> list[float]:
    high, low, close = frame["high"].to_list(), frame["low"].to_list(), frame["close"].to_list()
    ranges = [high[0] - low[0]] + [
        max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        for i in range(1, frame.height)
    ]
    return wilder(ranges, window, window - 1)


def adx_by_hand(frame: pl.DataFrame, window: int) -> list[float]:
    high, low, close = frame["high"].to_list(), frame["low"].to_list(), frame["close"].to_list()
    rows = range(1, frame.height)
    ranges = [NAN] + [max(high[i], close[i - 1]) - min(low[i], close[i - 1]) for i in rows]
    rise = [NAN] + [high[i] - high[i - 1] for i in rows]
    fall = [NAN] + [low[i - 1] - low[i] for i in rows]
    plus = [r if r > f and r > 0 else 0.0 for r, f in zip(rise, fall, strict=True)]
    minus = [f if f > r and f > 0 else 0.0 for r, f in zip(rise, fall, strict=True)]
    smoothed = [wilder(values, window, window) for values in (ranges, plus, minus)]
    index = [
        100 * abs(p - m) / (p + m)
        for p, m in ((100 * p / s, 100 * m / s) for s, p, m in zip(*smoothed, strict=True))
    ]
    return wilder(index, window, 2 * window - 1)


def macd_by_hand(frame: pl.DataFrame, fast: int, slow: int, signal: int) -> list[float]:
    close = frame["close"].to_list()
    line = [f - s for f, s in zip(ema(close, fast), ema(close, slow), strict=True)]
    return [value - sig for value, sig in zip(line, ema(line, signal), strict=True)]


@pytest.mark.parametrize(
    ("block", "params", "by_hand"),
    [
        ("rsi@1", {"window": 2}, lambda frame: rsi_by_hand(frame, 2)),
        ("tsi@1", {"slow": 3, "fast": 2}, lambda frame: tsi_by_hand(frame, 3, 2)),
        ("atr@1", {"window": 2}, lambda frame: atr_by_hand(frame, 2)),
        ("adx@1", {"window": 2}, lambda frame: adx_by_hand(frame, 2)),
        ("ema@1", {"window": 2}, lambda frame: ema(frame["close"].to_list(), 2)),
        (
            "macd-diff@1",
            {"fast": 2, "slow": 3, "signal": 2},
            lambda frame: macd_by_hand(frame, 2, 3, 2),
        ),
    ],
)
def test_a_recursive_block_follows_ta_s_formula_after_its_warm_up(
    block: str, params: dict[str, int], by_hand: Any
) -> None:
    frame = wave()
    count = CATALOG[block].warm_up(CATALOG[block].params.model_validate(params))
    ours = run(block, frame, **params)

    assert count < frame.height
    assert ours[:count] == [None] * count
    assert ours[count:] == [pytest.approx(value, rel=1e-9) for value in by_hand(frame)[count:]]


def steady(rows: int, step: float) -> pl.DataFrame:
    """Candles that move by ``step`` each, with a range of 2 around the close."""
    close = [100.0 + step * row for row in range(rows)]
    return pl.DataFrame(
        {
            "ts": [row * 1800 for row in range(rows)],
            "open": close,
            "high": [value + 1 for value in close],
            "low": [value - 1 for value in close],
            "close": close,
            "volume": [1.0] * rows,
        }
    )


def test_the_rsi_is_100_while_closes_rise_and_0_while_they_fall() -> None:
    assert run("rsi@1", steady(40, 0.5), window=2)[-1] == 100.0
    assert run("rsi@1", steady(40, -0.5), window=2)[-1] == pytest.approx(0.0)


def closes(values: list[float]) -> pl.DataFrame:
    """Candles of these closes, with a range of 2 around each."""
    return pl.DataFrame(
        {
            "ts": [row * 1800 for row in range(len(values))],
            "open": values,
            "high": [value + 1 for value in values],
            "low": [value - 1 for value in values],
            "close": values,
            "volume": [1.0] * len(values),
        }
    )


def test_the_rsi_of_closes_that_never_moved_is_0() -> None:
    # ta says 100 here (no fall); the BLESK data says 0.
    assert run("rsi@1", steady(60, 0.0), window=2)[28:] == [0.0] * 32


def test_the_rsi_holds_while_the_closes_stay_flat_then_falls_to_0() -> None:
    # A rise of 1 and a fall of 0.5 leave a mean rise and a mean fall of 0.25 each (RSI 50),
    # which halve on every flat candle: from the candle 48 on they add up to under 1e-14.
    rsi = run("rsi@1", closes([100.0, 101.0, 100.5] + [100.5] * 80), window=2)

    assert rsi[28:48] == [50.0] * 20
    assert rsi[48:] == [0.0] * 35
    # The next move counts again.
    moved = run("rsi@1", closes([100.0, 101.0, 100.5] + [100.5] * 80 + [101.0]), window=2)
    assert moved[-1] == 100.0


def test_the_tsi_is_100_while_closes_rise() -> None:
    assert run("tsi@1", steady(60, 0.5), slow=3, fast=2)[-1] == pytest.approx(100.0)


def test_the_atr_of_candles_of_one_range_is_that_range() -> None:
    assert run("atr@1", steady(40, 0.5), window=2)[-1] == pytest.approx(2.0)


def test_the_adx_is_100_in_a_steady_rise_and_0_without_any_move() -> None:
    assert run("adx@1", steady(60, 0.5), window=2)[-1] == pytest.approx(100.0)
    # No directional movement at all: ta counts the directional index as 0.
    assert run("adx@1", steady(60, 0.0), window=2)[-1] == 0.0


def test_the_macd_histogram_of_a_steady_rise_goes_to_0() -> None:
    assert run("macd-diff@1", steady(80, 0.5), fast=2, slow=3, signal=2)[-1] == pytest.approx(
        0.0, abs=1e-9
    )


@pytest.mark.parametrize("block", INDICATORS, ids=[block.id for block in INDICATORS])
def test_the_warm_up_is_exactly_the_leading_nulls(block: Any) -> None:
    params = block.params()
    count = block.warm_up(params)
    values = block.run(random_walk(count + 50, 0), params)

    assert values.dtype == pl.Float64
    assert values.head(count).null_count() == count
    assert values.slice(count).null_count() == 0


@pytest.mark.parametrize("block", FROM_ANY_START, ids=[block.id for block in FROM_ANY_START])
def test_after_the_warm_up_where_the_candles_start_changes_nothing(block: Any) -> None:
    """The values after the warm-up are those of a longer history, within the tolerance of the
    golden tests: the first candles weigh less than 1e-6 in them."""
    params = block.params()
    count = block.warm_up(params)
    candles = random_walk(count + 1000, 1)
    later = 300

    full = block.run(candles, params).slice(later + count).to_numpy()
    started_later = block.run(candles.slice(later), params).slice(count).to_numpy()

    np.testing.assert_allclose(started_later, full, rtol=1e-6, atol=1e-8)


# TA-Lib's formulas as plain loops, after its C code (research #265): each smoothing starts from
# the mean of its first window. They give NaN before TA-Lib's first value.


def talib_ema_by_hand(values: list[float], window: int) -> list[float]:
    """TA-Lib's EMA, past the leading NaNs as its Python wrapper takes them: the mean of the
    first ``window`` values, then ``previous + 2 / (window + 1) * (value - previous)``."""
    first = next(row for row, value in enumerate(values) if not math.isnan(value))
    seed_at = first + window - 1
    out = [NAN] * seed_at + [sum(values[first : seed_at + 1]) / window]
    for value in values[seed_at + 1 :]:
        out.append(out[-1] + 2 / (window + 1) * (value - out[-1]))
    return out


def talib_rsi_by_hand(frame: pl.DataFrame, window: int) -> list[float]:
    """TA-Lib's RSI: the mean rise and fall of the first ``window`` changes, then Wilder's
    smoothing; 0 where they add up to under 1e-14, as in the BLESK data."""
    moves = changes(frame, NAN)[1:]
    gain = sum(max(move, 0.0) for move in moves[:window]) / window
    loss = sum(max(-move, 0.0) for move in moves[:window]) / window
    out = [NAN] * window

    def value() -> float:
        return 0.0 if gain + loss < 1e-14 else 100 * gain / (gain + loss)

    out.append(value())
    for move in moves[window:]:
        gain = (gain * (window - 1) + max(move, 0.0)) / window
        loss = (loss * (window - 1) + max(-move, 0.0)) / window
        out.append(value())
    return out


def true_ranges(frame: pl.DataFrame) -> list[float]:
    """The true range of each candle after the first, at its own index; NaN for the first."""
    high, low, close = frame["high"].to_list(), frame["low"].to_list(), frame["close"].to_list()
    return [NAN] + [
        max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        for i in range(1, frame.height)
    ]


def talib_atr_by_hand(frame: pl.DataFrame, window: int) -> list[float]:
    """TA-Lib's ATR: the mean of the true ranges of the candles 1 to ``window``, then
    Wilder's smoothing."""
    ranges = true_ranges(frame)
    out = [NAN] * window + [sum(ranges[1 : window + 1]) / window]
    for value in ranges[window + 1 :]:
        out.append((out[-1] * (window - 1) + value) / window)
    return out


def talib_adx_by_hand(frame: pl.DataFrame, window: int) -> list[float]:
    """TA-Lib's ADX: sums of ``window - 1`` true ranges and directional movements, then
    ``sum - sum / window + value``; the directional index averaged over its first ``window``
    candles, then smoothed by Wilder; an index of a zero sum counts 0 in the average and
    leaves the ADX as it was afterwards."""
    high, low = frame["high"].to_list(), frame["low"].to_list()
    ranges = true_ranges(frame)
    sums = [0.0, 0.0, 0.0]

    def step(i: int) -> tuple[float, float, float]:
        rise, fall = high[i] - high[i - 1], low[i - 1] - low[i]
        plus = rise if rise > 0 and rise > fall else 0.0
        minus = fall if fall > 0 and fall > rise else 0.0
        return ranges[i], plus, minus

    def index() -> float | None:
        if abs(sums[0]) < 1e-14:
            return None
        plus, minus = 100 * sums[1] / sums[0], 100 * sums[2] / sums[0]
        if abs(plus + minus) < 1e-14:
            return None
        return 100 * abs(plus - minus) / (plus + minus)

    for i in range(1, window):
        sums = [total + value for total, value in zip(sums, step(i), strict=True)]
    indices = []
    for i in range(window, 2 * window):
        sums = [total - total / window + value for total, value in zip(sums, step(i), strict=True)]
        indices.append(index() or 0.0)
    out = [NAN] * (2 * window - 1) + [sum(indices) / window]
    for i in range(2 * window, frame.height):
        sums = [total - total / window + value for total, value in zip(sums, step(i), strict=True)]
        value = index()
        out.append(out[-1] if value is None else (out[-1] * (window - 1) + value) / window)
    return out


def talib_macd_by_hand(frame: pl.DataFrame, fast: int, slow: int, signal: int) -> list[float]:
    """TA-Lib's MACD histogram: both EMAs start at the candle ``slow - 1``, the fast one from
    the mean of its last ``fast`` closes up to it; the shorter is the fast one."""
    fast, slow = sorted((fast, slow))
    close = frame["close"].to_list()
    emas = []
    for window in (fast, slow):
        out = [NAN] * (slow - 1) + [sum(close[slow - window : slow]) / window]
        for value in close[slow:]:
            out.append(out[-1] + 2 / (window + 1) * (value - out[-1]))
        emas.append(out)
    line = [f - s for f, s in zip(*emas, strict=True)]
    return [value - sig for value, sig in zip(line, talib_ema_by_hand(line, signal), strict=True)]


def talib_tsi_by_hand(frame: pl.DataFrame, slow: int, fast: int) -> list[float]:
    moves = changes(frame, NAN)
    smoothed = talib_ema_by_hand(talib_ema_by_hand(moves, slow), fast)
    absolute = talib_ema_by_hand(talib_ema_by_hand([abs(move) for move in moves], slow), fast)
    return [100 * s / a for s, a in zip(smoothed, absolute, strict=True)]


TALIB_CASES = [
    ("rsi@2", {"window": 5}, lambda frame: talib_rsi_by_hand(frame, 5)),
    ("rsi@2", {}, lambda frame: talib_rsi_by_hand(frame, 20)),
    ("tsi@2", {"slow": 6, "fast": 4}, lambda frame: talib_tsi_by_hand(frame, 6, 4)),
    ("tsi@2", {}, lambda frame: talib_tsi_by_hand(frame, 25, 13)),
    ("atr@2", {"window": 5}, lambda frame: talib_atr_by_hand(frame, 5)),
    ("atr@2", {}, lambda frame: talib_atr_by_hand(frame, 14)),
    ("adx@2", {"window": 4}, lambda frame: talib_adx_by_hand(frame, 4)),
    ("adx@2", {}, lambda frame: talib_adx_by_hand(frame, 14)),
    ("ema@2", {"window": 5}, lambda frame: talib_ema_by_hand(frame["close"].to_list(), 5)),
    ("ema@2", {"window": 50}, lambda frame: talib_ema_by_hand(frame["close"].to_list(), 50)),
    (
        "macd-diff@2",
        {"fast": 3, "slow": 6, "signal": 4},
        lambda frame: talib_macd_by_hand(frame, 3, 6, 4),
    ),
    (
        "macd-diff@2",
        {"fast": 6, "slow": 3, "signal": 4},
        lambda frame: talib_macd_by_hand(frame, 3, 6, 4),
    ),
    ("macd-diff@2", {}, lambda frame: talib_macd_by_hand(frame, 12, 26, 9)),
]


@pytest.mark.parametrize(
    ("block", "params", "by_hand"),
    TALIB_CASES,
    ids=[f"{block} {params}" for block, params, _ in TALIB_CASES],
)
@pytest.mark.parametrize("candles", ["wave", "random walk"])
def test_a_2_version_follows_talib_s_formula_from_its_first_value(
    block: str, params: dict[str, int], by_hand: Any, candles: str
) -> None:
    frame = wave() if candles == "wave" else random_walk(90, 2)
    count = CATALOG[block].warm_up(CATALOG[block].params.model_validate(params))
    expected = by_hand(frame)
    ours = run(block, frame, **params)

    # The warm-up is TA-Lib's: the candles before its first value.
    assert count == next(row for row, value in enumerate(expected) if not math.isnan(value))
    assert ours[:count] == [None] * count
    assert ours[count:] == [pytest.approx(value, rel=1e-9) for value in expected[count:]]


@pytest.mark.parametrize(("version", "first"), list(TALIB_START.items()))
def test_a_2_version_gives_the_1_version_s_values_after_its_warm_up(
    version: str, first: str
) -> None:
    """Only the start differs: after the @1 warm-up the first candles weigh under 1e-6 in
    both, so they agree within the tolerance of the golden tests."""
    params = CATALOG[first].params()
    count = CATALOG[first].warm_up(params)
    candles = random_walk(count + 500, 3)

    ours = CATALOG[version].run(candles, params).slice(count).to_numpy()
    theirs = CATALOG[first].run(candles, params).slice(count).to_numpy()

    np.testing.assert_allclose(ours, theirs, rtol=1e-6, atol=1e-8)
    assert CATALOG[version].warm_up(params) < count


def test_the_2_rsi_is_100_while_closes_rise_and_0_once_they_stayed_flat() -> None:
    assert run("rsi@2", steady(40, 0.5), window=2)[-1] == 100.0
    assert run("rsi@2", steady(40, -0.5), window=2)[-1] == 0.0
    # A rise of 1 and a fall of 0.5 start a mean rise of 0.5 and a mean fall of 0.25 (RSI
    # 200 / 3), which halve on every flat candle: from the candle 49 on they add up to under
    # 1e-14.
    rsi = run("rsi@2", closes([100.0, 101.0, 100.5] + [100.5] * 80), window=2)

    assert rsi[2:49] == [pytest.approx(200 / 3, rel=1e-12)] * 47
    assert rsi[49:] == [0.0] * 34


def test_the_2_adx_holds_where_the_candles_stop_moving() -> None:
    # A steady rise, then candles of one price: the smoothed true range fades to under 1e-14
    # and from there on the ADX stays as it was.
    rising = steady(30, 0.5)
    price = rising["close"][-1]
    flat = steady(150, 0.0).with_columns(
        pl.col("ts") + 30 * 1800,
        *(pl.lit(price).alias(name) for name in ("open", "high", "low", "close")),
    )
    frame = pl.concat([rising, flat])

    adx = run("adx@2", frame, window=2)

    expected = talib_adx_by_hand(frame, 2)
    assert adx[3:] == [pytest.approx(value, rel=1e-9) for value in expected[3:]]
    assert adx[29] == pytest.approx(100.0)
    assert adx[-1] == adx[-30]
