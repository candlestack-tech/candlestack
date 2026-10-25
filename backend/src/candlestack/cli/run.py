"""``candlestack-bt run``: one backtest of a model's predictions on the candles of one stock.

The steps are the public functions of the backtest modules, so a run from code gives the same
numbers: read both Parquet files, name their time column ``ts``, ``relabel`` both when they are
labelled by the candle's close, ``align`` (strict unless ``--allow-gaps``), the post-processing
of the predictions into the signal (``make_signal``: calibration, ``ema``, one rule, ``long_only``,
``confirm``, ``min_hold``, in that order, each off unless asked for), ``backtest``, ``stats``.
The run writes four files to ``--out``:

- ``trades.parquet``: the engine's trades;
- ``series.parquet``: the engine's series with its ``drawdown``;
- ``stats.json``: the statistics, None as null;
- ``run.json``: the settings, the post-processing, the engine version, the SHA-256 of both
  input files and the align report;

and prints a short summary of the statistics.

``check_time_labels``, ``read_input`` and ``aligned`` are public in this module so that other
commands read and align files the same way; the post-processing is ``cli/post_processing.py``.
"""

import argparse
import dataclasses
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import polars as pl

from candlestack.cli.options import _fraction, _non_negative
from candlestack.cli.post_processing import (
    add_drawdown_stop,
    add_post_processing,
    check_post_processing,
    post_processing,
    rule_limit,
)
from candlestack.engine import Result, Settings, backtest
from candlestack.metrics import drawdown, stats
from candlestack.signals import AlignReport, PostProcessing, align, describe, make_signal, relabel

Stats = dict[str, float | int | None]

_PRICES = ["open", "high", "low", "close"]
_TIMEFRAME = re.compile(r"([1-9][0-9]*)([mhd])")
_UNIT_SECONDS = {"m": 60, "h": 3_600, "d": 86_400}


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """Adds the options of ``run`` to its parser, in groups for its help."""
    _add_files(parser)
    _add_time_labels(parser)
    _add_signals(parser)
    add_post_processing(parser, grid=False)
    _add_engine(parser)


def _add_files(parser: argparse.ArgumentParser) -> None:
    files = parser.add_argument_group("files")
    files.add_argument(
        "--candles",
        type=Path,
        required=True,
        metavar="PATH",
        help="Parquet file of the candles: the time column, open, high, low and close",
    )
    files.add_argument(
        "--predictions",
        type=Path,
        required=True,
        metavar="PATH",
        help="Parquet file of the predictions: the time column, prediction (and model)",
    )
    files.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="DIR",
        help="folder the four result files go to, created if needed",
    )


def _add_time_labels(parser: argparse.ArgumentParser) -> None:
    labels = parser.add_argument_group("time labels")
    labels.add_argument(
        "--ts-column",
        default="ts",
        metavar="NAME",
        help="the time column of both files, integer UTC epoch seconds (default: ts; the "
        "supervisor's files: unix)",
    )
    labels.add_argument(
        "--labels",
        choices=["open", "close"],
        default="open",
        help="whether the time is a candle's open or its close (default: open); close "
        "moves the times of both files back by --timeframe",
    )
    labels.add_argument(
        "--timeframe",
        type=_timeframe,
        metavar="LENGTH",
        help="the length of a candle, like 30m, 1h or 1d; needed with --labels close, refused "
        "with --labels open",
    )


def _add_signals(parser: argparse.ArgumentParser) -> None:
    signals = parser.add_argument_group("signals")
    signals.add_argument(
        "--model", metavar="NAME", help="backtest only this model's predictions (column model)"
    )
    signals.add_argument(
        "--allow-gaps",
        action="store_true",
        help="drop predictions without a candle and let candles without a prediction repeat "
        "the previous one, instead of refusing both; run.json counts them",
    )


