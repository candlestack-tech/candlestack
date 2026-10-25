"""The checks of a manifest on upload (docs/preprocessing.md, checks 2 and 3): one problem in
plain words per check that fails."""

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
BLESK_NO_VOL = {
    "version": 1,
    "input": {
        "timeframe": "30m",
        "session": "regular",
        "representation": {
            "id": "renko-triples@1",
            "params": {"atr_multiple": 0.6, "atr_window": 14, "atr_at": "brick_start"},
        },
        "features": [
            {
                "name": name,
                "id": "rsi@1" if name == "rsi" else "close@1",
                "norm": {"id": "blesk-norm@1"},
            }
            for name in ["bb_bbh", "rsi", *(f"f{index}" for index in range(21))]
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


def test_blocks_still_to_come_parse_but_cannot_run() -> None:
    manifest = parse_manifest(BLESK_NO_VOL)

    assert unknown_blocks(manifest) == ["renko-triples@1", "blesk-norm@1", "rsi@1"]
    assert check_manifest(manifest) == [
        ManifestProblem(
            "catalog",
            (
                (
                    "The catalog has no blocks renko-triples@1, blesk-norm@1 and rsi@1 yet, so "
                    "it cannot run."
                ),
                (
                    'The window option last "current" is not supported yet. It needs rows that '
                    "span several candles, such as Renko bricks."
                ),
            ),
        )
    ]


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
    raw["input"]["features"][4]["name"] = "ts"

    assert problems(raw) == {
        "names": "The feature name open is used 3 times. Each feature needs a name of its own. "
        "The feature name ts is taken by the time of each row. Rename the feature."
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
    raw["input"]["features"][0]["id"] = "rsi@1"
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
