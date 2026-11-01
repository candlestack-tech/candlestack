"""``candlestack-bt run-all`` called in-process on a small data folder in ``tmp_path``."""

import hashlib
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

import candlestack.cli.run_all as run_all_module
from candlestack.cli import main
from candlestack.cli.run_all import _one_polars_thread
from candlestack.engine import ENGINE_VERSION, Result, Settings

HALF_HOUR = 1800
DAY = 86_400
FILES = ["stats.parquet", "trades.parquet", "run.json"]
KEYS = ["stock", "model", "threshold", "stop_loss"]
SETTINGS = ["long_only", "fill", "fee_bps", "slippage_bps"]
LABELS = ["--ts-column", "unix", "--labels", "close", "--timeframe", "30m"]
NO_GAP = {"predictions_without_candle": 0, "candles_without_prediction": 0}


def utc(text: str) -> int:
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp())


# Three days of eight 30m candles from 14:30 UTC: enough days for a Sharpe ratio.
OPENS = [utc("2026-01-05 14:30") + day * DAY + k * HALF_HOUR for day in range(3) for k in range(8)]


def candles(shift: float) -> pl.DataFrame:
    """Candles in the supervisor's terms (``unix`` is the close, Int32) that rise and fall;
    each opens at the close before it."""
    closes = [100 + 4 * math.sin(i / 2 + shift) + 0.3 * i for i in range(len(OPENS))]
    starts = [100.0, *closes[:-1]]
    return pl.DataFrame(
        {
            "unix": [ts + HALF_HOUR for ts in OPENS],
            "open": starts,
            "high": [max(pair) + 0.5 for pair in zip(starts, closes, strict=True)],
            "low": [min(pair) - 0.5 for pair in zip(starts, closes, strict=True)],
            "close": closes,
        },
        schema_overrides={"unix": pl.Int32},
    )


def predictions(waves: dict[str, float]) -> pl.DataFrame:
    """One prediction per candle of each model, labelled by the close."""
    return pl.concat(
        pl.DataFrame(
            {
                "unix": [ts + HALF_HOUR for ts in OPENS],
                "model": model,
                "prediction": [0.2 * math.cos(i / wave) for i in range(len(OPENS))],
            },
            schema={"unix": pl.Int64, "model": pl.String, "prediction": pl.Float32},
        )
        for model, wave in waves.items()
    )


def stock(data: Path, name: str, shift: float, waves: dict[str, float]) -> Path:
    folder = data / name
    folder.mkdir(parents=True)
    candles(shift).write_parquet(folder / "candles.parquet")
    predictions(waves).write_parquet(folder / "predictions.parquet")
    return folder


@pytest.fixture
def data(tmp_path: Path) -> Path:
    """Two stocks with the models A and B, and a folder that is no stock."""
    folder = tmp_path / "data"
    stock(folder, "MSFT", 1.0, {"B": 0.7, "A": 1.5})
    stock(folder, "AAPL", 0.0, {"A": 1.5, "B": 0.7})
    (folder / "_raw").mkdir()
    return folder


def run_all(data: Path, out: Path, *options: str) -> int:
    return main(["run-all", "--data", str(data), "--out", str(out), *options])


def run(folder: Path, out: Path, *options: str) -> int:
    return main(
        [
            "run",
            "--candles",
            str(folder / "candles.parquet"),
            "--predictions",
            str(folder / "predictions.parquet"),
            "--out",
            str(out),
            *options,
        ]
    )


Keys = tuple[str, str, float | None, float | None]


def assert_run_gives(data: Path, out: Path, keys: Keys, options: list[str]) -> None:
    """Runs ``run`` on one combination of ``run-all`` into ``out`` (a limit of None is the
    kmeans rule, which has none)."""
    name, model, limit, stop = keys
    limit_option = [] if limit is None else ["--threshold", str(limit)]
    stop_option = [] if stop is None else ["--stop-loss", str(stop)]
    assert run(
        data / name, out, *LABELS, "--model", model, *limit_option, *stop_option, *options
    ) == 0  # fmt: skip


def combination(keys: Keys) -> pl.Expr:
    """Selects the rows of one combination."""
    name, model, limit, stop = keys
    selected = (pl.col("stock") == name) & (pl.col("model") == model)
    selected &= pl.col("threshold").is_null() if limit is None else pl.col("threshold") == limit
    return selected & (
        pl.col("stop_loss").is_null() if stop is None else pl.col("stop_loss") == stop
    )


