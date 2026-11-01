"""Checks the input frame and turns it into the plain lists the loop runs over."""

from dataclasses import dataclass

import polars as pl

from candlestack.engine.errors import InvalidInput

_COLUMNS = ("ts", "open", "high", "low", "close", "signal")
_PRICES = ("open", "high", "low", "close")
_SIGNAL = pl.col("signal")
_FINITE_SIGNAL = _SIGNAL.is_finite()

# What a row may not have. NaN compares above every number in Polars, so the range rules look
# at finite values only and a NaN is reported once, as non-finite.
_RULES = [
    ("a missing ts", pl.col("ts").is_null()),
    ("a ts not after the previous row", pl.col("ts").diff() <= 0),
    (
        "a missing or non-finite price",
        pl.any_horizontal(pl.col(_PRICES).is_finite().not_().fill_null(value=True)),
    ),
    (
        "a price at or below zero",
        pl.any_horizontal(*(pl.col(name).is_finite() & (pl.col(name) <= 0) for name in _PRICES)),
    ),
    ("a missing or non-finite signal", _FINITE_SIGNAL.not_().fill_null(value=True)),
    ("a signal beyond -1 or +1", _FINITE_SIGNAL & (_SIGNAL.abs() > 1)),
    (
        "a fractional signal (the size is the setting position_size)",
        _FINITE_SIGNAL & (_SIGNAL.abs() < 1) & (_SIGNAL != 0),
    ),
]


@dataclass(frozen=True, slots=True)
class Candles:
    """The checked input: ``frame`` with ``ts`` as Int64 and the prices and ``signal`` as
    Float64, and its columns as Python lists, which a loop reads much faster than a frame."""

    frame: pl.DataFrame
    ts: list[int]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    signals: list[int]


def read(frame: pl.DataFrame) -> Candles:
    """Checks ``frame`` against the input contract and returns its columns; other columns are
    ignored.

    Raises:
        InvalidInput: a column is missing or not numeric, or rows break a rule; the message
            names every broken rule, how many rows break it and the first one.
    """
    _check_columns(frame)
    data = frame.select(pl.col("ts").cast(pl.Int64), pl.col(*_PRICES, "signal").cast(pl.Float64))
    _check_rows(data)
    return Candles(
        frame=data,
        ts=data["ts"].to_list(),
        opens=data["open"].to_list(),
        highs=data["high"].to_list(),
        lows=data["low"].to_list(),
        closes=data["close"].to_list(),
        signals=data["signal"].cast(pl.Int8).to_list(),
    )


def _check_columns(frame: pl.DataFrame) -> None:
    schema = frame.schema
    missing = [name for name in _COLUMNS if name not in schema]
    if missing:
        raise InvalidInput(
            f"Invalid backtest input: no column {', '.join(missing)}. "
            "The engine needs ts, open, high, low, close and signal."
        )
    problems = [
        f"{name} is {schema[name]}, not a number"
        for name in (*_PRICES, "signal")
        if not schema[name].is_numeric()
    ]
    if not schema["ts"].is_integer():
        problems.insert(0, f"ts is {schema['ts']}, not integer epoch seconds")
    if problems:
        raise InvalidInput(f"Invalid backtest input: {'; '.join(problems)}.")


def _check_rows(data: pl.DataFrame) -> None:
    flags = data.with_row_index("row").select(
        "row", *(rule.alias(f"rule{i}") for i, (_, rule) in enumerate(_RULES))
    )
    problems = []
    for i, (what, _) in enumerate(_RULES):
        bad = flags.filter(pl.col(f"rule{i}"))["row"]
        if bad.len():
            has = "row has" if bad.len() == 1 else "rows have"
            problems.append(f"{bad.len()} {has} {what}, first at row {bad[0]}")
    if problems:
        raise InvalidInput(f"Invalid backtest input: {'; '.join(problems)}.")
