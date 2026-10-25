"""The post-processing in its order (``make_signal``, ``prepare``, ``decide``) and how it is
recorded (``describe``), by hand: the example of docs/engine.md (Signals) and each step's
argument checks."""

import polars as pl
import pytest

from candlestack.signals import (
    Calibration,
    PostProcessing,
    confirm,
    decide,
    describe,
    ema,
    hysteresis,
    kmeans,
    long_only,
    make_signal,
    min_hold,
    prepare,
    rank,
    threshold,
    zscore,
)

PREDICTIONS = pl.Series("prediction", [0.0, 0.2, 0.2, -0.2, -0.2, -0.2])
RANDOM = pl.Series(
    "prediction", [0.3, -0.1, 0.25, 0.05, -0.4, 0.15, 0.2, -0.05, 0.35, -0.3, 0.1, 0.0]
)


def test_the_default_is_a_threshold_at_0() -> None:
    assert make_signal(PREDICTIONS, PostProcessing()).to_list() == [0.0, 1.0, 1.0, -1.0, -1.0, -1.0]


def test_the_example_of_the_doc_opens_and_closes_one_row_late() -> None:
    """ema span 3, threshold 0.05, long only, 2 candles to confirm (docs/engine.md, Signals)."""
    post = PostProcessing(ema=3, threshold=0.05, long_only=True, confirm=2)
    assert make_signal(PREDICTIONS, post).to_list() == [0.0, 0.0, 1.0, 1.0, 0.0, 0.0]


@pytest.mark.parametrize("calibration", ["zscore", "rank"])
def test_every_step_runs_in_the_order_of_the_doc(calibration: Calibration) -> None:
    post = PostProcessing(
        calibrate=calibration,
        calibration_window=3,
        ema=2,
        rule="hysteresis",
        threshold=0.5,
        exit=0.2,
        long_only=True,
        confirm=2,
        min_hold=3,
    )
    scaled = (zscore if calibration == "zscore" else rank)(RANDOM, 3)
    expected = min_hold(confirm(long_only(hysteresis(ema(scaled, 2), 0.5, 0.2)), 2), 3)
    assert make_signal(RANDOM, post).to_list() == expected.to_list()


def test_prepare_then_decide_is_make_signal() -> None:
    post = PostProcessing(calibrate="rank", calibration_window=4, ema=2, threshold=0.3)
    assert decide(prepare(RANDOM, post), post).to_list() == make_signal(RANDOM, post).to_list()


def test_prepare_leaves_the_predictions_without_calibration_and_ema() -> None:
    assert prepare(RANDOM, PostProcessing()).to_list() == RANDOM.to_list()


def test_the_kmeans_rule_takes_no_limit() -> None:
    post = PostProcessing(rule="kmeans", threshold=None, k=2, kmeans_window=4, refit_every=3)
    assert make_signal(RANDOM, post).to_list() == kmeans(RANDOM, 2, 4, 3).to_list()


def test_the_signal_is_named_signal() -> None:
    assert make_signal(RANDOM, PostProcessing(threshold=0.1)).name == "signal"
    assert make_signal(RANDOM, PostProcessing()).to_list() == threshold(RANDOM, 0.0).to_list()


@pytest.mark.parametrize(
    ("post", "message"),
    [
        (PostProcessing(calibrate="zscore"), "calibrate needs calibration_window"),
        (PostProcessing(rule="kmeans", k=2, kmeans_window=4), "rule kmeans needs k"),
        (PostProcessing(threshold=None), "rule threshold needs threshold"),
        (PostProcessing(rule="hysteresis", threshold=0.2), "rule hysteresis needs exit"),
        (PostProcessing(ema=0), "span"),
    ],
)
def test_a_missing_or_wrong_argument_is_a_value_error(post: PostProcessing, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_signal(RANDOM, post)


def test_describe_lists_the_steps_in_their_order_null_when_off() -> None:
    assert describe(PostProcessing(threshold=0.05)) == {
        "calibrate": None,
        "ema": None,
        "rule": {"name": "threshold", "limit": 0.05},
        "long_only": False,
        "confirm": None,
        "min_hold": None,
    }


def test_describe_names_the_arguments_of_each_function() -> None:
    post = PostProcessing(
        calibrate="zscore",
        calibration_window=500,
        ema=5,
        rule="hysteresis",
        threshold=1.0,
        exit=0.5,
        long_only=True,
        confirm=2,
        min_hold=10,
    )
    record = describe(post)
    assert list(record) == ["calibrate", "ema", "rule", "long_only", "confirm", "min_hold"]
    assert record == {
        "calibrate": {"name": "zscore", "window": 500},
        "ema": {"span": 5},
        "rule": {"name": "hysteresis", "enter": 1.0, "exit": 0.5},
        "long_only": True,
        "confirm": {"candles": 2},
        "min_hold": {"candles": 10},
    }
    kmeans_rule = PostProcessing(
        rule="kmeans", threshold=None, k=3, kmeans_window=500, refit_every=100
    )
    assert describe(kmeans_rule)["rule"] == {
        "name": "kmeans",
        "k": 3,
        "window": 500,
        "refit_every": 100,
    }
