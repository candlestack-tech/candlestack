"""The manifest (version 1): what a model takes, as data.

It names every step by a block of the catalog and that block's parameters, so the schema stays
the same when blocks are added: it checks the form of the document only. Which blocks exist
and what their parameters are is the catalog's, checked as a separate step
(``check_manifest``), so a manifest of blocks still to come parses and is reported as not
runnable instead of invalid.

Every model is strict: a string is never read as a number ("25" is not 25), and an unknown
field is refused rather than ignored, since a manifest that says more than CandleStack reads
would describe an input it does not build.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, FailFast, Field, field_validator
from pydantic_core import PydanticCustomError

# The only version of the manifest so far: a manifest of any other is refused.
VERSION = 1

# The timeframes of the market data (docs/data.md#timeframes); data is not imported here.
Timeframe = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]

# A catalog name and its version: `rsi@1`, `renko-triples@1`.
BLOCK_ID = r"^[a-z][a-z0-9]*(-[a-z0-9]+)*@[1-9][0-9]*$"

# The longest window: a run loads the window's warm-up before its first day, and 1000 rows of
# 30-minute candles are about 77 trading days already.
WINDOW_MAX = 1000

# Caps on what a manifest lists, far above any real model's, so the checks of a large document
# stay a few sentences: the columns, the parameters of a block and the axes of a shape.
FEATURES_MAX = 256
PARAMS_MAX = 32
AXES_MAX = 8

# The time of a row (``ts``) is a column of the rows next to the features.
RESERVED_NAMES = frozenset({"ts"})

NONE = "none@1"

BlockId = Annotated[
    str,
    Field(
        pattern=BLOCK_ID,
        max_length=64,
        description="A block of the catalog and its version",
        examples=["candles@1"],
    ),
]
Params = Annotated[
    dict[str, Any],
    Field(max_length=PARAMS_MAX, description="The block's parameters, checked against the catalog"),
]
Number = Annotated[float, Field(allow_inf_nan=False)]
# A list stops at its first wrong item: an example full of NaN is one error, not one per value.
Row = Annotated[list[Number], FailFast()]
Sample = Annotated[list[Row], FailFast()]


class _Strict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class BlockRef(_Strict):
    """One step: a block of the catalog with its parameters."""

    id: BlockId
    params: Params = Field(default_factory=dict)


class Feature(_Strict):
    """One column of the input: its name, the block that computes it and its normalization."""

    name: str = Field(
        min_length=1, max_length=64, description="The column's name, unique in the manifest"
    )
    id: BlockId
    params: Params = Field(default_factory=dict)
    norm: BlockRef = Field(
        default_factory=lambda: BlockRef(id=NONE),
        description=f"How the column is scaled; without one it is not ({NONE})",
    )


class Window(_Strict):
    """How rows make one sample: the parameters of the catalog's ``window@1``."""

    length: int = Field(ge=1, le=WINDOW_MAX, description="How many rows make one sample")
    last: Literal["closed", "current"] = Field(
        default="closed",
        description=(
            "The last row of a sample: the last finished one (`closed`), or the row still "
            "forming (`current`, for representations with rows that span several candles)"
        ),
    )


class Input(_Strict):
    """The candles the model expects and how its input is built from them."""

    timeframe: Timeframe
    session: Literal["regular", "24/7"] = Field(
        description="`regular`: US trading hours; `24/7`: around the clock, as crypto trades"
    )
    representation: BlockRef = Field(description="Which rows the model sees")
    features: Annotated[list[Feature], FailFast()] = Field(
        min_length=1, max_length=FEATURES_MAX, description="The columns, in their order"
    )
    window: Window


class Tensor(_Strict):
    """The shape of one sample without the batch axis, and its type."""

    shape: Annotated[list[Annotated[int, Field(ge=1)]], FailFast()] = Field(
        min_length=1, max_length=AXES_MAX, examples=[[25, 5]]
    )
    dtype: Literal["float32"]


class Output(Tensor):
    meaning: Literal["score"] = Field(
        description="`score`: the sign is the direction, the size is compared with the threshold"
    )


class Signature(_Strict):
    """What goes into the model and what comes out; must equal what the model file says."""

    input: Tensor
    output: Output


class Example(_Strict):
    """A small input with the output the model must give for it (within 1e-4)."""

    input: Annotated[list[Sample], FailFast()] = Field(
        min_length=1, description="Samples of the shape of `signature.input`"
    )
    output: Annotated[list[Row], FailFast()] = Field(
        min_length=1, description="The model's output for each sample"
    )


class Manifest(_Strict):
    """What a model takes and gives, stored with the model (docs/preprocessing.md)."""

    model_config = ConfigDict(title="CandleStack model manifest")

    version: Literal[1] = Field(description="The version of the manifest's form")
    input: Input
    signature: Signature
    example: Example | None = None
    about: dict[str, str] = Field(
        default_factory=dict,
        description="Free text for people: trained on which instruments, which period, notes",
    )

    @field_validator("version", mode="before")
    @classmethod
    def _version_is_an_integer(cls, value: object) -> object:
        # Literal[1] alone takes true and 1.0 for 1, and the manifest is strict.
        if not isinstance(value, int) or isinstance(value, bool):
            raise PydanticCustomError("int_type", "Input should be a valid integer")
        return value