def assert_combinations_give_runs(
    data: Path, out: Path, tmp_path: Path, combinations: list[Keys], options: list[str]
) -> None:
    """Each combination's row of ``stats.parquet`` and rows of ``trades.parquet`` are what
    ``run`` writes with the same options."""
    stats_frame = pl.read_parquet(out / "stats.parquet")
    trades = pl.read_parquet(out / "trades.parquet")
    assert stats_frame.select(KEYS).rows() == combinations
    for index, keys in enumerate(combinations):
        single = tmp_path / f"run{index}"
        assert_run_gives(data, single, keys, options)
        row = stats_frame.row(index, named=True)
        written = json.loads((single / "stats.json").read_text(encoding="utf-8"))
        assert {name: row[name] for name in written} == written
        assert_frame_equal(
            trades.filter(combination(keys)).drop(KEYS),
            pl.read_parquet(single / "trades.parquet"),
            check_exact=True,
        )


# The combinations and run


def test_each_combination_gives_the_trades_and_stats_of_run(data: Path, tmp_path: Path) -> None:
    signals = ["--long-only"]
    shared = ["--fill", "signal_close", "--fee-bps", "5", "--slippage-bps", "1"]
    out = tmp_path / "all"

    code = run_all(
        data, out, *LABELS, *signals, *shared, "--thresholds", "0,0.05", "--stop-losses",
        "none,0.02",
    )  # fmt: skip

    assert code == 0
    stats_frame = pl.read_parquet(out / "stats.parquet")
    trades = pl.read_parquet(out / "trades.parquet")
    combinations = [
        (name, model, limit, stop)
        for name in ["AAPL", "MSFT"]
        for model in ["A", "B"]
        for limit in [0.0, 0.05]
        for stop in [None, 0.02]
    ]
    assert stats_frame.select(KEYS).rows() == combinations
    assert trades.select(KEYS).unique(maintain_order=True).rows() == [
        keys for keys in combinations if keys in trades.select(KEYS).rows()
    ]
    for index, keys in enumerate(combinations):
        single = tmp_path / f"run{index}"
        assert_run_gives(data, single, keys, [*signals, *shared])
        row = stats_frame.row(index, named=True)
        written = json.loads((single / "stats.json").read_text(encoding="utf-8"))
        assert {name: row[name] for name in written} == written
        assert list(row) == [*KEYS, *SETTINGS, *written]
        assert [row[name] for name in SETTINGS] == [True, "signal_close", 5.0, 1.0]
        assert_frame_equal(
            trades.filter(combination(keys)).drop(KEYS),
            pl.read_parquet(single / "trades.parquet"),
            check_exact=True,
        )
    assert trades.height >= 20


def test_the_position_size_reaches_every_combination_as_in_run(data: Path, tmp_path: Path) -> None:
    size = ["--position-size", "0.5"]
    out = tmp_path / "all"

    code = run_all(data, out, *LABELS, *size, "--thresholds", "0,0.3")

    assert code == 0
    combinations: list[Keys] = [
        (name, model, limit, None)
        for name in ["AAPL", "MSFT"]
        for model in ["A", "B"]
        for limit in [0.0, 0.3]
    ]
    assert_combinations_give_runs(data, out, tmp_path, combinations, size)
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["position_size"] == 0.5


def test_the_post_processing_and_the_drawdown_stop_reach_every_combination_as_in_run(
    data: Path, tmp_path: Path
) -> None:
    steps = ["--calibrate", "rank", "--calibration-window", "4", "--ema", "2", "--long-only"]
    held = ["--confirm", "2", "--min-hold", "2", "--drawdown-stop", "0.03"]
    out = tmp_path / "all"

    code = run_all(data, out, *LABELS, *steps, *held, "--thresholds", "0,0.3", "--stop-losses",
                   "none,0.02")  # fmt: skip

    assert code == 0
    combinations: list[Keys] = [
        (name, model, limit, stop)
        for name in ["AAPL", "MSFT"]
        for model in ["A", "B"]
        for limit in [0.0, 0.3]
        for stop in [None, 0.02]
    ]
    assert_combinations_give_runs(data, out, tmp_path, combinations, [*steps, *held])
    trades = pl.read_parquet(out / "trades.parquet")
    assert trades.height >= 8
    assert "drawdown" in trades["exit_reason"].to_list()
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["drawdown_stop"] == 0.03
    assert record["post_processing"] == {
        "calibrate": {"name": "rank", "window": 4},
        "ema": {"span": 2},
        "rule": {"name": "threshold", "limit": [0.0, 0.3]},
        "long_only": True,
        "confirm": {"candles": 2},
        "min_hold": {"candles": 2},
    }


