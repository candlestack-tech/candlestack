"""Signals: turn a model's predictions into the signals the engine trades on, and the
post-processing that applies them in their order (``PostProcessing``, ``make_signal``).

It imports no other module and no server library. Other modules import only from here, never
from the submodules.
"""

from candlestack.signals.alignment import AlignReport, align, relabel
from candlestack.signals.calibration import rank, zscore
from candlestack.signals.clusters import kmeans
from candlestack.signals.filters import confirm, ema, hysteresis
from candlestack.signals.holding import min_hold
from candlestack.signals.post_processing import (
    Calibration,
    PostProcessing,
    Rule,
    decide,
    describe,
    make_signal,
    prepare,
)
from candlestack.signals.rules import long_only, threshold

__all__ = [
    "AlignReport",
    "Calibration",
    "PostProcessing",
    "Rule",
    "align",
    "confirm",
    "decide",
    "describe",
    "ema",
    "hysteresis",
    "kmeans",
    "long_only",
    "make_signal",
    "min_hold",
    "prepare",
    "rank",
    "relabel",
    "threshold",
    "zscore",
]
