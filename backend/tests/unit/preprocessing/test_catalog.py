"""The catalog: what every block registers, and how the app reads it."""

import re
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from candlestack.preprocessing import CATALOG, Block, ParamOut, describe_catalog
from candlestack.preprocessing.catalog import _describe
from candlestack.preprocessing.manifest import BLOCK_ID

# The indicators of the BLESK models, in the order of their input, with their display group.
INDICATORS = {
    "bollinger-high@1": "Bands",
    "bollinger-low@1": "Bands",
    "keltner-high@1": "Bands",
    "keltner-low@1": "Bands",
    "squeeze@1": "Bands",
    "rsi@1": "Momentum",
    "roc@1": "Momentum",
    "tsi@1": "Momentum",
    "atr@1": "Bands",
    "adx@1": "Trend",
    "aroon-up@1": "Trend",
    "aroon@1": "Trend",
    "aroon-down@1": "Trend",
    "sma@1": "Trend",
    "ema@1": "Trend",
    "macd-diff@1": "Trend",
}


def test_the_blocks_are_registered() -> None:
    assert {block: CATALOG[block].step for block in CATALOG} == {
        "candles@1": "representation",
        "open@1": "feature",
        "high@1": "feature",
        "low@1": "feature",
        "close@1": "feature",
        "volume@1": "feature",
        "session-last-candle@1": "feature",
        **dict.fromkeys(INDICATORS, "feature"),
        "none@1": "normalization",
        "window@1": "window",
    }


@pytest.mark.parametrize("block", list(CATALOG.values()), ids=list(CATALOG))
def test_every_block_is_registered_under_its_versioned_id(block: Block) -> None:
    assert re.fullmatch(BLOCK_ID, block.id)
    assert CATALOG[block.id] is block
    assert block.description
    assert block.tunable <= set(block.params.model_fields)


@pytest.mark.parametrize("block", list(CATALOG.values()), ids=list(CATALOG))
def test_every_parameter_has_a_type_the_app_can_show(block: Block) -> None:
    """A parameter of a type the catalog cannot describe (an object, a union) would give the
    app a form field it cannot build."""
    for param in _describe(block).params:
        schema = block.params.model_json_schema()["properties"][param.name]
        assert schema.get("type") == param.type


def test_the_catalog_lists_the_blocks_by_step() -> None:
    blocks = describe_catalog().blocks

    assert [block.id for block in blocks] == [
        "candles@1",
        "open@1",
        "high@1",
        "low@1",
        "close@1",
        "volume@1",
        "session-last-candle@1",
        *INDICATORS,
        "none@1",
        "window@1",
    ]
    assert blocks[0].model_dump() == {
        "id": "candles@1",
        "step": "representation",
        "description": "Every candle a row",
        "group": None,
        "warm_up": "0",
        "params": [],
    }


@pytest.mark.parametrize("block", list(CATALOG.values()), ids=list(CATALOG))
def test_every_feature_has_a_display_group_and_no_other_block_has_one(block: Block) -> None:
    assert (block.group is not None) == (block.step == "feature")


def test_the_features_are_in_their_display_groups() -> None:
    groups = {block.id: block.group for block in describe_catalog().blocks if block.group}

    assert groups == {
        **dict.fromkeys(
            ["open@1", "high@1", "low@1", "close@1", "volume@1", "session-last-candle@1"], "Candle"
        ),
        **INDICATORS,
    }


def test_the_catalog_describes_an_indicator_with_its_defaults_and_warm_up() -> None:
    rsi = next(block for block in describe_catalog().blocks if block.id == "rsi@1")

    assert (rsi.step, rsi.group, rsi.warm_up) == ("feature", "Momentum", "14 * window")
    assert [param.model_dump() for param in rsi.params] == [
        {
            "name": "window",
            "type": "integer",
            "description": "Candles in the window",
            "required": False,
            "default": 20,
            "minimum": 2,
            "maximum": 1000,
            "exclusive_minimum": None,
            "exclusive_maximum": None,
            "options": None,
            "tunable": False,
        }
    ]


@pytest.mark.parametrize(
    ("block", "rule"),
    [
        ("bollinger-high@1", "window - 1"),
        ("squeeze@1", "max(bollinger_window - 1, keltner_window - 1, keltner_atr_window)"),
        ("roc@1", "window"),
        ("tsi@1", "7 * (slow + fast)"),
        ("atr@1", "15 * window - 1"),
        ("adx@1", "20 * window"),
        ("ema@1", "7 * window"),
        ("macd-diff@1", "7 * (max(fast, slow) + signal)"),
    ],
)
def test_the_warm_up_rule_says_what_the_warm_up_counts(block: str, rule: str) -> None:
    """The rule the app shows, evaluated on the block's defaults, is the warm-up it counts."""
    params = CATALOG[block].params()
    names = {"max": max, **params.model_dump()}

    assert CATALOG[block].warm_up_rule == rule
    assert eval(rule, {"__builtins__": {}}, names) == CATALOG[block].warm_up(params)


def test_only_a_representation_may_span_several_candles_a_row() -> None:
    assert [block.id for block in CATALOG.values() if block.candles_per_row] == []


def test_the_catalog_describes_the_window_and_its_parameters() -> None:
    window = describe_catalog().blocks[-1]

    assert window.warm_up == "length - 1"
    assert [param.model_dump() for param in window.params] == [
        {
            "name": "length",
            "type": "integer",
            "description": "How many rows make one sample",
            "required": True,
            "default": None,
            "minimum": 1,
            "maximum": 1000,
            "exclusive_minimum": None,
            "exclusive_maximum": None,
            "options": None,
            "tunable": False,
        },
        {
            "name": "last",
            "type": "string",
            "description": window.params[1].description,
            "required": False,
            "default": "closed",
            "minimum": None,
            "maximum": None,
            "exclusive_minimum": None,
            "exclusive_maximum": None,
            "options": ["closed", "current"],
            "tunable": False,
        },
    ]


class BrickParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    atr_multiple: float = Field(default=0.6, gt=0, lt=10, description="Brick size in ATRs")
    atr_window: int = Field(default=14, ge=2)
    close_only: bool = False
    atr_at: Literal["brick_start", "brick_end"] = "brick_start"


def test_a_block_with_parameters_describes_types_ranges_defaults_and_tunable() -> None:
    """The parameters later blocks will have, on a block made up for the test."""
    block = Block(
        id="bricks@1",
        step="representation",
        description="Bricks",
        run=lambda frame, _params: frame,
        params=BrickParams,
        tunable=frozenset({"atr_multiple"}),
    )

    params = {param.name: param for param in _describe(block).params}

    assert params["atr_multiple"] == ParamOut(
        name="atr_multiple",
        type="number",
        description="Brick size in ATRs",
        required=False,
        default=0.6,
        minimum=None,
        maximum=None,
        exclusive_minimum=0,
        exclusive_maximum=10,
        options=None,
        tunable=True,
    )
    assert (params["atr_window"].type, params["atr_window"].minimum) == ("integer", 2)
    assert (params["close_only"].type, params["close_only"].default) == ("boolean", False)
    assert params["atr_at"].options == ["brick_start", "brick_end"]
    assert not params["atr_window"].tunable


def test_only_the_last_candle_of_a_session_needs_sessions() -> None:
    assert [block.id for block in CATALOG.values() if block.needs_session] == [
        "session-last-candle@1"
    ]
    last = next(block for block in describe_catalog().blocks if block.id == "session-last-candle@1")
    assert (last.step, last.group, last.warm_up, last.params) == ("feature", "Candle", "0", [])
