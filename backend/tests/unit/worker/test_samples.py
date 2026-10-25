"""The built-in candles of a trial run: valid candles, enough for the warm-up and the samples,
the same every time."""

from typing import Any

import pytest

from candlestack.preprocessing import WarmUp, build_input, parse_manifest
from candlestack.worker import samples
from candlestack.worker.samples import SAMPLES, sample_candles


def test_they_make_the_samples_of_a_trial_run(manifest: dict[str, Any]) -> None:
    parsed = parse_manifest(manifest)

    candles = sample_candles(parsed)
    made = build_input(candles, parsed)

    assert candles.height == 24 + SAMPLES
    assert made.samples.shape == (SAMPLES, 25, 5)
    assert (candles["high"] >= candles[["open", "close"]].max_horizontal()).all()
    assert (candles["low"] <= candles[["open", "close"]].min_horizontal()).all()
    assert candles["ts"].diff().drop_nulls().unique().to_list() == [1800]
    assert sample_candles(parsed).equals(candles)


@pytest.mark.parametrize(("timeframe", "step"), [("1m", 60), ("4h", 14_400), ("1d", 86_400)])
def test_one_candle_every_timeframe(manifest: dict[str, Any], timeframe: str, step: int) -> None:
    manifest["input"]["timeframe"] = timeframe

    candles = sample_candles(parse_manifest(manifest))

    assert candles["ts"].diff().drop_nulls().unique().to_list() == [step]


@pytest.mark.parametrize(
    ("timeframe", "per_session"), [("1m", 390), ("30m", 13), ("1h", 7), ("4h", 2), ("1d", 1)]
)
def test_the_regular_session_marks_the_last_candle_of_each(
    manifest: dict[str, Any], timeframe: str, per_session: int
) -> None:
    manifest["input"]["timeframe"] = timeframe

    candles = sample_candles(parse_manifest(manifest))

    marked = candles.with_row_index().filter("session_last")["index"]
    assert marked.len() == candles.height // per_session
    assert (marked % per_session == per_session - 1).all()


def test_a_manifest_that_reads_the_marks_makes_its_samples(manifest: dict[str, Any]) -> None:
    manifest["input"]["features"][-1] = {
        "name": "is_last_bar",
        "id": "session-last-candle@1",
        "norm": {"id": "none@1"},
    }
    parsed = parse_manifest(manifest)

    made = build_input(sample_candles(parsed), parsed)

    assert made.samples.shape == (SAMPLES, 25, 5)
    assert set(made.samples[:, :, -1].ravel().tolist()) == {0.0, 1.0}


def test_candles_around_the_clock_have_no_sessions(manifest: dict[str, Any]) -> None:
    manifest["input"]["session"] = "24/7"

    candles = sample_candles(parse_manifest(manifest))

    assert candles.columns == ["ts", "open", "high", "low", "close", "volume"]


def test_an_estimated_warm_up_gets_twice_the_candles(
    manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(samples, "warm_up", lambda _: WarmUp(candles=30, kind="estimate"))

    assert sample_candles(parse_manifest(manifest)).height == 60 + SAMPLES
