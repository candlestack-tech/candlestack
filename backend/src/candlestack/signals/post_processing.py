"""The post-processing: the steps between ``align`` and ``backtest``, in the order of
docs/engine.md (Signals): calibration (``zscore`` or ``rank``), ``ema``, one rule that makes the
signal (``threshold``, ``hysteresis`` or ``kmeans``), ``long_only``, ``confirm``, ``min_hold``.

Whatever runs a backtest (the ``candlestack-bt`` command, the backtests of the API) applies the
steps (``make_signal``, or ``prepare`` and ``decide`` apart) and records them (``describe``)
from here, so the order cannot drift.
"""

from dataclasses import dataclass
from typing import Any, Literal

import polars as pl

from candlestack.signals.calibration import rank, zscore
from candlestack.signals.clusters import kmeans
from candlestack.signals.filters import confirm, ema, hysteresis
from candlestack.signals.holding import min_hold
from candlestack.signals.rules import long_only, threshold

Calibration = Literal["zscore", "rank"]
Rule = Literal["threshold", "hysteresis", "kmeans"]


@dataclass(frozen=True, slots=True)
class PostProcessing:
    """The steps from the predictions to the signal, in the order ``make_signal`` applies
    them: calibration, ``ema``, one rule that makes the signal, ``long_only``, ``confirm``,
    ``min_hold``. A step that is None is skipped. Plain values only, so run-all can send it to
    its worker processes.

    Attributes:
        calibrate: ``zscore`` or ``rank`` from the ``calibration_window`` predictions ending
            at each one, or None.
        calibration_window: the window of ``calibrate``, 2 or more.
        ema: the span of the exponential moving average, 1 or more, or None.
        rule: ``threshold`` (its limit is ``threshold``), ``hysteresis`` (``threshold`` is the
            limit a position opens at, ``exit`` the one it closes below) or ``kmeans`` (``k``,
            ``kmeans_window``, ``refit_every``; ``threshold`` is None).
        long_only: flat instead of short.
        confirm: the rows a value must hold before it counts, or None.
        min_hold: the rows a new position is kept for, or None.
    """

    calibrate: Calibration | None = None
    calibration_window: int | None = None
    ema: int | None = None
    rule: Rule = "threshold"
    threshold: float | None = 0.0
    exit: float | None = None
    k: int | None = None
    kmeans_window: int | None = None
    refit_every: int | None = None
    long_only: bool = False
    confirm: int | None = None
    min_hold: int | None = None


def make_signal(predictions: pl.Series, post: PostProcessing) -> pl.Series:
    """The ``signal`` of the predictions after every step of ``post``, in their order.

    Raises:
        ValueError: a step refuses its argument or the predictions, or ``post`` lacks an
            argument its rule needs.
    """
    return decide(prepare(predictions, post), post)


def prepare(predictions: pl.Series, post: PostProcessing) -> pl.Series:
    """The steps before the rule: calibration, then ``ema``. Apart from ``make_signal`` so that
    run-all does them once per model: its thresholds change the rule alone."""
    if post.calibrate is not None:
        if post.calibration_window is None:
            raise ValueError("calibrate needs calibration_window")
        calibrate = zscore if post.calibrate == "zscore" else rank
        predictions = calibrate(predictions, post.calibration_window)
    if post.ema is not None:
        predictions = ema(predictions, post.ema)
    return predictions


def decide(predictions: pl.Series, post: PostProcessing) -> pl.Series:
    """The steps from the rule on: the rule, ``long_only``, ``confirm``, ``min_hold``."""
    signal = _rule(predictions, post)
    if post.long_only:
        signal = long_only(signal)
    if post.confirm is not None:
        signal = confirm(signal, post.confirm)
    if post.min_hold is not None:
        signal = min_hold(signal, post.min_hold)
    return signal


def _rule(predictions: pl.Series, post: PostProcessing) -> pl.Series:
    if post.rule == "kmeans":
        if post.k is None or post.kmeans_window is None or post.refit_every is None:
            raise ValueError("rule kmeans needs k, kmeans_window and refit_every")
        return kmeans(predictions, post.k, post.kmeans_window, post.refit_every)
    if post.threshold is None:
        raise ValueError(f"rule {post.rule} needs threshold")
    if post.rule == "hysteresis":
        if post.exit is None:
            raise ValueError("rule hysteresis needs exit")
        return hysteresis(predictions, post.threshold, post.exit)
    return threshold(predictions, post.threshold)


def describe(post: PostProcessing) -> dict[str, Any]:
    """The steps as ``run.json`` lists them under ``post_processing``: in their order, each
    with the arguments of its function, null when it is off."""
    rules = {
        "threshold": {"limit": post.threshold},
        "hysteresis": {"enter": post.threshold, "exit": post.exit},
        "kmeans": {"k": post.k, "window": post.kmeans_window, "refit_every": post.refit_every},
    }
    calibrate = {"name": post.calibrate, "window": post.calibration_window}
    return {
        "calibrate": None if post.calibrate is None else calibrate,
        "ema": None if post.ema is None else {"span": post.ema},
        "rule": {"name": post.rule, **rules[post.rule]},
        "long_only": post.long_only,
        "confirm": None if post.confirm is None else {"candles": post.confirm},
        "min_hold": None if post.min_hold is None else {"candles": post.min_hold},
    }
