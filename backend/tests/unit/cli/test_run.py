"""``candlestack-bt run`` called in-process on small Parquet files written to ``tmp_path``."""

import hashlib
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from candlestack.cli import main
from candlestack.engine import ENGINE_VERSION, Result, Settings, backtest
from candlestack.metrics import drawdown, stats
from candlestack.signals import (
    align,
    confirm,
    ema,
    hysteresis,
    kmeans,
    long_only,
    min_hold,
    relabel,
    threshold,
    zscore,
)

HALF_HOUR = 1800
DAY = 86_400
FILES = ["trades.parquet", "series.parquet", "stats.json", "run.json"]
STATS_NAMES = [
    "total_return",
    "max_drawdown",
    "sharpe",
    "sortino",
    "num_trades",
    "win_rate",
    "avg_trade_return",
    "best_trade_return",
    "worst_trade_return",
    "exposure",
    "fees_paid",
    "buy_and_hold_return",
]


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


# Three days of eight 30m candles from 14:30 UTC: enough days for a Sharpe ratio.
OPENS = [utc("2026-01-05 14:30") + day * DAY + k * HALF_HOUR for day in range(3) for k in range(8)]


def candles(opens: list[int]) -> pl.DataFrame:
    """Candles that rise and fall; each opens at the close before it."""
    closes = [100 + 4 * math.sin(i / 2) + 0.3 * i for i in range(len(opens))]
    starts = [100.0, *closes[:-1]]
    return pl.DataFrame(
        {
            "ts": opens,
            "open": starts,
            "high": [max(pair) + 0.5 for pair in zip(starts, closes, strict=True)],
            "low": [min(pair) - 0.5 for pair in zip(starts, closes, strict=True)],
            "close": closes,
        }
    )


def predictions(opens: list[int], wave: float) -> pl.DataFrame:
    return pl.DataFrame(
        {"ts": opens, "prediction": [0.2 * math.cos(i / wave) for i in range(len(opens))]},
        schema={"ts": pl.Int64, "prediction": pl.Float32},
    )


def write(frame: pl.DataFrame, path: Path) -> Path:
    frame.write_parquet(path)
    return path


@pytest.fixture
def plain(tmp_path: Path) -> tuple[Path, Path]:
    """A candles and a predictions file in the engine's terms: ``ts`` is the open time, one
    model, no model column."""
    return (
        write(candles(OPENS), tmp_path / "candles.parquet"),
        write(predictions(OPENS, 1.5), tmp_path / "predictions.parquet"),
    )


@pytest.fixture
def supervisor(tmp_path: Path) -> tuple[Path, Path]:
    """The files in the supervisor's terms: ``unix`` (Int32 for the candles) is the close time,
    a stock column, and the predictions of two models in one file."""
    close_labels = (pl.col("ts") + HALF_HOUR).alias("unix")
    two_models = pl.concat(
        [
            predictions(OPENS, 1.5).with_columns(model=pl.lit("A")),
            predictions(OPENS, 0.7).with_columns(model=pl.lit("B")),
        ]
    )
    return (
        write(
            candles(OPENS).select(
                pl.lit("AAPL").alias("stock"), close_labels.cast(pl.Int32), pl.exclude("ts")
            ),
            tmp_path / "AAPL_ohlcv.parquet",
        ),
        write(
            two_models.select(close_labels, pl.lit("AAPL").alias("stock"), "model", "prediction"),
            tmp_path / "predictions_0.parquet",
        ),
    )


def run(candles_path: Path, predictions_path: Path, out: Path, *options: str) -> int:
    return main(
        [
            "run",
            "--candles",
            str(candles_path),
            "--predictions",
            str(predictions_path),
            "--out",
            str(out),
            *options,
        ]
    )


def in_code(
    candles_frame: pl.DataFrame,
    predictions_frame: pl.DataFrame,
    settings: Settings,
    *,
    model: str | None = None,
    limit: float = 0.0,
    only_long: bool = False,
) -> tuple[Result, dict[str, Any]]:
    """The run the command makes, from code."""
    frame, _ = align(candles_frame, predictions_frame, model=model)
    signal = threshold(frame["prediction"], limit)
    if only_long:
        signal = long_only(signal)
    result = backtest(frame.with_columns(signal), settings)
    return result, stats(result.trades, result.series, frame)


