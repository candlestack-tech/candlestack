import polars as pl
import pytest

from candlestack.engine import ENGINE_VERSION, InvalidInput, Settings, backtest

NAN, INF = float("nan"), float("inf")


def valid(**columns: pl.Series) -> pl.DataFrame:
    """Five valid candles that go long on row 1 and flat on row 3, with ``columns`` replaced."""
    frame = pl.DataFrame(
        {
            "ts": [0, 60, 120, 180, 240],
            "open": [10.0, 10.0, 11.0, 12.0, 12.0],
            "high": [10.0, 11.0, 12.0, 13.0, 13.0],
            "low": [10.0, 9.0, 10.0, 11.0, 11.0],
            "close": [10.0, 11.0, 12.0, 12.0, 13.0],
            "signal": [0.0, 1.0, 1.0, 0.0, 0.0],
        }
    )
    return frame.with_columns(**columns)


def test_the_result_follows_the_contract_schemas() -> None:
    result = backtest(valid(), Settings())

    assert result.trades.schema == pl.Schema(
        {
            "signal_ts": pl.Int64,
            "open_ts": pl.Int64,
            "close_ts": pl.Int64,
            "side": pl.Int8,
            "open_price": pl.Float64,
            "close_price": pl.Float64,
            "stop_price": pl.Float64,
            "exit_reason": pl.String,
            "return": pl.Float64,
            "fees": pl.Float64,
            "slippage_cost": pl.Float64,
        }
    )
    assert result.series.schema == pl.Schema(
        {"ts": pl.Int64, "signal": pl.Float64, "position": pl.Float64, "equity": pl.Float64}
    )
    assert result.series["ts"].to_list() == [0, 60, 120, 180, 240]
    assert result.series["signal"].to_list() == [0.0, 1.0, 1.0, 0.0, 0.0]
    assert result.engine_version == ENGINE_VERSION


def test_an_empty_frame_gives_empty_results_with_the_same_schemas() -> None:
    full = backtest(valid(), Settings())

    result = backtest(valid().clear(), Settings())

    assert result.trades.schema == full.trades.schema
    assert result.trades.is_empty()
    assert result.series.schema == full.series.schema
    assert result.series.is_empty()


def test_integer_columns_are_cast_and_other_columns_ignored() -> None:
    frame = valid().with_columns(
        pl.col("ts").cast(pl.Int32),
        pl.col("open", "high", "low", "close").cast(pl.Int64),
        pl.col("signal").cast(pl.Int8),
        volume=pl.lit(5.0),
        note=pl.lit("ignored"),
    )

    result = backtest(frame, Settings())

    expected = backtest(valid(), Settings())
    assert result.trades.height == 1
    assert result.trades.equals(expected.trades)
    assert result.series.equals(expected.series)


@pytest.mark.parametrize(("drop", "missing"), [(["signal"], "signal"), (["ts", "low"], "ts, low")])
def test_missing_columns_are_named(drop: list[str], missing: str) -> None:
    with pytest.raises(InvalidInput) as error:
        backtest(valid().drop(drop), Settings())

    assert str(error.value) == (
        f"Invalid backtest input: no column {missing}. "
        "The engine needs ts, open, high, low, close and signal."
    )


def test_columns_of_the_wrong_type_are_named() -> None:
    frame = valid(
        ts=pl.Series([0.0, 60.0, 120.0, 180.0, 240.0]),
        close=pl.Series(["10", "11", "12", "12", "13"]),
        signal=pl.Series([False, True, True, False, False]),
    )

    with pytest.raises(InvalidInput) as error:
        backtest(frame, Settings())

    assert str(error.value) == (
        "Invalid backtest input: ts is Float64, not integer epoch seconds; "
        "close is String, not a number; signal is Boolean, not a number."
    )


TS = "a ts not after the previous row"
PRICE = "a missing or non-finite price"
SIGNAL = "a missing or non-finite signal"
FRACTION = "a fractional signal (the size is the setting position_size)"


@pytest.mark.parametrize(
    ("column", "values", "problem"),
    [
        ("ts", [0, 60, 60, 180, 240], f"1 row has {TS}, first at row 2"),
        ("ts", [0, 120, 60, 30, 240], f"2 rows have {TS}, first at row 2"),
        ("ts", [0, 60, None, 180, 240], "1 row has a missing ts, first at row 2"),
        ("open", [10, NAN, 11, 12, 12], f"1 row has {PRICE}, first at row 1"),
        ("high", [10, 11, None, 13, 13], f"1 row has {PRICE}, first at row 2"),
        ("low", [10, 9, 10, -INF, 11], f"1 row has {PRICE}, first at row 3"),
        ("close", [10, 11, 12, 12, INF], f"1 row has {PRICE}, first at row 4"),
        ("low", [10, 0, 10, 11, -1], "2 rows have a price at or below zero, first at row 1"),
        ("signal", [0, 1, None, 0, 0], f"1 row has {SIGNAL}, first at row 2"),
        ("signal", [0, NAN, 1, 0, 0], f"1 row has {SIGNAL}, first at row 1"),
        ("signal", [0, 1, 2, 0, -3], "2 rows have a signal beyond -1 or +1, first at row 2"),
        ("signal", [0, 0.5, 1, -0.25, 0], f"2 rows have {FRACTION}, first at row 1"),
    ],
)
def test_rows_that_break_a_rule_are_counted(
    column: str, values: list[float | None], problem: str
) -> None:
    dtype = pl.Int64 if column == "ts" else pl.Float64

    with pytest.raises(InvalidInput) as error:
        backtest(valid(**{column: pl.Series(values, dtype=dtype)}), Settings())

    assert str(error.value) == f"Invalid backtest input: {problem}."


def test_every_broken_rule_is_reported_at_once() -> None:
    frame = valid(
        ts=pl.Series([0, 60, 60, 180, 240]), signal=pl.Series([0, 1, 0.5, 0, 0], dtype=pl.Float64)
    )

    with pytest.raises(InvalidInput) as error:
        backtest(frame, Settings())

    assert str(error.value) == (
        "Invalid backtest input: 1 row has a ts not after the previous row, first at row 2; "
        "1 row has a fractional signal (the size is the setting position_size), first at row 2."
    )


def test_invalid_input_is_a_value_error() -> None:
    assert issubclass(InvalidInput, ValueError)
