"""The tools about the user's models: the list and one model, the manifest (read, check without
keeping, set), rename, star and delete, and the upload of a file through a one-time link.

They call the models service the routes use and answer its errors with the routes' problems.
A file never goes through a tool: ``create_model_upload`` makes the link, and the file goes
from the user's disk to it with ``curl`` or through the upload page of the app.
"""

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.accounts import Scope
from candlestack.core import ProblemError
from candlestack.experiments import (
    BacktestFilters,
    BacktestSort,
    BacktestStatus,
    ListQuery,
    SortOrder,
    get_backtests_service,
)
from candlestack.mcp.apps import attach, shows_widget, tool_meta
from candlestack.mcp.convention import (
    Example,
    ToolCall,
    ToolSpec,
    backtest_url,
    changes,
    model_url,
    reads,
    tool_call,
)
from candlestack.mcp.docs import MANIFEST_DOCS, MANIFEST_SCHEMA
from candlestack.ml import (
    LINK_REFUSALS,
    MANIFEST_MAX_BYTES,
    MODELS_MAX,
    NAME_MAX,
    Framework,
    ManifestCheckFailed,
    ManifestCheckOut,
    ManifestProblemOut,
    ManifestSummary,
    ManifestTooLarge,
    ModelChecks,
    ModelNameTaken,
    ModelNotFound,
    ModelOut,
    ModelsService,
    ModelState,
    ModelUpdate,
    ModelUploadIn,
    NoManifest,
    UploadNotFound,
    UploadStatus,
    get_models_service,
    manifest_check_failed,
    manifest_checked,
    manifest_too_large,
    model_not_found,
    no_manifest,
    rename_taken,
    upload_curl,
    upload_not_found,
    upload_problem,
)
from candlestack.preprocessing import Manifest, parse_manifest

logger = logging.getLogger(__name__)

ModelId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        description="As list_models gives it",
        examples=["mdl_7f3k2m9qx4"],
    ),
]
ManifestDocument = Annotated[
    dict[str, Any],
    Field(
        description="The manifest, version 1: `version`, `input` (timeframe, session, "
        "representation, features with their normalization, window), `signature` (input and "
        "output shapes), optionally `example` and `about`. At most "
        f"{MANIFEST_MAX_BYTES // 1024 // 1024} MB as JSON"
    ),
]


def _service(call: ToolCall, db: AsyncSession) -> ModelsService:
    """The models service the routes use, on the call's session."""
    state = call.state
    return get_models_service(db, state.redis, state.model_storage, state.model_usage)


def _checks(call: ToolCall) -> ModelChecks:
    """The checks of the models in the worker, which ``candlestack.main`` puts on the app."""
    return call.state.model_checks


async def _chunks(document: dict[str, Any]) -> AsyncIterator[bytes]:
    """The manifest as the JSON text a route reads, in one chunk."""
    yield json.dumps(document, ensure_ascii=False).encode()


# The most backtests of a model the widget shows, the best by the Sharpe ratio.
WIDGET_BACKTESTS = 6


class ModelBrief(BaseModel):
    """A model of the user, in short."""

    id: str = Field(description="Public ID of the model", examples=["mdl_7f3k2m9qx4"])
    name: str = Field(description="File name, extension included", examples=["lstm.keras"])
    framework: Framework
    state: ModelState
    starred: bool = Field(description="The user starred the model")
    timeframe: str | None = Field(
        description="The timeframe of the candles its manifest reads; null without a manifest",
        examples=["30m"],
    )
    session: str | None = Field(
        description="`regular` (US trading hours) or `24/7` (crypto); null without a manifest",
        examples=["regular"],
    )
    backtest_count: int = Field(description="Backtests that used it, failed ones included")
    best_sharpe: float | None = Field(
        description="Best Sharpe ratio of its finished backtests; null without one"
    )
    created_at: datetime = Field(description="When it was uploaded (UTC)")
    url: str = Field(description="The model's page in the app, to show the user")