def assert_written(out: Path, result: Result, values: dict[str, Any]) -> None:
    assert sorted(path.name for path in out.iterdir()) == sorted(FILES)
    assert_frame_equal(pl.read_parquet(out / "trades.parquet"), result.trades, check_exact=True)
    assert_frame_equal(
        pl.read_parquet(out / "series.parquet"),
        result.series.with_columns(drawdown(result.series)),
        check_exact=True,
    )
    written = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert list(written) == STATS_NAMES
    assert written == values


# The command and the same calls from code


def test_the_command_gives_the_numbers_of_the_calls_from_code(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "runs" / "plain"

    assert run(*plain, out, "--stop-loss", "0.02") == 0

    settings = Settings(stop_loss=0.02)
    result, values = in_code(pl.read_parquet(plain[0]), pl.read_parquet(plain[1]), settings)
    # The fixture covers shorts and the stop and end exits, so the files are checked on them.
    assert result.trades.height >= 3
    assert -1 in result.trades["side"]
    assert {"stop", "end"} <= set(result.trades["exit_reason"])
    assert values["sharpe"] is not None
    assert_written(out, result, values)
    assert capsys.readouterr().err == ""


def test_the_supervisors_files_give_the_numbers_of_the_calls_from_code(
    supervisor: tuple[Path, Path], tmp_path: Path
) -> None:
    out = tmp_path / "runs" / "supervisor"
    labels = ["--ts-column", "unix", "--labels", "close", "--timeframe", "30m"]
    signals = ["--model", "B", "--threshold", "0.05", "--long-only"]
    engine = ["--fill", "signal_close", "--stop-loss", "0.02"]
    costs = ["--fee-bps", "5", "--slippage-bps", "1"]

    assert run(*supervisor, out, *labels, *signals, *engine, *costs) == 0

    def opened(path: Path) -> pl.DataFrame:
        return relabel(pl.read_parquet(path).rename({"unix": "ts"}), HALF_HOUR)

    settings = Settings(fill="signal_close", stop_loss=0.02, fee_bps=5, slippage_bps=1)
    result, values = in_code(
        opened(supervisor[0]),
        opened(supervisor[1]),
        settings,
        model="B",
        limit=0.05,
        only_long=True,
    )
    assert result.trades.height >= 3
    assert result.trades["side"].to_list() == [1] * result.trades.height
    assert result.series["ts"].to_list() == OPENS  # labelled by the open again
    assert_written(out, result, values)


@pytest.mark.parametrize(
    "options",
    [
        ["--threshold", "0.15"],
        ["--long-only"],
        ["--fill", "signal_close"],
        ["--stop-loss", "0.005"],
        ["--fee-bps", "10"],
        ["--slippage-bps", "10"],
        ["--calibrate", "zscore", "--calibration-window", "4"],
        ["--calibrate", "rank", "--calibration-window", "4"],
        ["--ema", "3"],
        ["--rule", "hysteresis", "--threshold", "0.1", "--exit", "0.02"],
        ["--rule", "kmeans", "--k", "2", "--kmeans-window", "4", "--refit-every", "1"],
        ["--confirm", "2"],
        ["--min-hold", "6"],
        ["--drawdown-stop", "0.01"],
    ],
)
def test_each_option_reaches_its_step(
    plain: tuple[Path, Path], tmp_path: Path, options: list[str]
) -> None:
    # Each changes the trades of the defaults, so none is lost on the way to its step.
    assert run(*plain, tmp_path / "defaults") == 0

    assert run(*plain, tmp_path / "changed", *options) == 0

    defaults = pl.read_parquet(tmp_path / "defaults" / "trades.parquet")
    assert not pl.read_parquet(tmp_path / "changed" / "trades.parquet").equals(defaults)


# The post-processing: each step is its function from signals, in the documented order


@pytest.mark.parametrize(
    ("options", "rule"),
    [
        pytest.param(
            ["--threshold", "0.1"],
            lambda p: threshold(p, 0.1),
            id="threshold",
        ),
        pytest.param(
            ["--rule", "hysteresis", "--threshold", "0.1", "--exit", "0.02"],
            lambda p: hysteresis(p, 0.1, 0.02),
            id="hysteresis",
        ),
        pytest.param(
            ["--rule", "kmeans", "--k", "3", "--kmeans-window", "6", "--refit-every", "2"],
            lambda p: kmeans(p, 3, 6, 2),
            id="kmeans",
        ),
    ],
)
def test_each_rule_gives_the_numbers_of_its_function(
    plain: tuple[Path, Path], tmp_path: Path, options: list[str], rule
) -> None:
    out = tmp_path / "out"

    assert run(*plain, out, *options) == 0

    frame, _ = align(pl.read_parquet(plain[0]), pl.read_parquet(plain[1]))
    result = backtest(frame.with_columns(rule(frame["prediction"])), Settings())
    assert result.trades.height >= 2
    assert_written(out, result, stats(result.trades, result.series, frame))


def test_the_steps_go_in_the_documented_order(plain: tuple[Path, Path], tmp_path: Path) -> None:
    # Calibration, ema, the rule, long_only, confirm, min_hold; each from signals.
    out = tmp_path / "out"
    steps = ["--calibrate", "zscore", "--calibration-window", "4", "--ema", "2"]
    rule = ["--threshold", "0.5", "--long-only", "--confirm", "2", "--min-hold", "3"]

    assert run(*plain, out, *steps, *rule, "--drawdown-stop", "0.02") == 0

    frame, _ = align(pl.read_parquet(plain[0]), pl.read_parquet(plain[1]))
    predictions = ema(zscore(frame["prediction"], 4), 2)
    signal = min_hold(confirm(long_only(threshold(predictions, 0.5)), 2), 3)
    result = backtest(frame.with_columns(signal), Settings(drawdown_stop=0.02))
    assert result.trades.height >= 1
    assert result.trades["side"].to_list() == [1] * result.trades.height
    assert_written(out, result, stats(result.trades, result.series, frame))


def test_confirm_comes_before_min_hold(tmp_path: Path) -> None:
    # A long of one row: confirmed over 2 rows it never counts, so there is nothing to hold.
    # Held first, it would last 3 rows and be confirmed: the other order would trade.
    opens = OPENS[:8]
    values = pl.DataFrame({"ts": opens, "prediction": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]})
    out = tmp_path / "out"

    code = run(
        write(candles(opens), tmp_path / "c.parquet"),
        write(values, tmp_path / "p.parquet"),
        out,
        "--confirm",
        "2",
        "--min-hold",
        "3",
    )

    assert code == 0
    series = pl.read_parquet(out / "series.parquet")
    assert series["signal"].to_list() == [0.0] * 8
    assert pl.read_parquet(out / "trades.parquet").is_empty()
    other_order = confirm(min_hold(threshold(values["prediction"], 0.0), 3), 2)
    assert other_order.to_list() == [0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0]


