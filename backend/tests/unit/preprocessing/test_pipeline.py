"""The model's input from candles and a manifest, by hand."""

from typing import Any

import numpy as np
import polars as pl
import pytest
from pydantic import BaseModel, ConfigDict

from candlestack.preprocessing import (
    CATALOG,
    Block,
    Manifest,
    WarmUp,
    build_input,
    checks,
    parse_manifest,
    pipeline,
    warm_up,
)

COLUMNS = ["open", "high", "low", "close", "volume"]


def candles(count: int) -> pl.DataFrame:
    """``count`` 30-minute candles whose values say their row: open 100 + row, and so on."""
    return pl.DataFrame(
        {
            "ts": [row * 1800 for row in range(count)],
            "open": [100.0 + row for row in range(count)],
            "high": [101.0 + row for row in range(count)],
            "low": [99.0 + row for row in range(count)],
            "close": [100.5 + row for row in range(count)],
            "volume": [1000.0 * row for row in range(count)],
        },
        schema={"ts": pl.Int64, **dict.fromkeys(COLUMNS, pl.Float64)},
    )


def with_window(raw: dict[str, Any], length: int) -> dict[str, Any]:
    raw["input"]["window"]["length"] = length
    raw["signature"]["input"]["shape"] = [length, len(raw["input"]["features"])]
    return raw


def test_raw_candles_are_the_last_25_candles_up_to_each_candle(raw: dict[str, Any]) -> None:
    frame = candles(30)

    built = build_input(frame, parse_manifest(raw))

    assert built.samples.shape == (6, 25, 5)
    assert built.samples.dtype == np.float32
    assert built.dropped == 24
    # A sample is known at the close of its last candle: the 25th candle is the first.
    assert built.ts.to_list() == frame["ts"].to_list()[24:]
    assert built.ts.dtype == pl.Int64
    assert built.samples[0].tolist() == frame.select(COLUMNS).head(25).to_numpy().tolist()
    assert built.samples[-1].tolist() == frame.select(COLUMNS).tail(25).to_numpy().tolist()
    assert built.rows.equals(frame)


def test_the_features_come_in_the_manifest_s_order_under_its_names(raw: dict[str, Any]) -> None:
    raw["input"]["features"] = [
        {"name": "c", "id": "close@1"},
        {"name": "v", "id": "volume@1", "norm": {"id": "none@1"}},
    ]
    raw = with_window(raw, 2)

    built = build_input(candles(3), parse_manifest(raw))

    assert built.rows.columns == ["ts", "c", "v"]
    assert built.samples.tolist() == [
        [[100.5, 0.0], [101.5, 1000.0]],
        [[101.5, 1000.0], [102.5, 2000.0]],
    ]
    assert built.ts.to_list() == [1800, 3600]
    assert built.dropped == 1


def test_a_feature_may_come_twice_under_two_names(raw: dict[str, Any]) -> None:
    raw["input"]["features"] = [{"name": "a", "id": "open@1"}, {"name": "b", "id": "open@1"}]
    raw = with_window(raw, 1)

    built = build_input(candles(2), parse_manifest(raw))

    assert built.samples.tolist() == [[[100.0, 100.0]], [[101.0, 101.0]]]


def test_fewer_candles_than_the_window_give_no_sample(raw: dict[str, Any]) -> None:
    built = build_input(candles(24), parse_manifest(raw))

    assert built.samples.shape == (0, 25, 5)
    assert built.ts.is_empty()
    assert built.dropped == 24


def test_no_candles_give_no_sample(raw: dict[str, Any]) -> None:
    built = build_input(candles(0), parse_manifest(raw))

    assert built.samples.shape == (0, 25, 5)
    assert built.dropped == 0


def test_candles_of_integers_and_more_columns(raw: dict[str, Any]) -> None:
    frame = candles(25).with_columns(
        pl.col("ts").cast(pl.Int32), pl.col("volume").cast(pl.Int64), trades=pl.lit(7)
    )

    built = build_input(frame, parse_manifest(raw))

    assert built.rows.columns == ["ts", *COLUMNS]
    assert built.rows.schema["volume"] == pl.Float64
    assert built.ts.dtype == pl.Int64