def test_hysteresis_takes_the_thresholds_as_its_entry_limits(data: Path, tmp_path: Path) -> None:
    rule = ["--rule", "hysteresis", "--exit", "0.02"]
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, *rule, "--thresholds", "0.05,0.1") == 0

    combinations: list[Keys] = [
        (name, model, limit, None)
        for name in ["AAPL", "MSFT"]
        for model in ["A", "B"]
        for limit in [0.05, 0.1]
    ]
    assert_combinations_give_runs(data, out, tmp_path, combinations, rule)
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["thresholds"] == [0.05, 0.1]
    assert record["post_processing"]["rule"] == {
        "name": "hysteresis",
        "enter": [0.05, 0.1],
        "exit": 0.02,
    }


def test_kmeans_has_no_threshold(data: Path, tmp_path: Path) -> None:
    rule = ["--rule", "kmeans", "--k", "2", "--kmeans-window", "4", "--refit-every", "2"]
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, *rule) == 0

    combinations: list[Keys] = [
        (name, model, None, None) for name in ["AAPL", "MSFT"] for model in ["A", "B"]
    ]
    assert_combinations_give_runs(data, out, tmp_path, combinations, rule)
    assert pl.read_parquet(out / "stats.parquet").schema["threshold"] == pl.Float64
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["thresholds"] == [None]
    assert record["post_processing"]["rule"] == {
        "name": "kmeans",
        "k": 2,
        "window": 4,
        "refit_every": 2,
    }


def test_slippage_cost_and_ruin_reach_the_trades_as_in_run(data: Path, tmp_path: Path) -> None:
    folder = stock(data, "RUIN", 0.0, {"A": 1.5})
    # Short from the second candle on (the first is no decision) while the price triples.
    opens = pl.Series([100.0 + 10 * i for i in range(len(OPENS))])
    candles(0.0).with_columns(
        open=opens, high=opens + 10, low=opens, close=opens + 10
    ).write_parquet(folder / "candles.parquet")
    signs = pl.Series([1.0] + [-1.0] * (len(OPENS) - 1), dtype=pl.Float32)
    predictions({"A": 1.5}).with_columns(prediction=0.2 * signs).write_parquet(
        folder / "predictions.parquet"
    )
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, "--slippage-bps", "10") == 0

    assert run(folder, tmp_path / "one", *LABELS, "--model", "A", "--slippage-bps", "10") == 0
    trades = pl.read_parquet(out / "trades.parquet")
    ruined = trades.filter(pl.col("stock") == "RUIN").drop(KEYS)
    assert_frame_equal(ruined, pl.read_parquet(tmp_path / "one" / "trades.parquet"))
    assert ruined["exit_reason"].to_list() == ["ruin"]
    assert (trades["slippage_cost"] > 0).all()


def test_the_defaults_are_runs_defaults(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS) == 0

    assert run(data / "AAPL", tmp_path / "one", *LABELS, "--model", "B") == 0
    row = pl.read_parquet(out / "stats.parquet").row(1, named=True)
    assert row["stock"] == "AAPL"
    assert row["model"] == "B"
    assert row["threshold"] == 0.0
    assert row["stop_loss"] is None
    assert [row[name] for name in SETTINGS] == [False, "next_open", 0.0, 0.0]
    written = json.loads((tmp_path / "one" / "stats.json").read_text(encoding="utf-8"))
    assert {name: row[name] for name in written} == written


def test_models_picks_the_models_in_its_order(data: Path, tmp_path: Path) -> None:
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, "--models", "B,A") == 0

    rows = pl.read_parquet(out / "stats.parquet").select("stock", "model").rows()
    assert rows == [("AAPL", "B"), ("AAPL", "A"), ("MSFT", "B"), ("MSFT", "A")]


def test_the_output_does_not_depend_on_jobs(data: Path, tmp_path: Path) -> None:
    options = [*LABELS, "--thresholds", "0,0.1", "--stop-losses", "0.01,none", "--ema", "2"]
    assert run_all(data, tmp_path / "one", *options) == 0

    assert run_all(data, tmp_path / "two", *options, "--jobs", "2") == 0

    for name in FILES:
        assert (tmp_path / "two" / name).read_bytes() == (tmp_path / "one" / name).read_bytes()


