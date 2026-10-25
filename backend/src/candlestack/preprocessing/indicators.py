"""The indicator features of the BLESK models, each computed as the ``ta`` library computes it.

The BLESK models were trained on ``ta``'s numbers, quirks included (research #118): Keltner's
original version, the older Aroon on the close, Wilder's smoothing seeded with a plain mean. A
block here gives those numbers from the candles with Polars and NumPy, without ``ta``.

Each block takes the candles and gives one Float64 value per candle, null for its warm-up: the
first candles whose value would change if the candles started earlier. A block over the last
``window`` candles has a warm-up of the candles before its first full window. A recursive one
(an EMA, Wilder's smoothing) depends on every earlier candle: its warm-up ends where the first
candles weigh less than 1e-6 in a value, (1 - alpha) ** k < e ** -14, so its values do not
depend on where the candles start. The run loads that many candles before the period, so the
first value of the period is the same as on a longer history.
"""

from collections.abc import Callable
from typing import Annotated

import numpy as np
import polars as pl
from pydantic import BaseModel, ConfigDict, Field

HIGH, LOW, CLOSE = pl.col("high"), pl.col("low"), pl.col("close")
# How many smoothing windows a recursive block needs until its start weighs under 1e-6:
# (1 - 2 / (n + 1)) ** (7 n) and (1 - 1 / n) ** (14 n) are both below e ** -14.
EMA_WINDOWS = 7
WILDER_WINDOWS = 14
# ADX smooths Wilder's smoothing once more: its start's weight falls as k / n * (1 - 1 / n) ** k,
# under 1e-6 after about 18 windows, and its first value comes after 2 windows.
ADX_WINDOWS = 20
# Under this sum of the mean rise and the mean fall the closes count as flat and the RSI is 0,
# as in the data the BLESK models were trained on: on a long stretch of one close (a stock not
# yet listed, or suspended) both means fade towards 0 at the same pace, and their ratio alone
# would hold the last RSI for good. After a move of one cent, the sum falls under it only once
# the close stayed the same for about 480 candles (the default window of 20).
RSI_FLAT = 1e-14

Window = Annotated[int, Field(ge=2, le=1000)]
Multiple = Annotated[float, Field(gt=0, le=10)]


