"""Request and response bodies of the models API."""

import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from candlestack.preprocessing import Check

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
# How long an upload link takes its file.
UPLOAD_LINK_MINUTES = 15
# Unicode categories a name never holds: control and format characters (bidirectional
# overrides, zero-width spaces), which make a name read other than it is.
_INVISIBLE = frozenset({"Cc", "Cf"})


def without_invisible(text: str) -> str:
    """`text` without control and format characters."""
    return "".join(char for char in text if unicodedata.category(char) not in _INVISIBLE)


class Framework(StrEnum):
    """The format of a model file: Keras (`.keras`) or ONNX (`.onnx`)."""

    keras = "keras"
    onnx = "onnx"


# The extension of each format's files, in lowercase: of the uploaded file and of the file the
# model storage keeps.
SUFFIXES = {Framework.keras: ".keras", Framework.onnx: ".onnx"}


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


class CheckPart(StrEnum):
    """The part of the worker's check of a model with its manifest that failed: `trial_run`
    (the run on built-in candles) or `example` (the output for the manifest's example is not
    the expected one, after a trial run that passed)."""

    trial_run = "trial_run"
    example = "example"


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
    checked_at: datetime | None = Field(
        description=(
            "When the worker's check of its manifest (the trial run, then the example) came to "
            "an end, passed or failed (UTC); null while it has not, as without a manifest"
        ),
        examples=[None],
    )
    check_failed: CheckPart | None = Field(
        description="Which part of that check failed; null when it passed or has not run",
        examples=[None],
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


class ModelUploadIn(BaseModel):
    """The file an upload link is made for."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        # A path is fine: the model gets its base name, cut as an upload's name is.
        max_length=4096,
        description="Name of the file, extension included (`.keras` or `.onnx`), or its path; "
        "the model gets the base name, as an upload's name",
        examples=["lstm-signals.keras"],
    )
    size_bytes: int = Field(
        gt=0,
        description="Size of the file in bytes: the link takes no larger file",
        examples=[14_889_779],
    )


class UploadStatus(StrEnum):
    """Where an upload through a link stands: `waiting` for the file, `checking` while it comes
    in and is checked, `done` (the model is made), `failed` (`problem` says why) or `expired`
    (the link was not used in time)."""

    waiting = "waiting"
    checking = "checking"
    done = "done"
    failed = "failed"
    expired = "expired"


class ModelUploadOut(BaseModel):
    id: str = Field(description="Public ID of the upload link", examples=["upl_7f3k2m9qx4"])
    name: str = Field(
        description="The name the model gets; a name the user has already gets a number then",
        examples=["lstm-signals.keras"],
    )
    size_bytes: int = Field(description="The largest file the link takes", examples=[14_889_779])
    status: UploadStatus
    created_at: datetime = Field(description="When the link was made (UTC)")
    expires_at: datetime = Field(description="Until when the link takes its file (UTC)")
    finished_at: datetime | None = Field(
        description="When the upload ended, `done` or `failed` (UTC); null before",
        examples=[None],
    )
    model_id: str | None = Field(
        description="The model the upload made, once `done`; null before",
        examples=[None],
    )
    problem: str | None = Field(
        description="The problem the upload answered once `failed` (`model-check-failed`, "
        "`storage-full`, ...); null otherwise",
        examples=[None],
    )
    check: ModelCheck | None = Field(
        description="The check that failed, for `model-check-failed`; null otherwise",
        examples=[None],
    )
    error: str | None = Field(
        description="Why the upload failed and what to do, in plain words; null otherwise",
        examples=[None],
    )


class ModelUploadCreated(ModelUploadOut):
    """A new upload link with its URL, which only this response shows."""

    url: str = Field(
        repr=False,  # it holds the token
        description="Where the file goes: `PUT` it there as the body, with no other sign-in. "
        "Shown only in this response: CandleStack keeps just the SHA-256 of its token",
        examples=["https://app.candlestack.tech/api/v1/uploads/" + "x" * 40],
    )
    page_url: str = Field(
        repr=False,  # it holds the token
        description="The upload page of the link in the app: a person signed in as the user "
        "drops the file there in the browser, for a client that cannot run `command`. Shown only "
        "in this response",
        examples=["https://app.candlestack.tech/upload/" + "x" * 40],
    )
    command: str = Field(
        repr=False,
        description="The `curl` command that uploads the file from the folder it is in; it "
        "exits with an error and prints the problem when the upload is refused",
        examples=[
            "curl --fail-with-body -T lstm-signals.keras https://app.candlestack.tech/api/v1/uploads/"
            + "x" * 40
        ],
    )


class ManifestProblemOut(BaseModel):
    """One thing wrong with a manifest, in a form to fix it from."""

    check: Check = Field(
        description="The check that found it: `schema` (the form), `catalog` (the blocks and "
        "their steps), `params` (a block's parameters), `names` (the columns' names), "
        "`signature` (against the window and the columns), `example` (its shapes) or `model` "
        "(against the input and output the upload read from the model file)"
    )
    field: str | None = Field(
        description="The field of the manifest it is about, as a path; null when it is about "
        "the whole document",
        examples=["input.features[2].id"],
    )
    detail: str = Field(
        description="What is wrong, in one sentence, and sometimes a second that says what to do",
        examples=["The catalog has no block pocket-pivot@1 yet, so it cannot run."],
    )
    allowed: list[Any] | None = Field(
        description="The values that fit in `field` when the check knows them: the blocks of "
        "the step, the fields or parameters of the object, the options of a choice, the shape "
        "to give; empty when nothing fits (a block without parameters); null when the check "
        "does not say",
        examples=[["close@1", "rsi@1"]],
    )


class ManifestCheckOut(BaseModel):
    """The result of checking a manifest without keeping it."""

    valid: bool = Field(
        description="Every check passed: setting this manifest would make the model `ready`"
    )
    problems: list[ManifestProblemOut] = Field(
        description="What is wrong, at most 10; empty when it is valid"
    )
    more_problems: int = Field(description="Problems found beyond those listed", examples=[0])
    unknown_blocks: list[str] = Field(
        description="The blocks it names that the catalog does not have", examples=[[]]
    )
