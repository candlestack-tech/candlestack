"""The form of a manifest (the schema ``candlestack.manifest/1``): what parses, and the plain
words of what does not."""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from candlestack.preprocessing import InvalidManifest, Manifest, parse_manifest

DOC = Path(__file__).parents[4] / "docs" / "preprocessing.md"

Edit = Callable[[dict[str, Any]], object]


def doc_examples() -> list[str]:
    return re.findall(r"```json\n(.*?)```", DOC.read_text(encoding="utf-8"), re.DOTALL)


def schema_problem(document: str | dict[str, Any]) -> str:
    with pytest.raises(InvalidManifest) as invalid:
        parse_manifest(document)
    assert invalid.value.problem.check == "schema"
    assert str(invalid.value) == invalid.value.problem.message
    return invalid.value.problem.message


def test_both_examples_of_the_doc_parse() -> None:
    examples = doc_examples()

    assert len(examples) == 2
    raw, blesk = (parse_manifest(example) for example in examples)
    assert raw.input.window.length == blesk.input.window.length == 25
    assert blesk.input.representation.params["atr_multiple"] == 0.6
    assert blesk.input.window.last == "current"
    assert blesk.input.features[2].norm.id == "none@1"


def test_the_fixture_is_the_doc_example_without_preprocessing(raw: dict[str, Any]) -> None:
    assert raw == json.loads(doc_examples()[0])


def test_text_and_a_json_object_parse_the_same(raw: dict[str, Any]) -> None:
    text = json.dumps(raw)

    assert parse_manifest(text) == parse_manifest(text.encode()) == parse_manifest(raw)


def test_a_feature_without_norm_is_not_normalized(raw: dict[str, Any]) -> None:
    del raw["input"]["features"][0]["norm"]

    feature = parse_manifest(raw).input.features[0]

    assert feature.norm.id == "none@1"
    assert feature.params == {}
    assert feature.norm.params == {}


def test_defaults_fill_what_the_manifest_leaves_out(raw: dict[str, Any]) -> None:
    del raw["about"]

    manifest = parse_manifest(raw)

    assert manifest.input.window.last == "closed"
    assert manifest.example is None
    assert manifest.about == {}


def test_the_manifest_dumps_back_with_its_field_names(raw: dict[str, Any]) -> None:
    dumped = parse_manifest(raw).model_dump(by_alias=True)

    assert dumped["schema"] == "candlestack.manifest/1"
    assert Manifest.model_validate(dumped) == parse_manifest(raw)


def test_a_document_that_is_no_json() -> None:
    assert schema_problem("{").startswith("The manifest is not valid JSON: ")


@pytest.mark.parametrize("document", ["[]", "25", '"manifest"'])
def test_a_document_that_is_no_object(document: str) -> None:
    assert schema_problem(document) == "The manifest must be a JSON object."


def test_every_problem_of_the_form_is_one_sentence_of_one_message(raw: dict[str, Any]) -> None:
    raw["schema"] = "candlestack.manifest/2"
    del raw["signature"]
    raw["input"]["features"][2]["name"] = ""
    raw["input"]["window"]["size"] = 25

    assert schema_problem(raw) == (
        "schema: Input should be 'candlestack.manifest/1'. "
        "input.features[2].name: String should have at least 1 character. "
        "input.window.size is not a field of the manifest. "
        "signature is missing."
    )


@pytest.mark.parametrize(
    ("edit", "message"),
    [
        # A number in a string, a float or a bool is not an integer: the manifest is strict.
        (
            lambda m: m["input"]["window"].update(length="25"),
            "input.window.length: Input should be a valid integer.",
        ),
        (
            lambda m: m["input"]["window"].update(length=25.5),
            "input.window.length: Input should be a valid integer.",
        ),
        (
            lambda m: m["signature"]["input"].update(shape=[25, True]),
            "signature.input.shape[1]: Input should be a valid integer.",
        ),
        (
            lambda m: m["input"]["window"].update(length=0),
            "input.window.length: Input should be greater than or equal to 1.",
        ),
        (
            lambda m: m["input"]["window"].update(length=1001),
            "input.window.length: Input should be less than or equal to 1000.",
        ),
        (
            lambda m: m["input"]["window"].update(last="next"),
            "input.window.last: Input should be 'closed' or 'current'.",
        ),
        (
            lambda m: m["input"].update(timeframe="2h"),
            "input.timeframe: Input should be '1m', '5m', '15m', '30m', '1h', '4h' or '1d'.",
        ),
        (
            lambda m: m["input"]["representation"].update(id="Candles"),
            (
                "input.representation.id: String should match pattern "
                "'^[a-z][a-z0-9]*(-[a-z0-9]+)*@[1-9][0-9]*$'."
            ),
        ),
        (
            lambda m: m["input"].update(features=[]),
            "input.features: List should have at least 1 item after validation, not 0.",
        ),
        (
            lambda m: m["signature"]["input"].update(dtype="float64"),
            "signature.input.dtype: Input should be 'float32'.",
        ),
        (
            lambda m: m["signature"]["output"].update(meaning="probability"),
            "signature.output.meaning: Input should be 'score'.",
        ),
        (
            lambda m: m.update(about={"epochs": 20}),
            "about.epochs: Input should be a valid string.",
        ),
        (
            lambda m: m.update(example={"input": [[[1, "2"]]], "output": [[0.1]]}),
            "example.input[0][0][1]: Input should be a valid number.",
        ),
    ],
)
def test_a_value_of_the_wrong_type_or_out_of_range(
    raw: dict[str, Any], edit: Edit, message: str
) -> None:
    edit(raw)

    assert schema_problem(raw) == message


def test_the_python_name_of_the_schema_field_is_not_a_field(raw: dict[str, Any]) -> None:
    raw["schema_"] = raw.pop("schema")

    assert schema_problem(raw) == "schema is missing. schema_ is not a field of the manifest."


def test_params_of_any_block_parse_and_the_catalog_checks_them(raw: dict[str, Any]) -> None:
    raw["input"]["features"][0]["params"] = {"window": 20, "anything": [1, 2]}

    assert parse_manifest(raw).input.features[0].params == {"window": 20, "anything": [1, 2]}