def test_what_a_step_refuses_is_a_message(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # ema refuses an infinite prediction, which threshold alone would take.
    infinite = pl.read_parquet(plain[1]).with_columns(
        prediction=pl.when(pl.int_range(pl.len()) == 3)
        .then(float("inf"))
        .otherwise("prediction")
        .cast(pl.Float32)
    )
    out = tmp_path / "out"

    code = run(plain[0], write(infinite, tmp_path / "inf.parquet"), out, "--ema", "2")

    assert_refused(code, capsys, out, "A prediction is infinite")


# What the run writes and prints


def test_a_run_writes_its_settings_engine_version_input_hashes_and_align_report(
    supervisor: tuple[Path, Path], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    options = ["--ts-column", "unix", "--labels", "close", "--timeframe", "30m", "--model", "B"]

    assert run(*supervisor, out, *options) == 0

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record == {
        "engine_version": ENGINE_VERSION,
        "inputs": {
            name: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for name, path in zip(["candles", "predictions"], supervisor, strict=True)
        },
        "settings": {
            "ts_column": "unix",
            "labels": "close",
            "timeframe_seconds": HALF_HOUR,
            "model": "B",
            "allow_gaps": False,
            "threshold": 0.0,
            "long_only": False,
            "fill": "next_open",
            "stop_loss": None,
            "drawdown_stop": None,
            "position_size": 1.0,
            "fee_bps": 0.0,
            "slippage_bps": 0.0,
        },
        "post_processing": {
            "calibrate": None,
            "ema": None,
            "rule": {"name": "threshold", "limit": 0.0},
            "long_only": False,
            "confirm": None,
            "min_hold": None,
        },
        "align": {"predictions_without_candle": 0, "candles_without_prediction": 0},
    }


def test_run_json_lists_the_post_processing_in_its_order(
    plain: tuple[Path, Path], tmp_path: Path
) -> None:
    out = tmp_path / "out"
    steps = ["--calibrate", "rank", "--calibration-window", "5", "--ema", "3", "--long-only"]
    rule = ["--rule", "hysteresis", "--threshold", "0.1", "--exit", "0.05"]
    held = ["--confirm", "2", "--min-hold", "4", "--drawdown-stop", "0.2"]
    held += ["--position-size", "0.5"]

    assert run(*plain, out, *steps, *rule, *held) == 0

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert list(record) == ["engine_version", "inputs", "settings", "post_processing", "align"]
    assert record["settings"]["threshold"] == 0.1
    assert record["settings"]["long_only"] is True
    assert record["settings"]["drawdown_stop"] == 0.2
    assert record["settings"]["position_size"] == 0.5
    assert record["post_processing"] == {
        "calibrate": {"name": "rank", "window": 5},
        "ema": {"span": 3},
        "rule": {"name": "hysteresis", "enter": 0.1, "exit": 0.05},
        "long_only": True,
        "confirm": {"candles": 2},
        "min_hold": {"candles": 4},
    }
    steps_in_order = ["calibrate", "ema", "rule", "long_only", "confirm", "min_hold"]
    assert list(record["post_processing"]) == steps_in_order


def test_the_kmeans_rule_has_no_threshold(plain: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "out"
    rule = ["--rule", "kmeans", "--k", "2", "--kmeans-window", "4", "--refit-every", "3"]

    assert run(*plain, out, *rule) == 0

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["threshold"] is None
    assert record["post_processing"]["rule"] == {
        "name": "kmeans",
        "k": 2,
        "window": 4,
        "refit_every": 3,
    }


@pytest.mark.parametrize(
    ("timeframe", "seconds"), [("30m", 1800), ("1h", 3600), ("4h", 14_400), ("1d", 86_400)]
)
def test_the_timeframe_is_a_number_and_a_unit(
    plain: tuple[Path, Path], tmp_path: Path, timeframe: str, seconds: int
) -> None:
    # Both files move back by the same timeframe, so they still align.
    out = tmp_path / "out"

    assert run(*plain, out, "--labels", "close", "--timeframe", timeframe) == 0

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["timeframe_seconds"] == seconds
    series = pl.read_parquet(out / "series.parquet")
    assert series["ts"].to_list() == [ts - seconds for ts in OPENS]


def gappy(tmp_path: Path) -> Path:
    """Predictions without those of candles 5 to 7 and with one between two candles."""
    values = predictions(OPENS, 1.5).filter(~pl.int_range(pl.len()).is_between(5, 7))
    between = pl.DataFrame(
        {"ts": [OPENS[10] + 60], "prediction": [0.5]},
        schema={"ts": pl.Int64, "prediction": pl.Float32},
    )
    return write(pl.concat([values, between]), tmp_path / "gappy.parquet")


def test_allow_gaps_aligns_without_strict_and_counts_the_gaps(
    plain: tuple[Path, Path], tmp_path: Path
) -> None:
    gaps = gappy(tmp_path)
    out = tmp_path / "out"

    assert run(plain[0], gaps, out, "--allow-gaps") == 0

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["allow_gaps"] is True
    assert record["align"] == {"predictions_without_candle": 1, "candles_without_prediction": 3}
    frame, _ = align(pl.read_parquet(plain[0]), pl.read_parquet(gaps), strict=False)
    result = backtest(frame.with_columns(threshold(frame["prediction"], 0.0)), Settings())
    assert_written(out, result, stats(result.trades, result.series, frame))


def test_without_allow_gaps_a_gap_is_refused(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Strict alignment: the prediction without a candle is the first thing it refuses.
    out = tmp_path / "out"

    code = run(plain[0], gappy(tmp_path), out)

    message = (
        f"1 prediction has no candle, first at ts {OPENS[10] + 60}: check that both are "
        "labelled by the candle's open (relabel), or pass --allow-gaps to drop them"
    )
    assert_refused(code, capsys, out, message)


def test_without_allow_gaps_a_candle_without_a_prediction_is_refused(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    holes = predictions(OPENS, 1.5).filter(pl.col("ts") != OPENS[4])
    out = tmp_path / "out"

    code = run(plain[0], write(holes, tmp_path / "holes.parquet"), out)

    message = (
        f"1 candle has no prediction, first at ts {OPENS[4]}: pass --allow-gaps to repeat the "
        "previous prediction on them"
    )
    assert_refused(code, capsys, out, message)


def test_the_summary_and_stats_of_a_hand_made_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # One day: long from the close of row 1 (100) to the end (121). One day has no Sharpe.
    opens = OPENS[:4]
    frame = pl.DataFrame(
        {
            "ts": opens,
            "open": [100.0, 100.0, 100.0, 110.0],
            "high": [100.0, 100.0, 110.0, 121.0],
            "low": [100.0, 100.0, 100.0, 110.0],
            "close": [100.0, 100.0, 110.0, 121.0],
        }
    )
    values = pl.DataFrame({"ts": opens, "prediction": [0.0, 1.0, 1.0, 1.0]})
    out = tmp_path / "out"

    code = run(
        write(frame, tmp_path / "c.parquet"),
        write(values, tmp_path / "p.parquet"),
        out,
        "--fill",
        "signal_close",
    )

    assert code == 0
    assert capsys.readouterr().out == (
        f"Wrote trades.parquet, series.parquet, stats.json and run.json to {out}\n"
        "  trades        1\n"
        "  win rate      100.00%\n"
        "  total return  21.00%\n"
        "  max drawdown  0.00%\n"
        "  sharpe        n/a\n"
        "  buy and hold  21.00%\n"
    )
    text = (out / "stats.json").read_text(encoding="utf-8")
    assert '  "sharpe": null,\n  "sortino": null,\n' in text
    assert text.endswith("}\n")
    assert "\r" not in (out / "stats.json").read_bytes().decode()


def test_a_run_without_trades_has_no_win_rate(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"

    assert run(*plain, out, "--threshold", "1") == 0

    written = json.loads((out / "stats.json").read_text(encoding="utf-8"))
    assert written["num_trades"] == 0
    assert written["win_rate"] is None
    assert "  trades        0\n  win rate      n/a\n" in capsys.readouterr().out


def test_a_run_again_writes_the_same_bytes(plain: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "out"
    options = ["--stop-loss", "0.01", "--fee-bps", "3"]
    assert run(*plain, out, *options) == 0
    first = {name: (out / name).read_bytes() for name in FILES}

    assert run(*plain, out, *options) == 0

    assert {name: (out / name).read_bytes() for name in FILES} == first


# Errors: a message and exit code 2, no traceback and no files


def assert_refused(code: int, capsys: pytest.CaptureFixture[str], out: Path, message: str) -> None:
    assert code == 2
    captured = capsys.readouterr()
    assert captured.err == f"candlestack-bt run: error: {message}\n"
    assert captured.out == ""
    assert not out.exists()


def test_a_missing_candles_file(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nowhere.parquet"
    out = tmp_path / "out"

    code = run(missing, plain[1], out)

    assert_refused(code, capsys, out, f"No candles file at {missing}")


def test_a_missing_predictions_file(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"

    code = run(plain[0], tmp_path, out)  # a folder is no file

    assert_refused(code, capsys, out, f"No predictions file at {tmp_path}")


def test_a_file_that_is_not_parquet(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = tmp_path / "candles.csv"
    text.write_text("ts,open\n1,2\n", encoding="utf-8")
    out = tmp_path / "out"

    code = run(text, plain[1], out)

    assert code == 2
    assert capsys.readouterr().err.startswith(
        f"candlestack-bt run: error: Cannot read the candles file {text} as Parquet: "
    )
    assert not out.exists()


@pytest.mark.parametrize(
    ("drop", "options", "message"),
    [
        (["high", "low"], [], "The candles file {candles} has no column 'high', 'low'"),
        ([], ["--ts-column", "unix"], "The candles file {candles} has no column 'unix'"),
    ],
)
def test_missing_candle_columns_are_named(
    plain: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    drop: list[str],
    options: list[str],
    message: str,
) -> None:
    candles_path = write(candles(OPENS).drop(drop), tmp_path / "few.parquet")
    out = tmp_path / "out"

    code = run(candles_path, plain[1], out, *options)

    assert_refused(code, capsys, out, message.format(candles=candles_path))


def test_model_needs_a_model_column(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"

    code = run(*plain, out, "--model", "A")

    assert_refused(code, capsys, out, f"The predictions file {plain[1]} has no column 'model'")


def test_several_models_need_model(
    supervisor: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"

    code = run(*supervisor, out, "--ts-column", "unix")

    message = f"The predictions file {supervisor[1]} holds 2 models: pick one with --model"
    assert_refused(code, capsys, out, message)


def test_an_unknown_model(
    supervisor: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"

    code = run(*supervisor, out, "--ts-column", "unix", "--model", "C")

    assert_refused(code, capsys, out, "There are no predictions of model 'C' to align")


def test_a_ts_column_next_to_the_time_column(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    both = write(candles(OPENS).with_columns(unix=pl.col("ts")), tmp_path / "both.parquet")
    out = tmp_path / "out"

    code = run(both, plain[1], out, "--ts-column", "unix")

    message = f"The candles file {both} has a 'ts' column besides 'unix': drop or rename it"
    assert_refused(code, capsys, out, message)


def test_a_time_column_that_is_not_epoch_seconds(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dated = write(
        candles(OPENS).select(pl.from_epoch("ts").alias("date"), pl.exclude("ts")),
        tmp_path / "dated.parquet",
    )
    out = tmp_path / "out"

    code = run(dated, plain[1], out, "--ts-column", "date")

    message = (
        f"Column 'date' of the candles file {dated} must be integer epoch seconds, "
        "not Datetime(time_unit='us', time_zone=None)"
    )
    assert_refused(code, capsys, out, message)


def test_close_labels_need_a_timeframe(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        run(*plain, out, "--labels", "close")

    assert exit_info.value.code == 2
    assert capsys.readouterr().err.endswith(
        "candlestack-bt run: error: --labels close needs --timeframe, the length of a candle "
        "(30m, 1h, 1d)\n"
    )
    assert not out.exists()


def test_open_labels_refuse_a_timeframe(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Open labels are not moved, so a timeframe would be silently ignored.
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        run(*plain, out, "--timeframe", "30m")

    assert exit_info.value.code == 2
    assert capsys.readouterr().err.endswith(
        "candlestack-bt run: error: --timeframe goes only with --labels close: open labels are "
        "not moved\n"
    )
    assert not out.exists()


BETWEEN = "must be between 0 and 1 (0.05 is 5%), got"
LENGTH = "not a candle length like 30m, 1h or 1d:"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--threshold", "abc"], "argument --threshold: not a number: 'abc'"),
        (["--threshold", "-0.1"], "argument --threshold: must be 0 or more, got -0.1"),
        (["--threshold", "nan"], "argument --threshold: not a finite number: 'nan'"),
        (["--fee-bps", "-5"], "argument --fee-bps: must be 0 or more, got -5"),
        (["--slippage-bps", "inf"], "argument --slippage-bps: not a finite number: 'inf'"),
        (["--stop-loss", "0"], f"argument --stop-loss: {BETWEEN} 0"),
        (["--stop-loss", "5"], f"argument --stop-loss: {BETWEEN} 5"),
        (["--timeframe", "30"], f"argument --timeframe: {LENGTH} '30'"),
        (["--timeframe", "0m"], f"argument --timeframe: {LENGTH} '0m'"),
        (["--fill", "close"], "argument --fill: invalid choice: 'close'"),
        (["--labels", "end"], "argument --labels: invalid choice: 'end'"),
        (["--bogus"], "unrecognized arguments: --bogus"),
        (["--calibrate", "minmax"], "argument --calibrate: invalid choice: 'minmax'"),
        (["--calibration-window", "1"], "argument --calibration-window: must be 2 or more, got 1"),
        (["--ema", "0"], "argument --ema: must be 1 or more, got 0"),
        (["--ema", "2.5"], "argument --ema: not a whole number: '2.5'"),
        (["--rule", "sign"], "argument --rule: invalid choice: 'sign'"),
        (["--exit", "-0.1"], "argument --exit: must be 0 or more, got -0.1"),
        (["--k", "6"], "argument --k: invalid choice: '6'"),
        (["--kmeans-window", "1"], "argument --kmeans-window: must be 2 or more, got 1"),
        (["--refit-every", "0"], "argument --refit-every: must be 1 or more, got 0"),
        (["--confirm", "0"], "argument --confirm: must be 1 or more, got 0"),
        (["--min-hold", "x"], "argument --min-hold: not a whole number: 'x'"),
        (["--drawdown-stop", "1"], f"argument --drawdown-stop: {BETWEEN} 1"),
    ],
)
def test_bad_options_are_usage_errors(
    plain: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    message: str,
) -> None:
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        run(*plain, out, *options)

    assert exit_info.value.code == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


KMEANS = ["--rule", "kmeans", "--k", "2", "--kmeans-window", "4", "--refit-every", "1"]
TOGETHER = "--calibrate and --calibration-window go together"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--calibrate", "zscore"], TOGETHER),
        (["--calibration-window", "5"], TOGETHER),
        (["--exit", "0.1"], "--exit goes only with --rule hysteresis"),
        (["--k", "2"], "--k goes only with --rule kmeans"),
        (["--rule", "hysteresis", "--kmeans-window", "4"], "--kmeans-window goes only with"),
        (["--rule", "hysteresis"], "--rule hysteresis needs --exit"),
        (["--rule", "kmeans"], "--rule kmeans needs --k, --kmeans-window and --refit-every"),
        (
            ["--rule", "kmeans", "--k", "2", "--kmeans-window", "4"],
            "--rule kmeans needs --refit-every",
        ),
        (
            [*KMEANS, "--threshold", "0.1"],
            "--threshold goes only with --rule threshold or hysteresis",
        ),
        (
            ["--rule", "hysteresis", "--threshold", "0.05", "--exit", "0.1"],
            (
                "--exit must be at most --threshold, the limit a position opens at: got "
                "--exit 0.1 and --threshold 0.05"
            ),
        ),
        (
            ["--rule", "hysteresis", "--exit", "0.1"],
            (
                "--exit must be at most --threshold, the limit a position opens at: got "
                "--exit 0.1 and --threshold (default 0)"
            ),
        ),
        (
            ["--rule", "kmeans", "--k", "3", "--kmeans-window", "5", "--refit-every", "1"],
            "--kmeans-window must be at least 2 * --k (6), got 5",
        ),
    ],
)
def test_a_wrong_mix_of_post_processing_options_is_a_usage_error(
    plain: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    message: str,
) -> None:
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        run(*plain, out, *options)

    assert exit_info.value.code == 2
    assert f"candlestack-bt run: error: {message}" in capsys.readouterr().err
    assert not out.exists()


def test_the_engines_invalid_input_is_a_message(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = candles(OPENS).with_columns(
        low=pl.when(pl.int_range(pl.len()) == 2).then(0.0).otherwise(pl.col("low"))
    )
    out = tmp_path / "out"

    code = run(write(broken, tmp_path / "broken.parquet"), plain[1], out)

    message = "Invalid backtest input: 1 row has a price at or below zero, first at row 2."
    assert_refused(code, capsys, out, message)


def test_a_cut_parquet_file(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cut = tmp_path / "cut.parquet"
    cut.write_bytes(plain[0].read_bytes()[:200])
    out = tmp_path / "out"

    code = run(cut, plain[1], out)

    assert code == 2
    err = capsys.readouterr().err
    assert err.startswith(f"candlestack-bt run: error: Cannot read the candles file {cut} ")
    assert err.count("\n") == 1
    assert not out.exists()


TIMEFRAME = ["--labels", "close", "--timeframe", "30m"]


@pytest.mark.parametrize(
    ("side", "change", "options", "message"),
    [
        pytest.param(
            "candles",
            pl.col("open").cast(pl.String),
            [],
            "Invalid backtest input: open is String, not a number.",
            id="text prices",
        ),
        pytest.param(
            "predictions",
            pl.col("prediction").cast(pl.String),
            [],
            "Predictions must be numbers, not String",
            id="text predictions",
        ),
        pytest.param(
            "predictions",
            pl.lit(1).alias("model"),
            ["--model", "A"],
            "'model' of the predictions must be text, not Int32",
            id="number models",
        ),
        pytest.param(
            "predictions",
            pl.when(pl.col("ts") == OPENS[3])
            .then(float("nan"))
            .otherwise("prediction")
            .alias("prediction"),
            [],
            f"1 prediction is missing or NaN, first at ts {OPENS[3]}: each prediction must be a "
            "number",
            id="NaN prediction",
        ),
        pytest.param(
            "predictions",
            pl.when(pl.col("ts").is_in(OPENS[5:7]))
            .then(None)
            .otherwise("prediction")
            .alias("prediction"),
            [],
            f"2 predictions are missing or NaN, first at ts {OPENS[5]}: each prediction must be "
            "a number",
            id="null predictions",
        ),
        pytest.param(
            "candles",
            pl.col("ts").cast(pl.UInt64) + 2**63,
            TIMEFRAME,
            f"'ts' of the frame leaves the Int64 range, at {OPENS[-1] + 2**63}",
            id="ts beyond Int64 when relabelled",
        ),
        pytest.param(
            "candles",
            pl.lit(-(2**63), pl.Int64).alias("ts"),
            TIMEFRAME,
            "'ts' of the frame leaves the Int64 range once moved back by 1800 seconds, at "
            f"{-(2**63)}",
            id="ts below Int64 when relabelled",
        ),
    ],
)
def test_mistyped_or_bad_columns_are_a_message(
    plain: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    side: str,
    change: pl.Expr,
    options: list[str],
    message: str,
) -> None:
    paths = dict(zip(["candles", "predictions"], plain, strict=True))
    paths[side] = write(pl.read_parquet(paths[side]).with_columns(change), tmp_path / "bad.parquet")
    out = tmp_path / "out"

    code = run(paths["candles"], paths["predictions"], out, *options)

    assert_refused(code, capsys, out, message)


def test_a_polars_error_is_a_one_line_message(
    plain: tuple[Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Data no step checks for can still stop Polars; its message spans several lines.
    def broken(frame: pl.DataFrame, timeframe_seconds: int) -> pl.DataFrame:
        raise pl.exceptions.ComputeError("cannot relabel\n\nin this expression:\n\tcol('ts')")

    monkeypatch.setattr("candlestack.cli.run.relabel", broken)
    out = tmp_path / "out"

    code = run(*plain, out, *TIMEFRAME)

    assert_refused(code, capsys, out, "cannot relabel in this expression: col('ts')")


def test_predictions_without_candles_are_refused(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Close labels read as open labels: the last prediction has no candle.
    late = write(predictions([ts + HALF_HOUR for ts in OPENS], 1.5), tmp_path / "late.parquet")
    out = tmp_path / "out"

    code = run(plain[0], late, out)

    assert code == 2
    assert capsys.readouterr().err.startswith(
        "candlestack-bt run: error: 3 predictions have no candle"
    )
    assert not out.exists()


def test_an_out_that_is_a_file(
    plain: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "taken"
    out.write_text("", encoding="utf-8")

    assert run(*plain, out) == 2

    assert capsys.readouterr().err.startswith("candlestack-bt run: error: ")
    assert out.read_text(encoding="utf-8") == ""
