"""The worked example of docs/engine.md, run: the numbers the document shows are the ones the
engine, the metrics, the signals and the command give. A change here goes into the document
too, and the other way round. Values are compared to the decimals the document prints."""

import hashlib
import json
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from candlestack.cli import main
from candlestack.engine import InvalidInput, Result, Settings, backtest
from candlestack.metrics import stats
from candlestack.signals import long_only, threshold

PRINTED = 5e-7  # the document prints at least 6 decimals
START = 1_717_421_400  # 2024-06-03 13:30 UTC, the 09:30 open in New York
TS = [START + 1800 * row for row in range(5)]  # five 30-minute candles

CANDLES = pl.DataFrame(
    {
        "ts": TS,
        "open": [100.0, 100.0, 102.0, 99.0, 97.0],
        "high": [101.0, 102.0, 104.0, 99.5, 98.0],
        "low": [99.0, 99.5, 101.0, 96.0, 96.5],
        "close": [100.0, 101.0, 103.0, 97.0, 98.0],
    }
)
EXAMPLE = CANDLES.with_columns(signal=pl.Series([0.0, 1.0, 1.0, 1.0, 1.0]))
COSTS = {"stop_loss": 0.05, "fee_bps": 10}


def trade(
    signal_row,
    open_row,
    close_row,
    open_price,
    close_price,
    stop,
    reason,
    result,
    fees,
    slippage_cost=0.0,
) -> dict[str, Any]:
    return {
        "signal_ts": TS[signal_row],
        "open_ts": TS[open_row],
        "close_ts": TS[close_row],
        "side": 1,
        "open_price": open_price,
        "close_price": close_price,
        "stop_price": stop,
        "exit_reason": reason,
        "return": result,
        "fees": fees,
        "slippage_cost": slippage_cost,
    }


def check(
    result: Result, trades: list[dict[str, Any]], position: list[float], equity: list[float]
) -> None:
    assert result.trades.rows(named=True) == [pytest.approx(row, abs=PRINTED) for row in trades]
    assert result.series["position"].to_list() == position
    assert result.series["equity"].to_list() == pytest.approx(equity, abs=PRINTED)


def test_signal_close_holds_the_long_to_the_end() -> None:
    check(
        backtest(EXAMPLE, Settings(fill="signal_close", **COSTS)),
        [trade(1, 1, 4, 101, 98, 95.95, "end", -0.031673, 0.001970)],
        position=[0, 1, 1, 1, 0],
        equity=[1, 0.999, 1.018802, 0.959396, 0.968327],
    )


def test_next_open_is_stopped_out_on_the_low() -> None:
    check(
        backtest(EXAMPLE, Settings(fill="next_open", **COSTS)),
        [trade(1, 2, 3, 102, 96.9, 96.9, "stop", -0.05195, 0.00195)],
        position=[0, 0, 1, 0, 0],
        equity=[1, 1, 1.008804, 0.94805, 0.94805],
    )


def test_slippage_moves_the_prices_and_the_stop_level() -> None:
    check(
        backtest(EXAMPLE, Settings(fill="next_open", slippage_bps=10, **COSTS)),
        [trade(1, 2, 3, 102.102, 96.8999031, 96.9969, "stop", -0.05289905, 0.00194905, 0.0019)],
        position=[0, 0, 1, 0, 0],
        equity=[1, 1, 1.007795, 0.947101, 0.947101],
    )


def test_fees_and_slippage_cost_are_the_return_without_costs_minus_the_return() -> None:
    [row] = backtest(EXAMPLE, Settings(fill="next_open", slippage_bps=10, **COSTS)).trades.rows(
        named=True
    )

    assert row["fees"] + row["slippage_cost"] == pytest.approx(0.00384905, abs=PRINTED)
    assert 96.9969 / 102 - 1 - row["return"] == pytest.approx(0.00384905, abs=PRINTED)