def _add_engine(parser: argparse.ArgumentParser) -> None:
    engine = parser.add_argument_group("engine")
    engine.add_argument(
        "--fill",
        choices=["next_open", "signal_close"],
        default="next_open",
        help="fill a decision at the next candle's open or at the close of its own candle "
        "(default: next_open)",
    )
    engine.add_argument(
        "--stop-loss",
        type=_fraction,
        metavar="FRACTION",
        help="close a position once it loses this fraction of its entry price, 0.05 for 5%% "
        "(default: no stop)",
    )
    add_drawdown_stop(engine)
    engine.add_argument(
        "--fee-bps",
        type=_non_negative,
        default=0.0,
        metavar="BPS",
        help="the fee of each leg, in basis points of its notional (default: 0)",
    )
    engine.add_argument(
        "--slippage-bps",
        type=_non_negative,
        default=0.0,
        metavar="BPS",
        help="how far each fill lands from its price, against the trade (default: 0)",
    )


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Runs one backtest with the options in ``args`` and returns the exit code.

    A wrong option stops at ``parser.error`` (exit code 2). A missing or unreadable file, a
    missing or mistyped column or data the steps refuse prints the reason on one line and
    returns 2, without a traceback and without writing any file.
    """
    check_time_labels(args, parser)
    check_post_processing(args, parser, grid=False)
    try:
        values = _run(args)
    # The engine's InvalidInput is a ValueError; a Polars error is data no step checks for.
    except (OSError, ValueError, pl.exceptions.PolarsError) as error:
        reason = " ".join(str(error).split())
        sys.stderr.write(f"{parser.prog}: error: {reason}\n")
        return 2
    sys.stdout.write(_summary(values, args.out))
    return 0


def _run(args: argparse.Namespace) -> Stats:
    settings = Settings(
        fill=args.fill,
        stop_loss=args.stop_loss,
        drawdown_stop=args.drawdown_stop,
        fee_bps=args.fee_bps,
        slippage_bps=args.slippage_bps,
    )
    post = post_processing(args, threshold=rule_limit(args.rule, args.threshold))
    candles = read_input(args.candles, "candles", args.ts_column, _PRICES)
    model = [] if args.model is None else ["model"]
    predictions = read_input(
        args.predictions, "predictions", args.ts_column, ["prediction", *model]
    )
    frame, report = aligned(
        candles,
        predictions,
        timeframe=args.timeframe,
        model=args.model,
        allow_gaps=args.allow_gaps,
        source=args.predictions,
    )
    result = backtest(frame.with_columns(make_signal(frame["prediction"], post)), settings)
    values = stats(result.trades, result.series, frame)
    _write(args.out, result, values, _record(args, settings, result, report, post=post))
    return values


def check_time_labels(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Stops at ``parser.error`` unless ``--timeframe`` comes exactly with ``--labels close``."""
    if args.labels == "close" and args.timeframe is None:
        parser.error("--labels close needs --timeframe, the length of a candle (30m, 1h, 1d)")
    if args.labels == "open" and args.timeframe is not None:
        parser.error("--timeframe goes only with --labels close: open labels are not moved")


def aligned(
    candles: pl.DataFrame,
    predictions: pl.DataFrame,
    *,
    timeframe: int | None,
    model: str | None,
    allow_gaps: bool,
    source: Path,
) -> tuple[pl.DataFrame, AlignReport]:
    """The candles from the model's first to its last prediction, with ``prediction``.

    Both frames have ``ts`` (see ``read_input``). A ``timeframe`` in seconds means both are
    labelled by the candle's close: ``relabel`` moves them back by it first. ``allow_gaps``
    aligns with ``strict=False``. ``source`` is the predictions file, for the messages.

    Raises:
        ValueError: the file holds several models and ``model`` is None, or ``relabel`` or
            ``align`` refuses the data; a refused gap points to ``--allow-gaps``.
    """
    models = predictions["model"].n_unique() if "model" in predictions.columns else 1
    if model is None and models > 1:
        raise ValueError(
            f"The predictions file {source} holds {models} models: pick one with --model"
        )
    if timeframe is not None:
        candles = relabel(candles, timeframe)
        predictions = relabel(predictions, timeframe)
    try:
        return align(candles, predictions, model=model, strict=not allow_gaps)
    except ValueError as error:
        raise ValueError(str(error).replace("pass strict=False", "pass --allow-gaps")) from None