def _brief(call: ToolCall, model: ModelOut) -> ModelBrief:
    summary = model.manifest_summary
    return ModelBrief(
        id=model.id,
        name=model.name,
        framework=model.framework,
        state=model.state,
        starred=model.starred,
        timeframe=None if summary is None else summary.timeframe,
        session=None if summary is None else summary.session,
        backtest_count=model.backtest_count,
        best_sharpe=model.best_sharpe,
        created_at=model.created_at,
        url=model_url(call, model.id),
    )


class ModelDetail(ModelOut):
    """One of the user's models in full, with its page in the app."""

    url: str = Field(description="The model's page in the app, to show the user")


def _detail(call: ToolCall, model: ModelOut) -> ModelDetail:
    return ModelDetail(**model.model_dump(), url=model_url(call, model.id))


class ModelsResult(BaseModel):
    """The user's models and the storage they take."""

    models: list[ModelBrief] = Field(description="Oldest first; the page asked for, else all")
    total: int = Field(description="All the user's models")
    used_bytes: int = Field(description="Bytes the models take")
    quota_bytes: int = Field(description="Bytes the user's models may take")
    file_max_bytes: int = Field(description="The largest file an upload takes")


async def list_models(
    ctx: Context,
    *,
    page: Annotated[int, Field(ge=1, description="With per_page")] = 1,
    per_page: Annotated[
        int | None,
        Field(
            ge=1,
            le=MODELS_MAX,
            description="All of them when left out",
        ),
    ] = None,
) -> ModelsResult:
    """The user's models in short, oldest first, and the storage they take. State:
    `needs_setup` has no manifest and cannot run (set_manifest); `ready` can be backtested;
    `verified` also passed the trial run of its manifest's example. One in full: get_model."""
    call = tool_call(ctx)
    offset = 0 if per_page is None else (page - 1) * per_page
    async with call.db() as db:
        listed = await _service(call, db).models_page(call.user.id, offset=offset, limit=per_page)
    return ModelsResult(
        models=[_brief(call, model) for model in listed.models],
        total=listed.total,
        used_bytes=listed.usage.used_bytes,
        quota_bytes=listed.usage.quota_bytes,
        file_max_bytes=listed.usage.file_max_bytes,
    )


async def _model(call: ToolCall, model_id: str) -> ModelOut:
    """The user's model with the ID, as the list shows it.

    Raises:
        ProblemError: 404 ``not-found``.
    """
    async with call.db() as db:
        try:
            return await _service(call, db).model(call.user.id, model_id)
        except ModelNotFound:
            raise model_not_found() from None


async def get_model(model_id: ModelId, ctx: Context) -> ModelDetail:
    """One of the user's models in full: file, state, its manifest in short, `backtest_count`
    and their best Sharpe ratio. Its backtests: list_backtests with `model` set to its name.

    After set_manifest, the trial run: ended when `checked_at` is set, passed when `state` is
    `verified`, else `check_failed` and `check_error` say why (the model stays `ready`)."""
    call = tool_call(ctx)
    detail = _detail(call, await _model(call, model_id))
    if shows_widget(ctx):
        attach(ctx, await _model_chart(call, detail.id))
    return detail


async def _model_chart(call: ToolCall, model_id: str) -> dict[str, Any]:
    """What the widget shows of a model beyond get_model's answer: its best finished backtests
    by the Sharpe ratio with their returns, and the best one's decision rule as the last step
    of the model's pipeline."""
    query = ListQuery(
        sort=BacktestSort.sharpe,
        order=SortOrder.desc,
        per_page=WIDGET_BACKTESTS,
        more=BacktestFilters(statuses=(BacktestStatus.finished,), model_id=model_id),
    )
    async with call.db() as db:
        found = await get_backtests_service(db).find(call.user.id, query)
    best = found.backtests[0].setup if found.backtests else None
    return {
        "backtests": [
            {
                "id": backtest.id,
                "name": backtest.name,
                "symbols": [instrument.symbol for instrument in backtest.instruments],
                "total_return": backtest.total_return,
                "sharpe": backtest.sharpe,
                "url": backtest_url(call, backtest.id),
            }
            for backtest in found.backtests
        ],
        "decision": None
        if best is None
        else {"threshold": best.threshold, "direction": best.direction.value},
    }