def marked(count: int) -> pl.DataFrame:
    """``candles(count)`` with every 13th marked the last of its session, as 30m stocks are."""
    return candles(count).with_columns(
        session_last=pl.Series([row % 13 == 12 for row in range(count)])
    )


def test_the_last_candle_of_each_session_is_one(raw: dict[str, Any]) -> None:
    raw["input"]["features"] = [
        {"name": "close", "id": "close@1"},
        {"name": "is_last_bar", "id": "session-last-candle@1"},
    ]
    raw = with_window(raw, 1)

    built = build_input(marked(27), parse_manifest(raw))

    assert built.rows.columns == ["ts", "close", "is_last_bar"]
    assert built.rows.schema["is_last_bar"] == pl.Float64
    assert built.rows["is_last_bar"].to_list() == [0.0] * 12 + [1.0] + [0.0] * 12 + [1.0, 0.0]
    assert built.dropped == 0


def test_the_session_marks_are_read_only_by_the_block_that_needs_them(
    raw: dict[str, Any],
) -> None:
    built = build_input(marked(25), parse_manifest(raw))

    assert built.rows.columns == ["ts", *COLUMNS]
    assert built.samples.tolist() == build_input(candles(25), parse_manifest(raw)).samples.tolist()


def test_candles_without_session_marks_cannot_give_the_last_candle(raw: dict[str, Any]) -> None:
    raw["input"]["features"][0] = {"name": "last", "id": "session-last-candle@1"}

    with pytest.raises(ValueError, match="do not mark the last candle of each session"):
        build_input(candles(25), parse_manifest(raw))


def test_a_manifest_that_cannot_run_is_refused(raw: dict[str, Any]) -> None:
    raw["input"]["representation"]["id"] = "kagi@1"

    with pytest.raises(
        ValueError,
        match=r"^The manifest cannot run: The catalog has no block kagi@1 yet",
    ):
        build_input(candles(30), parse_manifest(raw))


