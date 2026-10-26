"""The checks of a manifest on upload (docs/preprocessing.md, checks 2 and 3): one problem in
plain words per check that fails."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from candlestack.ml.inspect import inspect_keras
from candlestack.preprocessing import (
    ManifestProblem,
    check_manifest,
    check_with_model,
    parse_manifest,
    unknown_blocks,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
# The 23 features of the BLESK NO-VOL models, by their names and blocks, in their order.
BLESK_FEATURES = {
    "bb_bbh": "bollinger-high@1",
    "bb_bbl": "bollinger-low@1",
    "kch_high": "keltner-high@1",
    "kch_low": "keltner-low@1",
    "squeeze": "squeeze@1",
    "rsi": "rsi@1",
    "roc": "roc@1",
    "tsi": "tsi@1",
    "atr": "atr@1",
    "adx": "adx@1",
    "aroon_up": "aroon-up@1",
    "aroon": "aroon@1",
    "aroon_down": "aroon-down@1",
    "sma": "sma@1",
    "ema20": "ema@1",
    "ema50": "ema@1",
    "macd_diff": "macd-diff@1",
    "direction": "renko-direction@1",
    "rel_open": "rel-open@1",
    "rel_high": "rel-high@1",
    "rel_low": "rel-low@1",
    "rel_close": "rel-close@1",
    "is_last_bar": "session-last-candle@1",
}
BLESK_NO_VOL: dict[str, Any] = {
    "version": 1,
    "input": {
        "timeframe": "30m",
        "session": "regular",
        "representation": {
            "id": "renko-triples@1",
            "params": {"atr_multiple": 0.6, "atr_window": 14, "atr_at": "brick_start"},
        },
        "features": [
            {"name": name, "id": block, "norm": {"id": "blesk-norm@1"}}
            for name, block in BLESK_FEATURES.items()
        ],
        "window": {"length": 25, "last": "current"},
    },
    "signature": {
        "input": {"shape": [25, 23], "dtype": "float32"},
        "output": {"shape": [1], "dtype": "float32", "meaning": "score"},
    },
}


def problems(document: dict[str, Any]) -> dict[str, str]:
    return {problem.check: problem.message for problem in check_manifest(parse_manifest(document))}


def test_the_manifest_without_preprocessing_can_run(raw: dict[str, Any]) -> None:
    assert check_manifest(parse_manifest(raw)) == []
    assert unknown_blocks(parse_manifest(raw)) == []


def test_the_blesk_manifest_can_run() -> None:
    manifest = parse_manifest(BLESK_NO_VOL)

    assert unknown_blocks(manifest) == []
    assert check_manifest(manifest) == []


def test_a_feature_its_normalization_has_no_rule_for(raw: dict[str, Any]) -> None:
    raw["input"]["features"][1]["norm"] = {"id": "blesk-norm@1"}
    raw["input"]["features"][4]["norm"] = {"id": "blesk-norm@1"}

    assert check_manifest(parse_manifest(raw)) == [
        ManifestProblem(
            "catalog",
            (
                (
                    "input.features[1].norm is blesk-norm@1, which has no rule for high@1, so it "
                    "cannot scale that feature."
                ),
                (
                    "input.features[4].norm is blesk-norm@1, which has no rule for volume@1, so "
                    "it cannot scale that feature."
                ),
            ),
        )
    ]


def test_a_feature_in_the_wrong_step_is_not_checked_against_the_rules(
    raw: dict[str, Any],
) -> None:
    raw["input"]["features"][1] = {
        "name": "rows",
        "id": "candles@1",
        "norm": {"id": "blesk-norm@1"},
    }

    assert problems(raw) == {
        "catalog": "input.features[1] is candles@1, a representation block, where a feature "
        "block goes."
    }


def blesk_without_normalization() -> dict[str, Any]:
    document = copy.deepcopy(BLESK_NO_VOL)
    for feature in document["input"]["features"]:
        del feature["norm"]
    return document


def test_renko_triples_with_the_current_window_can_run() -> None:
    document = blesk_without_normalization()
    document["input"]["features"][2]["id"] = "renko-direction@1"

    assert problems(document) == {}


def test_the_current_window_needs_rows_that_span_several_candles(raw: dict[str, Any]) -> None:
    raw["input"]["window"]["last"] = "current"

    assert problems(raw) == {
        "catalog": 'The window option last "current" needs rows that span several candles, such '
        'as renko-triples@1, but each row of candles@1 is finished at its candle. Use "closed".'
    }


def test_renko_triples_need_the_current_window() -> None:
    document = blesk_without_normalization()
    document["input"]["window"]["last"] = "closed"

    assert problems(document) == {
        "catalog": "The rows of renko-triples@1 span several candles, so the window needs last "
        '"current": the last finished rows and the row still forming. "closed" is not '
        "supported with it."
    }


def test_the_direction_of_a_triple_needs_the_triples(raw: dict[str, Any]) -> None:
    raw["input"]["features"][3] = {"name": "direction", "id": "renko-direction@1"}

    assert problems(raw) == {
        "catalog": "input.features[3] is renko-direction@1, a column of the rows of "
        "renko-triples@1, but the representation is candles@1."
    }


def test_the_renko_parameters_are_checked() -> None:
    document = blesk_without_normalization()
    document["input"]["representation"]["params"] = {"atr_multiple": 0, "atr_at": "brick_end"}

    assert problems(document) == {
        "params": "input.representation.params.atr_multiple: Input should be greater than 0 "
        "(renko-triples@1). input.representation.params.atr_at: Input should be 'brick_start' "
        "(renko-triples@1)."
    }


def test_one_unknown_block(raw: dict[str, Any]) -> None:
    raw["input"]["features"][4]["id"] = "volume@2"

    assert problems(raw) == {"catalog": "The catalog has no block volume@2 yet, so it cannot run."}


def test_a_block_in_the_wrong_step(raw: dict[str, Any]) -> None:
    raw["input"]["representation"]["id"] = "close@1"
    raw["input"]["features"][1]["id"] = "candles@1"
    raw["input"]["features"][2]["norm"]["id"] = "low@1"

    assert problems(raw) == {
        "catalog": "input.representation is close@1, a feature block, where a representation "
        "block goes. input.features[1] is candles@1, a representation block, where a feature "
        "block goes. input.features[2].norm is low@1, a feature block, where a normalization "
        "block goes."
    }


def test_the_last_candle_of_a_session_needs_the_regular_session(raw: dict[str, Any]) -> None:
    raw["input"]["features"][4] = {"name": "last", "id": "session-last-candle@1"}

    assert problems(raw) == {}

    raw["input"]["session"] = "24/7"

    assert problems(raw) == {
        "catalog": "input.features[4] is session-last-candle@1, which marks the last candle of "
        "each trading session, but 24/7 candles have no sessions. Use it with the regular session."
    }


def test_parameters_a_block_does_not_take(raw: dict[str, Any]) -> None:
    raw["input"]["representation"]["params"] = {"size": 3}
    raw["input"]["features"][0]["params"] = {"window": 14}
    raw["input"]["features"][3]["norm"]["params"] = {"scale": 2}

    assert problems(raw) == {
        "params": "input.representation.params.size is not a parameter of candles@1. "
        "input.features[0].params.window is not a parameter of open@1. "
        "input.features[3].norm.params.scale is not a parameter of none@1."
    }


def test_parameters_of_a_block_in_the_wrong_step_are_not_checked(raw: dict[str, Any]) -> None:
    raw["input"]["representation"] = {"id": "close@1", "params": {"size": 3}}

    assert set(problems(raw)) == {"catalog"}


def test_duplicate_and_reserved_feature_names(raw: dict[str, Any]) -> None:
    for feature in raw["input"]["features"][1:3]:
        feature["name"] = "open"
    raw["input"]["features"][3]["name"] = "row_id"
    raw["input"]["features"][4]["name"] = "ts"

    assert problems(raw) == {
        "names": "The feature name open is used 3 times. Each feature needs a name of its own. "
        "The feature name row_id is taken by the id of each row still forming. Rename the "
        "feature. The feature name ts is taken by the time of each row. Rename the feature."
    }


def test_the_signature_must_be_the_window_of_the_features(raw: dict[str, Any]) -> None:
    raw["input"]["window"]["length"] = 1
    raw["input"]["features"] = raw["input"]["features"][:1]

    assert problems(raw) == {
        "signature": "signature.input is [25, 5], but a window of 1 row of 1 feature gives [1, 1]."
    }


def test_the_signature_names_rows_and_features_in_the_plural(raw: dict[str, Any]) -> None:
    raw["input"]["window"]["length"] = 24

    assert problems(raw) == {
        "signature": "signature.input is [25, 5], but a window of 24 rows of 5 features gives "
        "[24, 5]."
    }


def test_the_output_must_be_one_score(raw: dict[str, Any]) -> None:
    raw["signature"]["output"]["shape"] = [3]

    assert problems(raw) == {
        "signature": "signature.output is [3], but CandleStack takes models that give one score "
        "per sample, [1]."
    }


def test_an_example_of_the_signature_s_shape_passes(raw: dict[str, Any]) -> None:
    sample = [[100.0, 101.0, 99.0, 100.5, 1000.0]] * 25
    raw["example"] = {"input": [sample, sample], "output": [[0.1], [0.1]]}

    assert problems(raw) == {}


def test_an_example_of_another_shape(raw: dict[str, Any]) -> None:
    sample = [[100.0, 101.0, 99.0, 100.5]] * 25
    raw["example"] = {"input": [sample], "output": [[0.1], [0.2]]}

    assert problems(raw) == {
        "example": "example.input has the shape [1, 25, 4], but 1 sample of signature.input "
        "make [1, 25, 5]. example.output has the shape [2, 1], but 1 sample of "
        "signature.output make [1, 1]."
    }


def test_an_example_of_ragged_rows(raw: dict[str, Any]) -> None:
    sample = [[1.0] * 5] * 24 + [[1.0] * 4]
    raw["example"] = {"input": [sample, sample], "output": [[0.1], [0.1, 0.2]]}

    assert problems(raw) == {
        "example": "example.input has rows of different lengths, but 2 samples of "
        "signature.input make [2, 25, 5]. example.output has rows of different lengths, but 2 "
        "samples of signature.output make [2, 1]."
    }


def test_every_failed_check_is_one_problem(raw: dict[str, Any]) -> None:
    raw["input"]["features"][0]["id"] = "renko-direction@1"
    raw["input"]["features"][1]["params"] = {"window": 3}
    raw["input"]["features"][2]["name"] = "open"
    raw["signature"]["input"]["shape"] = [25, 23]
    raw["example"] = {"input": [[[1.0]]], "output": [[0.1]]}

    assert [problem.check for problem in check_manifest(parse_manifest(raw))] == [
        "catalog",
        "params",
        "names",
        "signature",
        "example",
    ]


def test_the_manifest_and_the_model_agree(raw: dict[str, Any]) -> None:
    assert check_with_model(parse_manifest(raw), [25, 5], (1,)) == []


@pytest.mark.parametrize(
    ("input_shape", "output_shape", "sentences"),
    [
        ([25, 23], [1], ("The manifest gives [25, 5], the model takes [25, 23].",)),
        ([25, 5], [3], ("The manifest says the model gives [1], the model gives [3].",)),
        (
            [5, 25],
            [25, 1],
            (
                "The manifest gives [25, 5], the model takes [5, 25].",
                "The manifest says the model gives [1], the model gives [25, 1].",
            ),
        ),
        (
            None,
            None,
            (
                "The model file does not say the shape of its input, so it cannot be compared.",
                "The model file does not say the shape of its output, so it cannot be compared.",
            ),
        ),
    ],
)
def test_the_manifest_and_the_model_differ(
    raw: dict[str, Any],
    input_shape: list[int] | None,
    output_shape: list[int] | None,
    sentences: tuple[str, ...],
) -> None:
    assert check_with_model(parse_manifest(raw), input_shape, output_shape) == [
        ManifestProblem("model", sentences)
    ]


def test_the_raw_candles_model_and_its_manifest_pass_every_check(tmp_path: Path) -> None:
    """The fixture pair: the model file read by the upload check, its manifest next to it."""
    facts = inspect_keras(
        FIXTURES / "raw_candles.keras", tmp_path / "weights.part", file_max=50 * 1024 * 1024
    )
    manifest = parse_manifest(
        json.loads((FIXTURES / "raw_candles.manifest.json").read_text(encoding="utf-8"))
    )

    assert check_manifest(manifest) == []
    assert check_with_model(manifest, facts.input_shape, facts.output_shape) == []