class ManifestResult(BaseModel):
    """A model's manifest."""

    manifest: Manifest = Field(
        description="The manifest, its defaults filled in; `example` is null when it is left "
        "out (see `example_included`)"
    )
    example_samples: int = Field(
        description="How many samples the manifest's example has; 0 without an example"
    )
    example_included: bool = Field(
        description="The example is in `manifest`; false when it was left out to keep the "
        "answer short or the manifest has none"
    )


async def get_manifest(
    model_id: ModelId,
    ctx: Context,
    *,
    include_example: Annotated[
        bool,
        Field(description="Include the example, which can be large"),
    ] = False,
) -> ManifestResult:
    """The manifest of one of the user's models: how its input is built from candles and the
    shapes of its input and output. set_manifest replaces the whole manifest: to change one and
    keep its example, read it with include_example true and send the example back."""
    call = tool_call(ctx)
    async with call.db() as db:
        try:
            manifest = await _service(call, db).manifest(call.user.id, model_id)
        except ModelNotFound:
            raise model_not_found() from None
        except NoManifest:
            raise no_manifest() from None
    samples = 0 if manifest.example is None else len(manifest.example.input)
    if not include_example:
        manifest = manifest.model_copy(update={"example": None})
    return ManifestResult(
        manifest=manifest,
        example_samples=samples,
        example_included=include_example and samples > 0,
    )


async def validate_manifest(
    model_id: ModelId, manifest: ManifestDocument, ctx: Context
) -> ManifestCheckOut:
    """Runs every check of set_manifest on a manifest for one of the user's models and keeps
    nothing. Unless `valid`, each of `problems` names its `field`, the `detail` and the
    `allowed` values: fix those and check again, then set_manifest. The format and every
    check: get_docs page manifest."""
    call = tool_call(ctx)
    async with call.db() as db:
        try:
            await _service(call, db).check_manifest(call.user.id, model_id, None, _chunks(manifest))
        except ModelNotFound:
            raise model_not_found() from None
        except ManifestTooLarge:
            raise manifest_too_large() from None
        except ManifestCheckFailed as failed:
            return manifest_checked(failed)
    return manifest_checked(None)


async def set_manifest(model_id: ModelId, manifest: ManifestDocument, ctx: Context) -> ModelDetail:
    """Gives one of the user's models its manifest when every check of validate_manifest
    passes; the model is then `ready`. A failing one answers manifest-check-failed with
    `problems` as validate_manifest names them. The whole manifest is replaced, the example
    included. A trial run follows in the background: see it with get_model."""
    call = tool_call(ctx)
    async with call.db() as db:
        try:
            model = await _service(call, db).set_manifest(
                call.user.id, model_id, None, _chunks(manifest)
            )
        except ModelNotFound:
            raise model_not_found() from None
        except ManifestTooLarge:
            raise manifest_too_large() from None
        except ManifestCheckFailed as failed:
            failed_checks = ", ".join(dict.fromkeys(problem.check for problem in failed.problems))
            logger.info("A manifest of user %s was refused (%s)", call.user.id, failed_checks)
            raise manifest_check_failed(failed) from None
    _checks(call).start(call.user.id, model.id)
    return _detail(call, model)


async def _update(call: ToolCall, model_id: str, update: ModelUpdate) -> ModelBrief:
    async with call.db() as db:
        try:
            model = await _service(call, db).update(call.user.id, model_id, update)
        except ModelNotFound:
            raise model_not_found() from None
        except ModelNameTaken as taken:
            raise rename_taken(taken) from None
    return _brief(call, model)


async def rename_model(
    model_id: ModelId,
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=NAME_MAX,
            pattern=r"^[^/\\\x00-\x1f\x7f]+$",
            description="Without the extension, which stays",
            examples=["lstm-v2"],
        ),
    ],
    ctx: Context,
) -> ModelBrief:
    """Renames one of the user's models; its extension stays (`lstm-v2` makes
    `lstm-v2.keras`). A name of another of the user's models, in any case, answers
    model-name-taken."""
    try:
        update = ModelUpdate(name=name)
    except ValidationError as error:
        raise ProblemError(
            422,
            "validation",
            "Invalid arguments",
            f"argument 'name': {error.errors(include_url=False)[0]['msg']}",
        ) from None
    return await _update(tool_call(ctx), model_id, update)


