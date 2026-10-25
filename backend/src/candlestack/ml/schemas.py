"""Request and response bodies of the models API."""

import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The name of a model without its extension, as the Rename dialog edits it.
NAME_MAX = 60
# Limits of the uploaded files (binary megabytes, as the app shows them).
MB = 1024 * 1024
FILE_MAX_BYTES = 50 * MB
# A manifest is a few KB; its example makes it larger, and 1 MB still holds two samples of
# 1000 rows by 23 columns.
MANIFEST_MAX_BYTES = 1 * MB
QUOTA_BYTES = 100 * MB
# Models a user may keep.
MODELS_MAX = 100
# Unicode categories a name never holds: control and format characters (bidirectional
# overrides, zero-width spaces), which make a name read other than it is.
_INVISIBLE = frozenset({"Cc", "Cf"})


def without_invisible(text: str) -> str:
    """`text` without control and format characters."""
    return "".join(char for char in text if unicodedata.category(char) not in _INVISIBLE)


class Framework(StrEnum):
    """The framework a model file belongs to; only Keras is accepted for now."""

    keras = "keras"


class ModelCheck(StrEnum):
    """The checks of an upload, in the order they run: `opens` (a real model of its format with
    no custom code inside), then `weights` (it holds trained weights)."""

    opens = "opens"
    weights = "weights"


class ModelState(StrEnum):
    """How far a model got: `needs_setup` (no manifest yet, so it cannot run), `ready` (its
    manifest passed every check that reads the files, against this model too) or `verified`
    (a trial run in the worker gave the output of the manifest's example)."""

    needs_setup = "needs_setup"
    ready = "ready"
    verified = "verified"


class BlockUse(BaseModel):
    id: str = Field(description="A block of the catalog and its version", examples=["candles@1"])
    params: dict[str, Any] = Field(
        description="Its parameters as the manifest gives them", examples=[{}]
    )


class WarmUpOut(BaseModel):
    candles: int = Field(
        description="Candles a backtest loads before the first sample", examples=[24]
    )
    kind: Literal["exact", "estimate"] = Field(
        description=(
            "`exact` when every candle is a row; `estimate` with rows that span several "
            "candles (Renko), where the run loads more and reports what the warm-up used"
        )
    )


class ManifestSummary(BaseModel):
    """What the model's manifest says, in short; the whole manifest is
    `GET /api/v1/models/{model_id}/manifest`."""

    representation: BlockUse = Field(description="Which rows the model sees")
    timeframe: str = Field(description="The timeframe of the candles it reads", examples=["30m"])
    session: str = Field(
        description="`regular` (US trading hours) or `24/7` (around the clock)",
        examples=["regular"],
    )
    input_shape: list[int] = Field(
        description="One sample: rows by columns (the features, in their order)",
        examples=[[25, 5]],
    )
    warm_up: WarmUpOut


class ModelOut(BaseModel):
    id: str = Field(description="Public ID of the model", examples=["mdl_7f3k2m9qx4"])
    name: str = Field(
        description="File name, extension included; Rename changes only the part before it",
        examples=["my-tsmixer.keras"],
    )
    framework: Framework
    size_bytes: int = Field(description="Size of the file", examples=[14_889_779])
    parameters: int = Field(description="Number of weights in the file", examples=[1_240_000])
    input_shape: list[int] | None = Field(
        description=(
            "Shape of one input sample without the batch dimension, e.g. 25 candles by 23 "
            "features; null when the file does not say"
        ),
        examples=[[25, 23]],
    )
    output_shape: list[int] | None = Field(
        description="Shape of one output without the batch dimension; null when unknown",
        examples=[[1]],
    )
    starred: bool = Field(description="The user starred the model")
    backtest_count: int = Field(
        description="Backtests that used the model, failed ones included; 0 until backtests exist",
        examples=[0],
    )
    best_sharpe: float | None = Field(
        description="Best Sharpe ratio of its finished backtests; null without one",
        examples=[None],
    )
    created_at: datetime = Field(description="When the model was uploaded (UTC)")
    state: ModelState
    manifest_summary: ManifestSummary | None = Field(
        description="What its manifest says, in short; null without one"
    )
    check_error: str | None = Field(
        description=(
            "Why the trial run of its manifest in the worker failed, in plain sentences; null "
            "when it passed or has not run (the model stays `ready`)"
        ),
        examples=[None],
    )


class StorageUsage(BaseModel):
    used_bytes: int = Field(description="Bytes taken by the user's uploaded models", examples=[0])
    quota_bytes: int = Field(
        description="Bytes the user's uploaded models may take", examples=[QUOTA_BYTES]
    )
    file_max_bytes: int = Field(
        description="Largest file an upload takes", examples=[FILE_MAX_BYTES]
    )


class ModelsOut(BaseModel):
    models: list[ModelOut] = Field(description="The user's models, oldest first")
    usage: StorageUsage


class ModelUpdate(BaseModel):
    """What Rename and the star change; a field left out stays as it is."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=NAME_MAX,
        pattern=r"^[^/\\\x00-\x1f\x7f]+$",
        description=(
            "New name without the extension, which stays; no slashes, control or format "
            "characters (Unicode Cc and Cf)"
        ),
        examples=["tsmixer-v2"],
    )
    starred: bool | None = Field(default=None, description="Star or unstar the model")

    @field_validator("name")
    @classmethod
    def _visible(cls, name: str | None) -> str | None:
        if name is not None and without_invisible(name) != name:
            raise ValueError("must not hold control or format characters")
        return name
