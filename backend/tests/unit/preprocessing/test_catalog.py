"""The catalog: what every block registers, and how the app reads it."""

import re
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field

from candlestack.preprocessing import CATALOG, Block, ParamOut, describe_catalog
from candlestack.preprocessing.catalog import _describe
from candlestack.preprocessing.manifest import BLOCK_ID


def test_the_first_blocks_are_registered() -> None:
    assert {block: CATALOG[block].step for block in CATALOG} == {
        "candles@1": "representation",
        "open@1": "feature",
        "high@1": "feature",
        "low@1": "feature",
        "close@1": "feature",
        "volume@1": "feature",
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


def test_the_candle_columns_are_in_the_candle_group() -> None:
    groups = {block.id: block.group for block in describe_catalog().blocks if block.group}

    assert groups == dict.fromkeys(["open@1", "high@1", "low@1", "close@1", "volume@1"], "Candle")


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