class _Params(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


def _window(default: int, description: str = "Candles in the window") -> Window:
    return Field(default=default, description=description)


class Bollinger(_Params):
    window: Window = _window(20)
    dev: Multiple = Field(default=2.0, description="Standard deviations from the mean")


class Keltner(_Params):
    window: Window = _window(20)


class Squeeze(_Params):
    bollinger_window: Window = _window(20, "Candles in the Bollinger bands")
    bollinger_dev: Multiple = Field(
        default=2.0, description="Standard deviations of the Bollinger bands"
    )
    keltner_window: Window = _window(20, "Candles in the mean close of the Keltner channel")
    keltner_atr_window: Window = _window(20, "Candles in the mean true range of the channel")
    keltner_multiplier: Multiple = Field(
        default=1.5, description="Mean true ranges from the mean close"
    )


class Rsi(_Params):
    window: Window = _window(20)


class Roc(_Params):
    window: Window = _window(20, "Candles back the close is compared with")


class Tsi(_Params):
    slow: Window = _window(25, "Candles in the first, slow EMA")
    fast: Window = _window(13, "Candles in the second, fast EMA")


class Atr(_Params):
    window: Window = _window(14)


class Adx(_Params):
    window: Window = _window(14)


class Aroon(_Params):
    window: Window = _window(25)


class Sma(_Params):
    window: Window = _window(20)


class Ema(_Params):
    window: Window = _window(20)


class Macd(_Params):
    fast: Window = _window(12, "Candles in the fast EMA")
    slow: Window = _window(26, "Candles in the slow EMA")
    signal: Window = _window(9, "Candles in the signal EMA of the MACD")


def _values(frame: pl.DataFrame, values: pl.Expr, warm_up: int) -> pl.Series:
    """``values`` on the candles, null for the first ``warm_up`` candles."""
    kept = pl.when(pl.int_range(pl.len()) >= warm_up).then(values)
    return frame.select(kept.cast(pl.Float64)).to_series()


def _true_range() -> pl.Expr:
    """The candle's true range as ``ta`` takes it: the first candle's is its high minus low."""
    before = CLOSE.shift(1)
    return pl.max_horizontal(HIGH - LOW, (HIGH - before).abs(), (LOW - before).abs())


def _wilder(values: pl.Expr, window: int, seed_at: int) -> pl.Expr:
    """Wilder's smoothing as ``ta`` does it: the mean of the ``window`` values up to the row
    ``seed_at``, then ``(previous * (window - 1) + value) / window`` on each later row."""
    row = pl.int_range(pl.len())
    seeded = (
        pl.when(row == seed_at).then(values.rolling_mean(window)).when(row > seed_at).then(values)
    )
    return seeded.ewm_mean(alpha=1 / window, adjust=False)


def _ema(values: pl.Expr, window: int) -> pl.Expr:
    """``ta``'s EMA: from the first value, null until ``window`` values were seen."""
    return values.ewm_mean(span=window, adjust=False, min_samples=window)


def _bollinger(window: int, dev: float) -> tuple[pl.Expr, pl.Expr]:
    mean = CLOSE.rolling_mean(window)
    deviation = CLOSE.rolling_std(window, ddof=0)
    return mean + dev * deviation, mean - dev * deviation


def bollinger_high(frame: pl.DataFrame, params: Bollinger) -> pl.Series:
    """``bollinger-high@1``: the mean close plus ``dev`` standard deviations (of the population)."""
    return _values(frame, _bollinger(params.window, params.dev)[0], params.window - 1)


def bollinger_low(frame: pl.DataFrame, params: Bollinger) -> pl.Series:
    """``bollinger-low@1``: the mean close minus ``dev`` standard deviations."""
    return _values(frame, _bollinger(params.window, params.dev)[1], params.window - 1)


def keltner_high(frame: pl.DataFrame, params: Keltner) -> pl.Series:
    """``keltner-high@1``: ``ta``'s original Keltner channel, the mean of
    ``(4 high - 2 low + close) / 3``."""
    band = ((4 * HIGH - 2 * LOW + CLOSE) / 3).rolling_mean(params.window)
    return _values(frame, band, params.window - 1)


def keltner_low(frame: pl.DataFrame, params: Keltner) -> pl.Series:
    """``keltner-low@1``: the mean of ``(-2 high + 4 low + close) / 3``."""
    band = ((-2 * HIGH + 4 * LOW + CLOSE) / 3).rolling_mean(params.window)
    return _values(frame, band, params.window - 1)


def squeeze_warm_up(params: Squeeze) -> int:
    # The first candle's true range has no close before it, so the mean true range waits one
    # more candle.
    return max(params.bollinger_window - 1, params.keltner_window - 1, params.keltner_atr_window)


def squeeze(frame: pl.DataFrame, params: Squeeze) -> pl.Series:
    """``squeeze@1``: 1 when both Bollinger bands lie inside a Keltner channel of the mean close
    plus and minus ``keltner_multiplier`` mean true ranges, else 0."""
    high, low = _bollinger(params.bollinger_window, params.bollinger_dev)
    middle = CLOSE.rolling_mean(params.keltner_window)
    width = params.keltner_multiplier * _true_range().rolling_mean(params.keltner_atr_window)
    inside = (high < middle + width) & (low > middle - width)
    return _values(frame, inside.cast(pl.Float64), squeeze_warm_up(params))


def rsi_warm_up(params: Rsi) -> int:
    return WILDER_WINDOWS * params.window


def rsi(frame: pl.DataFrame, params: Rsi) -> pl.Series:
    """``rsi@1``: the relative strength index with Wilder's smoothing from the first candle,
    whose change ``ta`` counts as 0; 100 while no close fell, and 0 once the closes stayed
    flat so long that the mean rise and the mean fall add up to less than ``RSI_FLAT``."""
    change = CLOSE.diff()
    alpha = 1 / params.window
    up = pl.when(change > 0).then(change).otherwise(0.0).ewm_mean(alpha=alpha, adjust=False)
    down = pl.when(change < 0).then(-change).otherwise(0.0).ewm_mean(alpha=alpha, adjust=False)
    value = (
        pl.when(up + down < RSI_FLAT)
        .then(0.0)
        .when(down == 0)
        .then(100.0)
        .otherwise(100 - 100 / (1 + up / down))
    )
    return _values(frame, value, rsi_warm_up(params))


def roc_warm_up(params: Roc) -> int:
    return params.window


def roc(frame: pl.DataFrame, params: Roc) -> pl.Series:
    """``roc@1``: the change of the close from ``window`` candles before, in percent."""
    before = CLOSE.shift(params.window)
    return _values(frame, (CLOSE - before) / before * 100, params.window)


def tsi_warm_up(params: Tsi) -> int:
    return EMA_WINDOWS * (params.slow + params.fast)


def tsi(frame: pl.DataFrame, params: Tsi) -> pl.Series:
    """``tsi@1``: the true strength index, the close's change smoothed by two EMAs over its
    absolute value smoothed the same way, in percent."""
    change = CLOSE.diff()
    smoothed = _ema(_ema(change, params.slow), params.fast)
    absolute = _ema(_ema(change.abs(), params.slow), params.fast)
    return _values(frame, smoothed / absolute * 100, tsi_warm_up(params))


def atr_warm_up(params: Atr) -> int:
    # Seeded at the candle window - 1, then 14 windows of Wilder's smoothing.
    return (WILDER_WINDOWS + 1) * params.window - 1


def atr(frame: pl.DataFrame, params: Atr) -> pl.Series:
    """``atr@1``: the average true range, Wilder's smoothing seeded with the mean of the first
    ``window`` true ranges."""
    value = _wilder(_true_range(), params.window, params.window - 1)
    return _values(frame, value, atr_warm_up(params))


def adx_warm_up(params: Adx) -> int:
    return ADX_WINDOWS * params.window


def adx(frame: pl.DataFrame, params: Adx) -> pl.Series:
    """``adx@1``: the average directional index as ``ta`` 0.11 computes it: the directional
    movements and the true range smoothed by Wilder from the candle ``window``, then the
    directional index smoothed again, from the candle ``2 window - 1``."""
    window = params.window
    before = CLOSE.shift(1)
    true_range = pl.max_horizontal(HIGH, before) - pl.min_horizontal(LOW, before)
    true_range = pl.when(before.is_not_null()).then(true_range)
    rise, fall = HIGH.diff(), -LOW.diff()
    plus = pl.when((rise > fall) & (rise > 0)).then(rise).otherwise(0.0)
    minus = pl.when((fall > rise) & (fall > 0)).then(fall).otherwise(0.0)
    plus, minus = (pl.when(before.is_not_null()).then(move) for move in (plus, minus))
    smoothed_range = _wilder(true_range, window, window)
    plus_di = _ratio(_wilder(plus, window, window), smoothed_range)
    minus_di = _ratio(_wilder(minus, window, window), smoothed_range)
    index = _ratio((plus_di - minus_di).abs(), plus_di + minus_di)
    return _values(frame, _wilder(index, window, 2 * window - 1), adx_warm_up(params))


def _ratio(numerator: pl.Expr, denominator: pl.Expr) -> pl.Expr:
    """100 times the ratio, 0 where the denominator is 0, as ``ta``'s ADX takes it."""
    return pl.when(denominator == 0).then(0.0).otherwise(100 * numerator / denominator)


def _aroon(frame: pl.DataFrame, window: int, pick: Callable[..., np.ndarray]) -> pl.Series:
    """``ta`` 0.10's Aroon on the close: 100 x (the position of the extreme close in the last
    ``window`` closes, the oldest at 1) / window; the first of equal extremes counts."""
    close = frame["close"].to_numpy()
    values = np.full(close.size, np.nan)
    if close.size >= window:
        views = np.lib.stride_tricks.sliding_window_view(close, window)
        values[window - 1 :] = (pick(views, axis=1) + 1) / window * 100
    return _values(frame, pl.lit(pl.Series(values)), window - 1)


def aroon_up(frame: pl.DataFrame, params: Aroon) -> pl.Series:
    """``aroon-up@1``: how recent the highest close of the window is, 100 when it is this one."""
    return _aroon(frame, params.window, np.argmax)


def aroon_down(frame: pl.DataFrame, params: Aroon) -> pl.Series:
    """``aroon-down@1``: how recent the lowest close of the window is, 100 when it is this one."""
    return _aroon(frame, params.window, np.argmin)


def aroon(frame: pl.DataFrame, params: Aroon) -> pl.Series:
    """``aroon@1``: Aroon up minus Aroon down."""
    return aroon_up(frame, params) - aroon_down(frame, params)


def sma(frame: pl.DataFrame, params: Sma) -> pl.Series:
    """``sma@1``: the mean close of the last ``window`` candles."""
    return _values(frame, CLOSE.rolling_mean(params.window), params.window - 1)


def ema_warm_up(params: Ema) -> int:
    return EMA_WINDOWS * params.window


def ema(frame: pl.DataFrame, params: Ema) -> pl.Series:
    """``ema@1``: the EMA of the close (``span`` ``window``) from the first candle."""
    return _values(frame, _ema(CLOSE, params.window), ema_warm_up(params))


def macd_warm_up(params: Macd) -> int:
    return EMA_WINDOWS * (max(params.fast, params.slow) + params.signal)


def macd_diff(frame: pl.DataFrame, params: Macd) -> pl.Series:
    """``macd-diff@1``: the MACD (the fast EMA minus the slow EMA of the close) minus its signal
    EMA, the MACD histogram."""
    line = _ema(CLOSE, params.fast) - _ema(CLOSE, params.slow)
    return _values(frame, line - _ema(line, params.signal), macd_warm_up(params))
