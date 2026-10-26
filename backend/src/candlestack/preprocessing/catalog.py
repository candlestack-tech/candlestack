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

from candlestack.preprocessing import blocks, renko
from candlestack.preprocessing import indicators as ind
from candlestack.preprocessing.manifest import Window

Step = Literal["representation", "feature", "normalization", "window"]
# How the app groups the features of a model when it draws the input as rows by columns.
Group = Literal["Candle", "Bands", "Momentum", "Trend", "Volume", "Brick"]

# The manifest's `input.window` holds the parameters of this block, the only window for now.
WINDOW = "window@1"
RENKO_TRIPLES = "renko-triples@1"


@dataclass(frozen=True, slots=True)
class Block:
    """One block of the catalog.

    - ``run``: the function, in the form of its step (``blocks``);
    - ``params``: the model its parameters are checked with;
    - ``warm_up``: how many earlier candles it needs before its first complete value (rows,
      for the window), from its parameters; ``warm_up_rule`` says it in words for the app;
    - ``group``: a feature's display group; None for the other steps;
    - ``needs_session``: it reads the mark of each session's last candle, which only candles of
      the regular session have, so a manifest of 24/7 candles cannot use it;
    - ``candles_per_row``: for a representation whose rows span several candles (Renko, Kagi),
      an estimate of the candles a row takes, from its parameters; None when every candle is a
      row, which makes the warm-up of a manifest exact;
    - ``of_rows``: for a feature of the rows of a representation rather than of the candles
      (the direction of a Renko triple), that representation: the feature takes its rows;
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
    group: Group | None = None
    needs_session: bool = False
    candles_per_row: Callable[[Any], float] | None = None
    of_rows: str | None = None
    tunable: frozenset[str] = field(default_factory=frozenset)


def _window_warm_up(params: Any) -> int:
    return params.window - 1


def _feature(
    block_id: str,
    group: Group,
    description: str,
    run: Callable[..., Any],
    params: type[BaseModel],
    *,
    warm_up: tuple[Callable[[Any], int], str] = (_window_warm_up, "window - 1"),
) -> Block:
    return Block(
        id=block_id,
        step="feature",
        description=description,
        run=run,
        params=params,
        warm_up=warm_up[0],
        warm_up_rule=warm_up[1],
        group=group,
    )


# The indicators of the BLESK models, in the order of their 25 x 23 input. A recursive one
# (an EMA, Wilder's smoothing) waits until its first candles weigh under 1e-6 (indicators).
_INDICATORS = (
    _feature(
        "bollinger-high@1",
        "Bands",
        "Upper Bollinger band: the mean close plus dev standard deviations",
        ind.bollinger_high,
        ind.Bollinger,
    ),
    _feature(
        "bollinger-low@1",
        "Bands",
        "Lower Bollinger band: the mean close minus dev standard deviations",
        ind.bollinger_low,
        ind.Bollinger,
    ),
    _feature(
        "keltner-high@1",
        "Bands",
        "Upper Keltner channel, original version: the mean of (4 high - 2 low + close) / 3",
        ind.keltner_high,
        ind.Keltner,
    ),
    _feature(
        "keltner-low@1",
        "Bands",
        "Lower Keltner channel, original version: the mean of (4 low - 2 high + close) / 3",
        ind.keltner_low,
        ind.Keltner,
    ),
    _feature(
        "squeeze@1",
        "Bands",
        "1 when the Bollinger bands lie inside a Keltner channel of mean true ranges, else 0",
        ind.squeeze,
        ind.Squeeze,
        warm_up=(
            ind.squeeze_warm_up,
            "max(bollinger_window - 1, keltner_window - 1, keltner_atr_window)",
        ),
    ),
    _feature(
        "rsi@1",
        "Momentum",
        "Relative strength index with Wilder's smoothing",
        ind.rsi,
        ind.Rsi,
        warm_up=(
            ind.rsi_warm_up,
            f"{ind.WILDER_WINDOWS} * window",
        ),
    ),
    _feature(
        "roc@1",
        "Momentum",
        "Rate of change: the close against the close window candles before, in percent",
        ind.roc,
        ind.Roc,
        warm_up=(ind.roc_warm_up, "window"),
    ),
    _feature(
        "tsi@1",
        "Momentum",
        "True strength index: the close's change smoothed by two EMAs, in percent",
        ind.tsi,
        ind.Tsi,
        warm_up=(ind.tsi_warm_up, f"{ind.EMA_WINDOWS} * (slow + fast)"),
    ),
    _feature(
        "atr@1",
        "Bands",
        "Average true range with Wilder's smoothing",
        ind.atr,
        ind.Atr,
        warm_up=(ind.atr_warm_up, f"{ind.WILDER_WINDOWS + 1} * window - 1"),
    ),
    _feature(
        "adx@1",
        "Trend",
        "Average directional index: how strong the trend is, with Wilder's smoothing",
        ind.adx,
        ind.Adx,
        warm_up=(ind.adx_warm_up, f"{ind.ADX_WINDOWS} * window"),
    ),
    _feature(
        "aroon-up@1",
        "Trend",
        "Aroon up on the close: 100 when the highest close of the window is the last",
        ind.aroon_up,
        ind.Aroon,
    ),
    _feature(
        "aroon@1",
        "Trend",
        "Aroon on the close: Aroon up minus Aroon down",
        ind.aroon,
        ind.Aroon,
    ),
    _feature(
        "aroon-down@1",
        "Trend",
        "Aroon down on the close: 100 when the lowest close of the window is the last",
        ind.aroon_down,
        ind.Aroon,
    ),
    _feature(
        "sma@1",
        "Trend",
        "Simple moving average of the close",
        ind.sma,
        ind.Sma,
    ),
    _feature(
        "ema@1",
        "Trend",
        "Exponential moving average of the close",
        ind.ema,
        ind.Ema,
        warm_up=(ind.ema_warm_up, f"{ind.EMA_WINDOWS} * window"),
    ),
    _feature(
        "macd-diff@1",
        "Trend",
        "MACD histogram: the fast EMA minus the slow EMA of the close, minus its signal EMA",
        ind.macd_diff,
        ind.Macd,
        warm_up=(ind.macd_warm_up, f"{ind.EMA_WINDOWS} * (max(fast, slow) + signal)"),
    ),
)


def _candle_column(name: str) -> Block:
    return Block(
        id=f"{name}@1",
        step="feature",
        description=f"The {name} of the candle",
        run=blocks.candle_column(name),
        group="Candle",
    )


# The candle's prices as the BLESK models take them, price-independent (their rel_* columns).
_RELATIVE = (
    *(
        Block(
            id=f"rel-{name}@1",
            step="feature",
            description=f"The {name} of the candle over its close, minus 1",
            run=blocks.relative_to_close(name),
            group="Candle",
        )
        for name in ("open", "high", "low")
    ),
    Block(
        id="rel-close@1",
        step="feature",
        description="The close over the previous candle's close, minus 1",
        run=blocks.close_change,
        warm_up=blocks.previous_candle_warm_up,
        warm_up_rule="1",
        group="Candle",
    ),
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
            Block(
                id=RENKO_TRIPLES,
                step="representation",
                description=(
                    "Rows of three Renko bricks on the close, each brick atr_multiple ATRs high "
                    "at the candle that starts it: one row per candle from the third brick on"
                ),
                run=renko.renko_triples,
                params=renko.RenkoTriples,
                warm_up=renko.warm_up,
                warm_up_rule=(
                    f"{ind.WILDER_WINDOWS + 1} * atr_window - 1 + "
                    f"ceil({renko.BRICKS_PER_ROW - 1} * ({renko.CANDLES_PER_BRICK:g} + "
                    f"{renko.CANDLES_PER_BRICK_PER_ATR:g} * atr_multiple))"
                ),
                candles_per_row=renko.candles_per_row,
            ),
            *(_candle_column(name) for name in blocks.CANDLE_COLUMNS),
            *_RELATIVE,
            Block(
                id="session-last-candle@1",
                step="feature",
                description=(
                    "1 on the last candle of its trading session, early closes included, else 0; "
                    "stock candles only"
                ),
                run=blocks.session_last,
                group="Candle",
                needs_session=True,
            ),
            *_INDICATORS,
            Block(
                id="renko-direction@1",
                step="feature",
                description=(
                    "The direction of a Renko triple: half the middle brick's direction plus a "
                    "quarter of the current one's (-0.75, -0.25, 0.25 or 0.75)"
                ),
                run=renko.direction,
                group="Brick",
                of_rows=RENKO_TRIPLES,
            ),
            Block(
                id="none@1",
                step="normalization",
                description="No normalization: the values as they are",
                run=blocks.none,
            ),
            Block(
                id=WINDOW,
                step="window",
                description=(
                    "The last rows up to each row make one sample; with last current, the "
                    "last finished rows and the row still forming"
                ),
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
    # A string, not an enum: a new group comes with a new block, which must not break clients.
    group: str | None = Field(
        description=(
            "Where the app shows a feature when it draws the input: Candle, Bands, Momentum, "
            "Trend, Volume or Brick; null for the other steps"
        ),
        examples=["Candle"],
    )
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
    # A choice of one value (a Literal of one) is a `const` in JSON Schema.
    options = schema.get("enum", [schema["const"]] if "const" in schema else None)
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
        group=block.group,
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