async def star_model(
    model_id: ModelId,
    ctx: Context,
    *,
    starred: Annotated[bool, Field(description="false unstars it")] = True,
) -> ModelBrief:
    """Stars or unstars one of the user's models."""
    return await _update(tool_call(ctx), model_id, ModelUpdate(starred=starred))


class DeletedModel(BaseModel):
    """The model that was deleted."""

    id: str = Field(description="Public ID of the model", examples=["mdl_7f3k2m9qx4"])
    name: str = Field(description="Its file name", examples=["lstm.keras"])
    freed_bytes: int = Field(description="The storage its file took, now free")


async def delete_model(
    model_id: ModelId,
    confirm_name: Annotated[
        str,
        Field(
            min_length=1,
            description="The model's name with its extension, exactly as list_models gives it",
            examples=["lstm.keras"],
        ),
    ],
    ctx: Context,
) -> DeletedModel:
    """Deletes one of the user's models and its file for good; its backtests stay. It cannot
    be undone: ask the user first. A confirm_name that is not the model's answers
    confirmation-mismatch and deletes nothing."""
    call = tool_call(ctx)
    model = await _model(call, model_id)
    if confirm_name != model.name:
        raise ProblemError(
            409,
            "confirmation-mismatch",
            "Confirmation mismatch",
            f"confirm_name is not the name of the model {model_id}, so nothing was deleted. "
            "Give its name exactly as list_models shows it, extension included.",
        )
    async with call.db() as db:
        try:
            await _service(call, db).delete(call.user.id, model_id)
        except ModelNotFound:
            raise model_not_found() from None
    return DeletedModel(id=model.id, name=model.name, freed_bytes=model.size_bytes)


# What the command of a link names instead of the folder of a file given by its name alone.
_PLACEHOLDER = "./path/to/"


def _local_path(file_name: str) -> str:
    """The file's path for curl: the one given, or the placeholder's folder for a bare name."""
    return file_name if "/" in file_name or "\\" in file_name else _PLACEHOLDER + file_name


class UploadLink(BaseModel):
    """A one-time link to upload a model file through."""

    id: str = Field(
        description="Public ID of the link, for get_model_upload", examples=["upl_7f3k2m9qx4"]
    )
    name: str = Field(description="The name the model gets", examples=["lstm.keras"])
    size_bytes: int = Field(description="The largest file the link takes")
    expires_at: datetime = Field(description="Until when the link takes its file (UTC)")
    command: str = Field(
        description="The curl command that uploads the file from the user's computer; it "
        "prints the problem when the upload is refused. It is for a POSIX shell (bash, zsh: "
        "macOS, Linux, WSL, Git Bash), not cmd.exe or PowerShell. The file's local path "
        "follows `-T`: the path given as "
        f"`file_name`, or `{_PLACEHOLDER}<name>` for a bare name, to replace with where the "
        "file is"
    )
    url: str = Field(
        description="Where the file goes with PUT, with no other sign-in; the token in it is "
        "the link's only key until it is used"
    )
    page_url: str = Field(
        description="The app's upload page of the link, where the signed-in user drops the "
        "file in the browser instead"
    )
    next_steps: str = Field(description="What to tell the user, and what to call then")