def test_the_workers_get_one_polars_thread_unless_told_otherwise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("POLARS_MAX_THREADS", raising=False)
    with _one_polars_thread():
        assert os.environ["POLARS_MAX_THREADS"] == "1"
    assert "POLARS_MAX_THREADS" not in os.environ

    monkeypatch.setenv("POLARS_MAX_THREADS", "3")
    with _one_polars_thread():
        assert os.environ["POLARS_MAX_THREADS"] == "3"
    assert os.environ["POLARS_MAX_THREADS"] == "3"


# What the run writes and prints


def test_run_json_holds_the_settings_models_hashes_and_align_reports(
    data: Path, tmp_path: Path
) -> None:
    out = tmp_path / "all"
    options = ["--thresholds", "0.1,0", "--stop-losses", "0.05,none", "--fee-bps", "2"]

    assert run_all(data, out, *LABELS, *options) == 0

    def inputs(name: str) -> dict[str, dict[str, str]]:
        return {
            kind: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for kind in ["candles", "predictions"]
            for path in [data / name / f"{kind}.parquet"]
        }

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record == {
        "engine_version": ENGINE_VERSION,
        "data": str(data),
        "settings": {
            "ts_column": "unix",
            "labels": "close",
            "timeframe_seconds": HALF_HOUR,
            "allow_gaps": False,
            "models": None,
            "thresholds": [0.1, 0.0],
            "long_only": False,
            "fill": "next_open",
            "stop_losses": [0.05, None],
            "drawdown_stop": None,
            "position_size": 1.0,
            "fee_bps": 2.0,
            "slippage_bps": 0.0,
        },
        "post_processing": {
            "calibrate": None,
            "ema": None,
            "rule": {"name": "threshold", "limit": [0.1, 0.0]},
            "long_only": False,
            "confirm": None,
            "min_hold": None,
        },
        "models": ["A", "B"],
        "stocks": [
            {"stock": name, "inputs": inputs(name), "align": {"A": NO_GAP, "B": NO_GAP}}
            for name in ["AAPL", "MSFT"]
        ],
        "failed": [],
    }
    assert "\r" not in (out / "run.json").read_bytes().decode()


def test_the_summary(data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, "--thresholds", "0,1") == 0

    trades = pl.read_parquet(out / "trades.parquet").height
    captured = capsys.readouterr()
    assert captured.out == (
        f"Wrote stats.parquet, trades.parquet and run.json to {out}\n"
        "  stocks  2 ran, 0 failed\n"
        "  models  2 ran, 0 failed on a stock\n"
        "  runs    8\n"
        f"  trades  {trades}\n"
    )
    assert captured.err == ""


# Stocks that fail: reported, and the others still run


def gappy_stock(data: Path) -> Path:
    """A stock whose model A lacks the prediction of the fourth candle; B has them all."""
    folder = stock(data, "GAPPY", 0.5, {"A": 1.5, "B": 0.7})
    path = folder / "predictions.parquet"
    gap = (pl.col("model") == "A") & (pl.col("unix") == OPENS[3] + HALF_HOUR)
    pl.read_parquet(path).filter(~gap).write_parquet(path)
    return folder