@pytest.mark.parametrize(
    ("frame", "message"),
    [
        (candles(3).drop("volume", "low"), "The candles have no column low, volume"),
        (
            candles(3).with_columns(pl.col("ts").cast(pl.Float64)),
            "ts must be integers, not Float64",
        ),
        (
            candles(3).with_columns(pl.col("open").cast(pl.String)),
            "The candles' open must be numbers",
        ),
        (
            candles(3).with_columns(close=pl.Series([1.0, None, 2.0])),
            "A candle has a missing value",
        ),
        (
            candles(3).with_columns(close=pl.Series([float("nan"), 101.5, 102.5])),
            "A candle has a value that is not a finite number",
        ),
        (
            candles(3).with_columns(volume=pl.Series([0.0, float("inf"), 2000.0])),
            "A candle has a value that is not a finite number",
        ),
        (
            candles(3).with_columns(ts=pl.Series([0, 1800, 1800])),
            "Each candle's ts must be after the one before it",
        ),
        (
            candles(3).with_columns(ts=pl.Series([3600, 1800, 0])),
            "Each candle's ts must be after the one before it",
        ),
        (
            candles(3).with_columns(session_last=pl.Series([0, 0, 1])),
            "The candles' session_last must be true or false",
        ),
        (
            candles(3).with_columns(session_last=pl.Series([False, None, True])),
            "A candle has a missing value",
        ),
    ],
    ids=[
        "missing",
        "float-ts",
        "text",
        "null",
        "nan",
        "infinite",
        "repeated-ts",
        "unsorted",
        "numeric-marks",
        "missing-mark",
    ],
)
def test_candles_that_break_their_rules(
    raw: dict[str, Any], frame: pl.DataFrame, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_input(frame, parse_manifest(raw))


@pytest.mark.parametrize(("length", "candles_before"), [(25, 24), (1, 0)])
def test_the_warm_up_of_raw_candles_is_exactly_the_window(
    raw: dict[str, Any], length: int, candles_before: int
) -> None:
    assert warm_up(parse_manifest(with_window(raw, length))) == WarmUp(candles_before, "exact")


def test_the_exact_warm_up_is_what_the_build_drops(raw: dict[str, Any]) -> None:
    manifest = parse_manifest(raw)

    assert build_input(candles(40), manifest).dropped == warm_up(manifest).candles


# The BLESK NO-VOL input (docs/preprocessing.md) without what is still to come: its
# normalization and the four rel_* columns.
BLESK_FEATURES = [
    ("bb_bbh", "bollinger-high@1", {"window": 20, "dev": 2.0}),
    ("bb_bbl", "bollinger-low@1", {"window": 20, "dev": 2.0}),
    ("kch_high", "keltner-high@1", {}),
    ("kch_low", "keltner-low@1", {}),
    ("squeeze", "squeeze@1", {}),
    ("rsi", "rsi@1", {"window": 20}),
    ("roc", "roc@1", {}),
    ("tsi", "tsi@1", {}),
    ("atr", "atr@1", {}),
    ("adx", "adx@1", {}),
    ("aroon_up", "aroon-up@1", {}),
    ("aroon", "aroon@1", {}),
    ("aroon_down", "aroon-down@1", {}),
    ("sma", "sma@1", {}),
    ("ema20", "ema@1", {"window": 20}),
    ("ema50", "ema@1", {"window": 50}),
    ("macd_diff", "macd-diff@1", {}),
    ("direction", "renko-direction@1", {}),
    ("is_last_bar", "session-last-candle@1", {}),
]


def renko(features: list[tuple[str, str, dict[str, Any]]], length: int = 25) -> Manifest:
    return parse_manifest(
        {
            "version": 1,
            "input": {
                "timeframe": "30m",
                "session": "regular",
                "representation": {
                    "id": "renko-triples@1",
                    "params": {"atr_multiple": 0.6, "atr_window": 14, "atr_at": "brick_start"},
                },
                "features": [
                    {"name": name, "id": block, "params": params}
                    for name, block, params in features
                ],
                "window": {"length": length, "last": "current"},
            },
            "signature": {
                "input": {"shape": [length, len(features)], "dtype": "float32"},
                "output": {"shape": [1], "dtype": "float32", "meaning": "score"},
            },
        }
    )


def walk(count: int, seed: int = 0) -> pl.DataFrame:
    """A random walk of 30-minute candles, every 13th the last of its session."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, count)))
    open_ = np.concatenate(([100.0], close[:-1]))
    spread = np.abs(rng.normal(0, 0.004, count))
    return pl.DataFrame(
        {
            "ts": np.arange(count, dtype=np.int64) * 1800,
            "open": open_,
            "high": np.maximum(open_, close) * (1 + spread),
            "low": np.minimum(open_, close) * (1 - spread),
            "close": close,
            "volume": rng.uniform(1_000, 100_000, count),
            "session_last": np.arange(count) % 13 == 12,
        }
    )


def test_the_blesk_input_without_its_normalization_is_25_rows_of_triples() -> None:
    manifest = renko(BLESK_FEATURES)
    frame = walk(1000)

    built = build_input(frame, manifest)

    assert built.samples.shape[1:] == (25, 19)
    assert len(built.samples) > 0
    assert np.isfinite(built.samples).all()
    assert built.rows.columns == ["ts", "row_id", *(name for name, _, _ in BLESK_FEATURES)]
    assert warm_up(manifest) == WarmUp(candles=350 + 53, kind="estimate")
    # The estimate is long enough: the first sample comes before it.
    assert built.dropped <= warm_up(manifest).candles


def test_the_build_drops_the_candles_before_the_first_sample_not_the_rows() -> None:
    """Triples start on the third brick after the ATR's warm-up: the candles before the
    first sample are many more than the rows the window drops."""
    frame = walk(600)

    built = build_input(frame, renko([("close", "close@1", {})], 3))

    first = int(frame["ts"].search_sorted(built.ts[0]))
    assert built.dropped == first == frame.height - len(built.samples)
    assert built.dropped > built.rows.height - len(built.samples)


def test_a_build_without_samples_drops_every_candle() -> None:
    frame = walk(230)

    built = build_input(frame, renko([("close", "close@1", {})], 25))

    assert len(built.samples) == 0
    assert built.dropped == frame.height


def test_a_triple_takes_the_candle_features_at_its_candle_and_its_own_direction() -> None:
    frame = walk(400)
    manifest = renko([("close", "close@1", {}), ("direction", "renko-direction@1", {})], 1)

    built = build_input(frame, manifest)

    triples = CATALOG["renko-triples@1"].run(frame, CATALOG["renko-triples@1"].params())
    at_candle = triples.join(frame.select("ts", "close"), on="ts", how="left")
    assert built.rows["ts"].equals(triples["ts"])
    assert built.rows["row_id"].equals(triples["row_id"])
    assert built.rows["close"].equals(at_candle["close"])
    assert built.rows["direction"].equals(triples["direction"])
    assert (
        built.samples[:, 0, :].tolist()
        == built.rows.select("close", "direction").to_numpy().astype(np.float32).tolist()
    )


def test_the_current_window_of_triples_ends_at_each_candle() -> None:
    frame = walk(600)

    built = build_input(frame, renko([("close", "close@1", {})], 3))

    rows = built.rows.with_row_index()
    finished = rows.filter(pl.col("row_id") != pl.col("row_id").shift(-1))["index"].to_list()
    # Each sample is the two finished rows before its candle, then the row of its candle.
    for sample, ts in zip(built.samples, built.ts, strict=True):
        index = int(rows.filter(pl.col("ts") == ts)["index"][0])
        earlier = [row for row in finished if row < index][-2:]
        expected = rows["close"].gather([*earlier, index]).cast(pl.Float32).to_list()
        assert sample[:, 0].tolist() == expected


class Lag(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    lag: int = 3


def lagged(frame: pl.DataFrame, params: Lag) -> pl.Series:
    """The close ``lag`` candles ago: null for the first ``lag`` candles."""
    return frame["close"].shift(params.lag)


# Blocks made up for the warm-up: a feature with one, and a representation of bricks whose
# rows take about 2.5 candles each after an ATR of 14 candles.
LAGGED = Block(
    id="lagged-close@1",
    step="feature",
    description="",
    run=lagged,
    params=Lag,
    warm_up=lambda params: params.lag,
    group="Momentum",
)
BRICKS = Block(
    id="bricks@1",
    step="representation",
    description="",
    run=lambda frame, _params: frame,
    warm_up=lambda _params: 14,
    candles_per_row=lambda _params: 2.5,
)


@pytest.fixture
def made_up(monkeypatch: pytest.MonkeyPatch) -> None:
    catalog = {**CATALOG, LAGGED.id: LAGGED, BRICKS.id: BRICKS}
    monkeypatch.setattr(pipeline, "CATALOG", catalog)
    monkeypatch.setattr(checks, "CATALOG", catalog)


@pytest.mark.usefixtures("made_up")
def test_the_exact_warm_up_is_the_longest_feature_and_the_window(raw: dict[str, Any]) -> None:
    raw["input"]["features"][1] = {"name": "lag", "id": "lagged-close@1", "params": {"lag": 5}}
    manifest = parse_manifest(raw)

    assert warm_up(manifest) == WarmUp(candles=5 + 24, kind="exact")
    assert build_input(candles(40), manifest).dropped == 29


@pytest.mark.usefixtures("made_up")
def test_rows_of_several_candles_give_an_estimate(raw: dict[str, Any]) -> None:
    raw["input"]["representation"] = {"id": "bricks@1"}

    # The ATR's 14 candles, then 24 earlier rows of about 2.5 candles each.
    assert warm_up(parse_manifest(raw)) == WarmUp(candles=14 + 60, kind="estimate")


@pytest.mark.usefixtures("made_up")
def test_the_estimate_rounds_up_and_counts_the_longest_warm_up_once(
    raw: dict[str, Any],
) -> None:
    raw["input"]["representation"] = {"id": "bricks@1"}
    raw["input"]["features"][0] = {"name": "lag", "id": "lagged-close@1", "params": {"lag": 20}}
    raw = with_window(raw, 2)

    assert warm_up(parse_manifest(raw)) == WarmUp(candles=20 + 3, kind="estimate")