async def create_model_upload(
    file_name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=4096,
            description="The file's path on the user's computer, or only its name, with the "
            "extension; the model gets the base name",
            examples=["lstm.keras"],
        ),
    ],
    size_bytes: Annotated[
        int,
        Field(gt=0, description="The link takes no larger file"),
    ],
    ctx: Context,
) -> UploadLink:
    """Makes a one-time link to upload a model file (`.keras` or `.onnx`, at most 50 MB). The
    file never goes through a tool: run `command` (curl) on the user's computer, or the user
    opens `page_url` and drops it there. Given only a name, `command` reads ./path/to/<name>:
    replace ./path/to/ with the folder the file is in before it runs.

    The link takes one file within 15 minutes; treat `url` and `command` like a key. Then
    get_model_upload with the link's `id` gives the new model's ID; set_manifest before a
    backtest."""
    call = tool_call(ctx)
    async with call.db() as db:
        try:
            link = await _service(call, db).create_upload(
                call.user.id,
                ModelUploadIn(name=file_name, size_bytes=size_bytes),
                call.settings.public_url,
            )
        except LINK_REFUSALS as error:
            raise upload_problem(call.user.id, error) from None
    return UploadLink(
        id=link.id,
        name=link.name,
        size_bytes=link.size_bytes,
        expires_at=link.expires_at,
        # The path given, or a placeholder of its folder: a bare name is often not where the
        # file is, and curl would fail on it.
        command=f"{upload_curl(_local_path(file_name))} {link.url}",
        url=link.url,
        page_url=link.page_url,
        next_steps=f"Ask the user to upload {link.name} before {link.expires_at:%H:%M} UTC: "
        "either run the command with the file's path on their computer after -T, or open the "
        f"page and drop the file there. Then call get_model_upload with id {link.id}.",
    )


class UploadState(BaseModel):
    """How the upload through a link stands."""

    id: str = Field(description="Public ID of the link", examples=["upl_7f3k2m9qx4"])
    name: str = Field(description="The name the model gets", examples=["lstm.keras"])
    status: UploadStatus
    expires_at: datetime = Field(description="Until when the link takes its file (UTC)")
    model_id: str | None = Field(description="The model the upload made once `done`; else null")
    model_url: str | None = Field(
        description="The new model's page in the app once `done`, to show the user; else null"
    )
    problem: str | None = Field(
        description="The problem the upload answered once `failed` (`model-check-failed`, "
        "`storage-full`, `upload-interrupted`, ...); else null"
    )
    error: str | None = Field(
        description="Why the upload failed and what to do, in plain words; else null"
    )


async def get_model_upload(
    upload_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            description="As create_model_upload gave it",
            examples=["upl_7f3k2m9qx4"],
        ),
    ],
    ctx: Context,
) -> UploadState:
    """How an upload through a one-time link stands: `waiting`, `checking`, `done` (with
    `model_id`), `failed` (with the `error` to tell the user) or `expired`. A failed or expired
    link is spent: make a new one."""
    call = tool_call(ctx)
    async with call.db() as db:
        try:
            upload = await _service(call, db).upload_status(call.user.id, upload_id)
        except UploadNotFound:
            raise upload_not_found() from None
    return UploadState(
        id=upload.id,
        name=upload.name,
        status=upload.status,
        expires_at=upload.expires_at,
        model_id=upload.model_id,
        model_url=None if upload.model_id is None else model_url(call, upload.model_id),
        problem=upload.problem,
        error=upload.error,
    )


_CREATED = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
_LSTM = ModelBrief(
    id="mdl_7f3k2m9qx4",
    name="lstm.keras",
    framework=Framework.keras,
    state=ModelState.verified,
    starred=True,
    timeframe="30m",
    session="regular",
    backtest_count=4,
    best_sharpe=1.42,
    created_at=_CREATED,
    url="https://app.candlestack.tech/models/mdl_7f3k2m9qx4",
)
_MODEL = ModelDetail(
    id="mdl_7f3k2m9qx4",
    name="lstm.keras",
    framework=Framework.keras,
    size_bytes=1_489_779,
    parameters=124_000,
    input_shape=[25, 5],
    output_shape=[1],
    starred=True,
    backtest_count=4,
    best_sharpe=1.42,
    created_at=_CREATED,
    state=ModelState.ready,
    manifest_summary=ManifestSummary.model_validate(
        {
            "representation": {"id": "candles@1", "params": {}},
            "timeframe": "30m",
            "session": "regular",
            "input_shape": [25, 5],
            "warm_up": {"candles": 24, "kind": "exact"},
        }
    ),
    checked_at=None,
    check_failed=None,
    check_error=None,
    url="https://app.candlestack.tech/models/mdl_7f3k2m9qx4",
)
_MANIFEST_EXAMPLE: dict[str, Any] = {
    "version": 1,
    "input": {
        "timeframe": "30m",
        "session": "regular",
        "representation": {"id": "candles@1"},
        "features": [
            {"name": name, "id": f"{name}@1"} for name in ("open", "high", "low", "close", "volume")
        ],
        "window": {"length": 25},
    },
    "signature": {
        "input": {"shape": [25, 5], "dtype": "float32"},
        "output": {"shape": [1], "dtype": "float32", "meaning": "score"},
    },
}
_EXPIRES = datetime(2026, 11, 2, 9, 45, tzinfo=UTC)
_UPLOAD_URL = "https://app.candlestack.tech/api/v1/uploads/" + "x" * 40