def test_a_failing_stock_or_model_is_reported_and_the_others_run(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (data / "EMPTY").mkdir()
    gappy_stock(data)
    out = tmp_path / "all"

    code = run_all(data, out, *LABELS)

    assert code == 1
    missing = data / "EMPTY" / "candles.parquet"
    gap = (
        f"1 candle has no prediction, first at ts {OPENS[3]}: pass --allow-gaps to repeat the "
        "previous prediction"
    )
    captured = capsys.readouterr()
    err = captured.err.splitlines()
    assert err[0] == f"candlestack-bt run-all: error: EMPTY: No candles file at {missing}"
    assert err[1].startswith(f"candlestack-bt run-all: error: GAPPY 'A': {gap}")
    assert len(err) == 2
    assert "  stocks  3 ran, 1 failed\n  models  2 ran, 1 failed on a stock\n" in captured.out
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert [entry["stock"] for entry in record["stocks"]] == ["AAPL", "GAPPY", "MSFT"]
    assert record["stocks"][1]["align"] == {"B": NO_GAP}
    assert record["failed"][0] == {
        "stock": "EMPTY",
        "model": None,
        "error": f"No candles file at {missing}",
    }
    assert record["failed"][1]["stock"] == "GAPPY"
    assert record["failed"][1]["model"] == "A"
    assert record["failed"][1]["error"].startswith(gap)
    assert len(record["failed"]) == 2
    rows = pl.read_parquet(out / "stats.parquet").select("stock", "model").rows()
    assert rows == [("AAPL", "A"), ("AAPL", "B"), ("GAPPY", "B"), ("MSFT", "A"), ("MSFT", "B")]


def test_allow_gaps_runs_the_gaps_as_run_does(data: Path, tmp_path: Path) -> None:
    gappy = gappy_stock(data)
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, "--allow-gaps") == 0

    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["settings"]["allow_gaps"] is True
    assert record["stocks"][1]["align"] == {
        "A": {"predictions_without_candle": 0, "candles_without_prediction": 1},
        "B": NO_GAP,
    }
    assert run(gappy, tmp_path / "one", *LABELS, "--model", "A", "--allow-gaps") == 0
    stats_frame = pl.read_parquet(out / "stats.parquet")
    row = stats_frame.filter(combination(("GAPPY", "A", 0.0, None))).row(0, named=True)
    written = json.loads((tmp_path / "one" / "stats.json").read_text(encoding="utf-8"))
    assert {name: row[name] for name in written} == written


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            lambda frame: frame.drop("model"),
            "The predictions file {path} has no column 'model'",
        ),
        (
            lambda frame: frame.with_columns(pl.lit(1).alias("model")),
            "Column 'model' of the predictions file {path} must be text",
        ),
        (
            lambda frame: frame.with_columns(pl.lit(None, pl.String).alias("model")),
            "The predictions file {path} names no model: column 'model' is empty",
        ),
        (
            lambda frame: frame.clear(),
            "The predictions file {path} names no model: column 'model' is empty",
        ),
    ],
)
def test_bad_predictions_fail_their_stock(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], change, message: str
) -> None:
    path = data / "MSFT" / "predictions.parquet"
    change(pl.read_parquet(path)).write_parquet(path)
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS) == 1

    error = message.format(path=path)
    assert capsys.readouterr().err == f"candlestack-bt run-all: error: MSFT: {error}\n"
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["failed"] == [{"stock": "MSFT", "model": None, "error": error}]
    assert pl.read_parquet(out / "stats.parquet")["stock"].unique().to_list() == ["AAPL"]


def test_a_model_a_stock_lacks_fails_only_that_model(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = data / "MSFT" / "predictions.parquet"
    pl.read_parquet(path).filter(pl.col("model") == "A").write_parquet(path)
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, "--models", "A,B") == 1

    error = f"The predictions file {path} has no model 'B'"
    assert capsys.readouterr().err == f"candlestack-bt run-all: error: MSFT 'B': {error}\n"
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["failed"] == [{"stock": "MSFT", "model": "B", "error": error}]
    rows = pl.read_parquet(out / "stats.parquet").select("stock", "model").rows()
    assert rows == [("AAPL", "A"), ("AAPL", "B"), ("MSFT", "A")]


def test_a_polars_error_fails_only_its_model(
    data: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = run_all_module.backtest
    calls: list[None] = []

    def backtest(frame: pl.DataFrame, settings: Settings) -> Result:
        calls.append(None)
        if len(calls) == 4:  # MSFT's model B: one backtest per stock and model here
            raise pl.exceptions.ComputeError("bad\ndata")
        return real(frame, settings)

    monkeypatch.setattr(run_all_module, "backtest", backtest)
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS) == 1

    assert capsys.readouterr().err == "candlestack-bt run-all: error: MSFT 'B': bad data\n"
    rows = pl.read_parquet(out / "stats.parquet").select("stock", "model").rows()
    assert rows == [("AAPL", "A"), ("AAPL", "B"), ("MSFT", "A")]


def test_a_polars_error_on_the_files_fails_only_its_stock(
    data: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = run_all_module.relabel
    calls: list[None] = []

    def relabel(frame: pl.DataFrame, seconds: int) -> pl.DataFrame:
        calls.append(None)
        if len(calls) == 3:  # MSFT's candles: the candles, then the predictions of each stock
            raise pl.exceptions.SchemaError("bad schema")
        return real(frame, seconds)

    monkeypatch.setattr(run_all_module, "relabel", relabel)
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS) == 1

    assert capsys.readouterr().err == "candlestack-bt run-all: error: MSFT: bad schema\n"
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["failed"] == [{"stock": "MSFT", "model": None, "error": "bad schema"}]
    assert [entry["stock"] for entry in record["stocks"]] == ["AAPL"]


