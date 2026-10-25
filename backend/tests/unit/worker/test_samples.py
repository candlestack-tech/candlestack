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


def test_an_estimated_warm_up_gets_twice_the_candles(
    manifest: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(samples, "warm_up", lambda _: WarmUp(candles=30, kind="estimate"))

    assert sample_candles(parse_manifest(manifest)).height == 60 + SAMPLES