TOOLS = (
    ToolSpec(
        list_models,
        name="list_models",
        title="List models",
        details="""
            Each model in short: ID, name, format, state, star, the timeframe and session of
            the manifest, how many backtests used it and their best Sharpe ratio, and its `url`.
            All of them unless per_page asks for a page (at most 100); `total` counts them all.
            `needs_setup` has no manifest yet, so it cannot run (set_manifest gives it one);
            `ready` passed the manifest's checks; `verified` also gave the output of the
            manifest's example in a trial run.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={},
            result=ModelsResult(
                models=[_LSTM],
                total=1,
                used_bytes=1_489_779,
                quota_bytes=100 * 1024 * 1024,
                file_max_bytes=50 * 1024 * 1024,
            ),
        ),
    ),
    ToolSpec(
        get_model,
        name="get_model",
        title="Get a model",
        details="""
            In full: its file (format, size, weights, input and output shapes), its state, what
            its manifest says in short (representation, timeframe, session, input shape,
            warm-up), how many backtests used it (`backtest_count`) and the best Sharpe ratio of
            them, how the trial run of its manifest went, and its `url`. get_manifest gives the
            whole manifest.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(arguments={"model_id": "mdl_7f3k2m9qx4"}, result=_MODEL),
        meta=tool_meta("Reading the model", "Read the model"),
    ),
    ToolSpec(
        get_manifest,
        name="get_manifest",
        title="Get a model's manifest",
        details="""
            The manifest says the timeframe, the session, the representation, the features and
            their normalization, and the window. The example, a small input with the output the
            model must give for it, is left out unless you ask for it. A model without a
            manifest answers the problem no-manifest.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"model_id": "mdl_7f3k2m9qx4"},
            result=ManifestResult(
                manifest=parse_manifest(_MANIFEST_EXAMPLE),
                example_samples=0,
                example_included=False,
            ),
        ),
    ),
    ToolSpec(
        validate_manifest,
        name="validate_manifest",
        title="Check a manifest",
        details=f"""
            A dry run of set_manifest, which changes nothing. The checks: the form, the catalog
            (every block exists and sits in its step), the blocks' parameters, the names of the
            columns, the signature against the window and the columns, the shapes of the
            example, and the signature against the input and output read from the model file.

            Each problem names its `field` (a path such as input.features[2].id), says what is
            wrong in `detail` and, when the check knows them, lists the values that fit there in
            `allowed`; at most 10, `more_problems` counts the rest.

            The manifest's guide: {MANIFEST_DOCS}; its JSON Schema: {MANIFEST_SCHEMA}.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={
                "model_id": "mdl_7f3k2m9qx4",
                "manifest": {
                    **_MANIFEST_EXAMPLE,
                    "input": {**_MANIFEST_EXAMPLE["input"], "session": "us"},
                },
            },
            result=ManifestCheckOut(
                valid=False,
                problems=[
                    ManifestProblemOut(
                        check="schema",
                        field="input.session",
                        detail="input.session: Input should be 'regular' or '24/7'.",
                        allowed=["regular", "24/7"],
                    )
                ],
                more_problems=0,
                unknown_blocks=[],
            ),
        ),
    ),
    ToolSpec(
        set_manifest,
        name="set_manifest",
        title="Set a model's manifest",
        details="""
            It gives the model its first manifest or replaces the one it has: what you leave
            out is gone. A failing manifest answers manifest-check-failed, whose `problems`
            name the `field`, the `detail` and the `allowed` values of each thing wrong: fix them
            and call it again, or try it with validate_manifest first. After the trial run,
            get_model shows the model `verified` when it gave the output of the manifest's
            example, or `check_error` when it failed.""",
        scopes=(Scope.models,),
        # It replaces the manifest the model had.
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"model_id": "mdl_7f3k2m9qx4", "manifest": _MANIFEST_EXAMPLE},
            result=_MODEL,
        ),
    ),
    ToolSpec(
        rename_model,
        name="rename_model",
        title="Rename a model",
        details="""
            The new name has no slashes or control characters; the extension (`.keras` or
            `.onnx`) stays.""",
        scopes=(Scope.models,),
        annotations=changes(destructive=False, idempotent=True),
        example=Example(
            arguments={"model_id": "mdl_7f3k2m9qx4", "name": "lstm-v2"},
            result=_LSTM.model_copy(update={"name": "lstm-v2.keras"}),
        ),
    ),
    ToolSpec(
        star_model,
        name="star_model",
        title="Star a model",
        details="The app lists starred models first in its pickers.",
        scopes=(Scope.models,),
        annotations=changes(destructive=False, idempotent=True),
        example=Example(arguments={"model_id": "mdl_7f3k2m9qx4", "starred": True}, result=_LSTM),
    ),
    ToolSpec(
        delete_model,
        name="delete_model",
        title="Delete a model",
        details="""
            Deleting frees the model's storage. confirm_name is the confirmation that this is
            the model to delete.""",
        scopes=(Scope.models,),
        annotations=changes(destructive=True, idempotent=True),
        example=Example(
            arguments={"model_id": "mdl_7f3k2m9qx4", "confirm_name": "lstm.keras"},
            result=DeletedModel(id="mdl_7f3k2m9qx4", name="lstm.keras", freed_bytes=1_489_779),
        ),
    ),
    ToolSpec(
        create_model_upload,
        name="create_model_upload",
        title="Make an upload link",
        details="""
            Keras `.keras` or ONNX `.onnx`. `command` sends the file at the path given as
            `file_name`, so with a path it runs as it is; with a name, its path is a placeholder
            (the file on disk may have another name than the model gets). The link counts
            against the user's storage, and the file is checked as any upload: a real model with
            trained weights, nothing from it run. Treat `url` and `command` like a key until the
            upload is done.""",
        scopes=(Scope.models,),
        annotations=changes(destructive=False, idempotent=False),
        example=Example(
            arguments={"file_name": "lstm.keras", "size_bytes": 1_489_779},
            result=UploadLink(
                id="upl_7f3k2m9qx4",
                name="lstm.keras",
                size_bytes=1_489_779,
                expires_at=_EXPIRES,
                command=f"curl --fail-with-body -T ./path/to/lstm.keras {_UPLOAD_URL}",
                url=_UPLOAD_URL,
                page_url="https://app.candlestack.tech/upload/" + "x" * 40,
                next_steps="Ask the user to upload lstm.keras before 09:45 UTC: either run the "
                "command with the file's path on their computer after -T, or open the page and "
                "drop the file there. Then call get_model_upload with id upl_7f3k2m9qx4.",
            ),
        ),
    ),
    ToolSpec(
        get_model_upload,
        name="get_model_upload",
        title="Check an upload",
        details="""
            `waiting` for the file, `checking` while it comes in and is checked, `done` with the
            new model's `model_id` and its page in the app (`model_url`), `failed` with the
            `problem` and the `error` to tell the user, or `expired` unused.""",
        scopes=(Scope.read,),
        annotations=reads(),
        example=Example(
            arguments={"upload_id": "upl_7f3k2m9qx4"},
            result=UploadState(
                id="upl_7f3k2m9qx4",
                name="lstm.keras",
                status=UploadStatus.done,
                expires_at=_EXPIRES,
                model_id="mdl_7f3k2m9qx4",
                model_url="https://app.candlestack.tech/models/mdl_7f3k2m9qx4",
                problem=None,
                error=None,
            ),
        ),
    ),
)