def read_input(path: Path, what: str, ts_column: str, columns: list[str]) -> pl.DataFrame:
    """The Parquet file at ``path`` with its time column ``ts_column`` renamed to ``ts``.

    ``what`` names the file in the messages (``candles`` or ``predictions``).

    Raises:
        ValueError: there is no such file, it cannot be read as Parquet, a column is missing or
            the time column is not integer epoch seconds.
    """
    if not path.is_file():
        raise ValueError(f"No {what} file at {path}")
    try:
        frame = pl.read_parquet(path)
    except (OSError, pl.exceptions.PolarsError) as error:
        raise ValueError(f"Cannot read the {what} file {path} as Parquet: {error}") from error
    missing = [name for name in [ts_column, *columns] if name not in frame.columns]
    if missing:
        names = ", ".join(map(repr, missing))
        raise ValueError(f"The {what} file {path} has no column {names}")
    if ts_column != "ts" and "ts" in frame.columns:
        raise ValueError(
            f"The {what} file {path} has a 'ts' column besides {ts_column!r}: drop or rename it"
        )
    dtype = frame.schema[ts_column]
    if not dtype.is_integer():
        raise ValueError(
            f"Column {ts_column!r} of the {what} file {path} must be integer epoch seconds, "
            f"not {dtype}"
        )
    return frame.rename({ts_column: "ts"})


def _record(
    args: argparse.Namespace,
    settings: Settings,
    result: Result,
    report: AlignReport,
    *,
    post: PostProcessing,
) -> dict[str, Any]:
    """What ``run.json`` holds: enough to repeat the run and to tell whether its inputs
    changed."""
    return {
        "engine_version": result.engine_version,
        "inputs": {
            "candles": {"path": str(args.candles), "sha256": _sha256(args.candles)},
            "predictions": {"path": str(args.predictions), "sha256": _sha256(args.predictions)},
        },
        "settings": {
            "ts_column": args.ts_column,
            "labels": args.labels,
            "timeframe_seconds": args.timeframe,
            "model": args.model,
            "allow_gaps": args.allow_gaps,
            "threshold": post.threshold,
            "long_only": post.long_only,
            **settings.model_dump(),
        },
        "post_processing": describe(post),
        "align": dataclasses.asdict(report),
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def _write(out: Path, result: Result, values: Stats, record: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    result.trades.write_parquet(out / "trades.parquet")
    result.series.with_columns(drawdown(result.series)).write_parquet(out / "series.parquet")
    _write_json(out / "stats.json", values)
    _write_json(out / "run.json", record)


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """``data`` as indented JSON in its own key order, with LF line ends on every OS."""
    text = json.dumps(data, indent=2, allow_nan=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _summary(values: Stats, out: Path) -> str:
    rows = [
        ("trades", values["num_trades"], "d"),
        ("win rate", values["win_rate"], ".2%"),
        ("total return", values["total_return"], ".2%"),
        ("max drawdown", values["max_drawdown"], ".2%"),
        ("sharpe", values["sharpe"], ".2f"),
        ("buy and hold", values["buy_and_hold_return"], ".2%"),
    ]
    lines = [
        f"Wrote trades.parquet, series.parquet, stats.json and run.json to {out}",
        *(
            f"  {name:<14}{'n/a' if value is None else format(value, spec)}"
            for name, value, spec in rows
        ),
    ]
    return "\n".join(lines) + "\n"


def _timeframe(text: str) -> int:
    """The seconds of a candle length like 30m, 1h or 1d (an argparse type)."""
    match = _TIMEFRAME.fullmatch(text)
    if match is None:
        raise argparse.ArgumentTypeError(f"not a candle length like 30m, 1h or 1d: {text!r}")
    return int(match[1]) * _UNIT_SECONDS[match[2]]