def test_the_invalid_input_message() -> None:
    frame = EXAMPLE.with_columns(
        ts=pl.Series([TS[0], TS[1], TS[1], TS[3], TS[4]]),
        signal=pl.Series([0.0, 0.5, 1.0, 1.0, 1.0]),
    )

    with pytest.raises(InvalidInput) as error:
        backtest(frame, Settings())

    assert str(error.value) == (
        "Invalid backtest input: 1 row has a ts not after the previous row, first at row 2; "
        "1 row has a fractional signal (the size is the setting position_size), first at row 1."
    )


def test_the_statistics_of_the_signal_close_run() -> None:
    result = backtest(EXAMPLE, Settings(fill="signal_close", **COSTS))

    assert stats(result.trades, result.series, EXAMPLE) == pytest.approx(
        {
            "total_return": -0.031673,
            "max_drawdown": 0.058310,
            "sharpe": None,
            "sortino": None,
            "num_trades": 1,
            "win_rate": 0.0,
            "avg_trade_return": -0.031673,
            "best_trade_return": -0.031673,
            "worst_trade_return": -0.031673,
            "exposure": 0.6,
            "fees_paid": 0.001970,
            "buy_and_hold_return": -0.02,
        },
        abs=PRINTED,
    )


def test_threshold_and_long_only() -> None:
    signal = threshold(pl.Series([0.01, 0.3, -0.02, -0.08, 0.05]), 0.05)

    assert signal.to_list() == [0.0, 1.0, 0.0, -1.0, 1.0]
    assert long_only(signal).to_list() == [0.0, 1.0, 0.0, 0.0, 1.0]


OUT = Path("runs", "example")


def run_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Writes the example's files and runs the command of the document on them."""
    monkeypatch.chdir(tmp_path)  # the document's paths are relative
    CANDLES.write_parquet("candles.parquet")
    pl.DataFrame({"ts": TS, "prediction": [0.01, 0.3, 0.2, 0.1, 0.4]}).write_parquet(
        "predictions.parquet"
    )
    return main(
        [
            "run",
            "--candles",
            "candles.parquet",
            "--predictions",
            "predictions.parquet",
            "--threshold",
            "0.05",
            "--fill",
            "signal_close",
            "--stop-loss",
            "0.05",
            "--fee-bps",
            "10",
            "--out",
            str(OUT),
        ]
    )


def test_the_command_runs_the_example(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code = run_the_command(tmp_path, monkeypatch)

    assert code == 0
    assert capsys.readouterr().out == (
        f"Wrote trades.parquet, series.parquet, stats.json and run.json to {OUT}\n"
        "  trades        1\n"
        "  win rate      0.00%\n"
        "  total return  -3.17%\n"
        "  max drawdown  5.83%\n"
        "  sharpe        n/a\n"
        "  buy and hold  -2.00%\n"
    )


def test_the_command_writes_the_run_json_of_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_the_command(tmp_path, monkeypatch)

    record = json.loads((OUT / "run.json").read_text(encoding="utf-8"))
    hashes = {name: record["inputs"][name].pop("sha256") for name in record["inputs"]}
    assert record == {
        "engine_version": "0.4.0",
        "inputs": {
            "candles": {"path": "candles.parquet"},
            "predictions": {"path": "predictions.parquet"},
        },
        "settings": {
            "ts_column": "ts",
            "labels": "open",
            "timeframe_seconds": None,
            "model": None,
            "allow_gaps": False,
            "threshold": 0.05,
            "long_only": False,
            "fill": "signal_close",
            "stop_loss": 0.05,
            "drawdown_stop": None,
            "position_size": 1.0,
            "fee_bps": 10.0,
            "slippage_bps": 0.0,
        },
        "post_processing": {
            "calibrate": None,
            "ema": None,
            "rule": {"name": "threshold", "limit": 0.05},
            "long_only": False,
            "confirm": None,
            "min_hold": None,
        },
        "align": {"predictions_without_candle": 0, "candles_without_prediction": 0},
    }
    # The document shortens the hashes to their first 8 digits. They hash the bytes Polars
    # writes, so a Polars update that writes the files differently changes them there too.
    for name, prefix in [("candles", "509ac6b5"), ("predictions", "efe0645e")]:
        assert hashes[name] == hashlib.sha256(Path(f"{name}.parquet").read_bytes()).hexdigest()
        assert hashes[name].startswith(prefix)
