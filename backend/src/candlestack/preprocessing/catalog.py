"""The catalog: every block a manifest can name, by its versioned id.

A block is registered here with the Pydantic model of its parameters, its warm-up and a short
description; ``GET /api/v1/preprocessing/catalog`` serves what ``describe_catalog`` gives, so
the app builds its forms from it and a new block needs no change of the frontend. A block
registered here is in the look-ahead test at once.

An id never changes what it computes: a block that gives other numbers is a new version
(``rsi@2``), so an old manifest keeps meaning what it meant.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, Field

from candlestack.preprocessing import blocks
from candlestack.preprocessing.manifest import Window

Step = Literal["representation", "feature", "normalization", "window"]

# The manifest's `input.window` holds the parameters of this block, the only window for now.
WINDOW = "window@1"


@dataclass(frozen=True, slots=True)
class Block:
    """One block of the catalog.

    - ``run``: the function, in the form of its step (``blocks``);
    - ``params``: the model its parameters are checked with;
    - ``warm_up``: how many earlier candles it needs before its first complete value (rows,
      for the window), from its parameters; ``warm_up_rule`` says it in words for the app;
    - ``tunable``: the parameters a user may change on a trained model, for a later experiment
      mode (docs/preprocessing.md#what-the-user-chooses); none yet.
    """

    id: str
    step: Step
    description: str
    run: Callable[..., Any]
    params: type[BaseModel] = blocks.NoParams
    warm_up: Callable[[Any], int] = blocks.no_warm_up
    warm_up_rule: str = "0"
    tunable: frozenset[str] = field(default_factory=frozenset)


def _candle_column(name: str) -> Block:
    return Block(
        id=f"{name}@1",
        step="feature",
        description=f"The {name} of the candle",
        run=blocks.candle_column(name),
    )


CATALOG: Mapping[str, Block] = MappingProxyType(
    {
        block.id: block
        for block in (
            Block(
                id="candles@1",
                step="representation",
                description="Every candle a row",
                run=blocks.candles,
            ),
            *(_candle_column(name) for name in blocks.CANDLE_COLUMNS),
            Block(
                id="none@1",
                step="normalization",
                description="No normalization: the values as they are",
                run=blocks.none,
            ),
            Block(
                id=WINDOW,
                step="window",
                description="The last rows up to each row make one sample",
                run=blocks.window,
                params=Window,
                warm_up=blocks.window_warm_up,
                warm_up_rule="length - 1",
            ),
        )
    }
)


class ParamOut(BaseModel):
    name: str = Field(examples=["length"])
    type: Literal["integer", "number", "string", "boolean"] = Field(
        description="The JSON type of the value"
    )
    description: str | None = Field(examples=["How many rows make one sample"])
    required: bool = Field(description="The manifest must give it; else `default` applies")
    default: int | float | str | bool | None = Field(
        description="The value without one in the manifest; null when it is required"
    )
    minimum: int | float | None = Field(description="The smallest value allowed", examples=[1])
    maximum: int | float | None = Field(description="The largest value allowed", examples=[1000])
    exclusive_minimum: int | float | None = Field(description="Values must be above it")
    exclusive_maximum: int | float | None = Field(description="Values must be below it")
    options: list[str] | None = Field(
        description="The values allowed, for a choice", examples=[["closed", "current"]]
    )
    tunable: bool = Field(
        description="A user may change it on a trained model (a later experiment mode)"
    )


class BlockOut(BaseModel):
    id: str = Field(description="The versioned name a manifest uses", examples=["candles@1"])
    step: Step = Field(description="The step of the input the block is for")
    description: str = Field(examples=["Every candle a row"])
    warm_up: str = Field(
        description=(
            "How many earlier candles the block needs before its first complete value (rows, "
            "for the window), in its parameters"
        ),
        examples=["0", "length - 1"],
    )
    params: list[ParamOut] = Field(description="Its parameters, in the order of the block")


class CatalogOut(BaseModel):
    blocks: list[BlockOut] = Field(description="Every block, by step in the order they apply")


_STEP_ORDER: tuple[Step, ...] = ("representation", "feature", "normalization", "window")


def _param(name: str, schema: dict[str, Any], *, required: bool, tunable: bool) -> ParamOut:
    """A parameter as the app shows it, from the JSON Schema of its field."""
    options = schema.get("enum")
    return ParamOut(
        name=name,
        type=schema.get("type", "string"),
        description=schema.get("description"),
        required=required,
        default=schema.get("default"),
        minimum=schema.get("minimum"),
        maximum=schema.get("maximum"),
        exclusive_minimum=schema.get("exclusiveMinimum"),
        exclusive_maximum=schema.get("exclusiveMaximum"),
        options=[str(option) for option in options] if options is not None else None,
        tunable=tunable,
    )


def _describe(block: Block) -> BlockOut:
    schema = block.params.model_json_schema()
    required = set(schema.get("required", ()))
    return BlockOut(
        id=block.id,
        step=block.step,
        description=block.description,
        warm_up=block.warm_up_rule,
        params=[
            _param(name, prop, required=name in required, tunable=name in block.tunable)
            for name, prop in schema.get("properties", {}).items()
        ],
    )


def describe_catalog() -> CatalogOut:
    """The catalog as the app reads it: every block with its parameters, by step."""
    ordered = sorted(CATALOG.values(), key=lambda block: _STEP_ORDER.index(block.step))
    return CatalogOut(blocks=[_describe(block) for block in ordered])