def test_a_stock_whose_models_all_fail_has_no_rows(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "all"

    assert run_all(data, out, *LABELS, "--models", "C") == 1

    err = capsys.readouterr().err.splitlines()
    assert [line.split(": ")[2] for line in err] == ["AAPL 'C'", "MSFT 'C'"]
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert [entry["align"] for entry in record["stocks"]] == [{}, {}]
    assert record["models"] == []
    assert pl.read_parquet(out / "stats.parquet").columns == KEYS


def test_when_every_stock_fails_the_files_hold_the_keys_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = tmp_path / "data"
    (data / "AAPL").mkdir(parents=True)
    out = tmp_path / "all"

    assert run_all(data, out) == 1

    assert "AAPL: No candles file" in capsys.readouterr().err
    for name in ["stats.parquet", "trades.parquet"]:
        frame = pl.read_parquet(out / name)
        assert frame.is_empty()
        assert frame.columns == KEYS
    record = json.loads((out / "run.json").read_text(encoding="utf-8"))
    assert record["models"] == []
    assert record["stocks"] == []


# Usage errors: exit code 2, no files


def assert_usage_error(
    code: int | str | None, capsys: pytest.CaptureFixture[str], message: str
) -> None:
    assert code == 2
    assert message in capsys.readouterr().err


KMEANS = ["--k", "2", "--kmeans-window", "4", "--refit-every", "1"]


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (["--thresholds", "0,x"], "argument --thresholds: not a number: 'x'"),
        (["--thresholds", "0.1,0.1"], "argument --thresholds: a value repeats: '0.1,0.1'"),
        (["--thresholds", "-1"], "argument --thresholds: must be 0 or more, got -1"),
        (["--stop-losses", "none,1"], "argument --stop-losses: must be between 0 and 1"),
        (["--stop-losses", "none,none"], "argument --stop-losses: a value repeats"),
        (["--models", "A,,B"], "argument --models: a model name is empty"),
        (["--jobs", "0"], "argument --jobs: must be 1 or more, got 0"),
        (["--jobs", "two"], "argument --jobs: not a whole number: 'two'"),
        (["--fee-bps", "-1"], "argument --fee-bps: must be 0 or more, got -1"),
        (["--labels", "close"], "error: --labels close needs --timeframe"),
        (["--timeframe", "30m"], "error: --timeframe goes only with --labels close"),
        (["--calibrate", "zscore"], "error: --calibrate and --calibration-window go together"),
        (["--ema", "0"], "argument --ema: must be 1 or more, got 0"),
        (["--drawdown-stop", "0"], "argument --drawdown-stop: must be between 0 and 1"),
        (["--rule", "hysteresis"], "error: --rule hysteresis needs --exit"),
        (
            ["--rule", "hysteresis", "--thresholds", "0.1,0.05", "--exit", "0.07"],
            (
                "error: --exit must be at most --thresholds, the limit a position opens at: "
                "got --exit 0.07 and --thresholds 0.05"
            ),
        ),
        (
            ["--rule", "kmeans", "--thresholds", "0", *KMEANS],
            "error: --thresholds goes only with --rule threshold or hysteresis",
        ),
    ],
)
def test_bad_options_are_usage_errors(
    data: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    message: str,
) -> None:
    out = tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        run_all(data, out, *options)

    assert_usage_error(exit_info.value.code, capsys, message)
    assert not out.exists()


def test_a_missing_data_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_all(tmp_path / "nowhere", tmp_path / "out")

    message = f"candlestack-bt run-all: error: --data: no folder at {tmp_path / 'nowhere'}"
    assert_usage_error(exit_info.value.code, capsys, message)


def test_a_data_folder_without_stocks(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".cache").mkdir()
    (tmp_path / "notes.txt").write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        run_all(tmp_path, tmp_path / "out")

    message = f"candlestack-bt run-all: error: --data: no stock folder in {tmp_path}"
    assert_usage_error(exit_info.value.code, capsys, message)


def test_an_out_that_is_a_file(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "taken"
    out.write_text("", encoding="utf-8")

    assert run_all(data, out, *LABELS) == 2

    assert capsys.readouterr().err.startswith("candlestack-bt run-all: error: ")
    assert out.read_text(encoding="utf-8") == ""
