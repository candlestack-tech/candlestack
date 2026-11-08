"""HTTP API of the ml module: the signed-in user's models under ``/api/v1/models``, and the
catalog their manifests are made of under ``/api/v1/preprocessing/catalog``.

An upload is the file itself as the request body (``application/octet-stream``) with its name
in ``?name=``, so the server streams it to disk instead of holding a multipart form in memory.
It takes Keras (``.keras``) and ONNX (``.onnx``) files and runs two checks on the file,
``opens`` and ``weights`` (``ModelCheck``), without executing anything from it; a failed check
answers 422 ``model-check-failed`` naming the check.

An agent that cannot send a file uploads it through a one-time link instead: it asks for one
with ``POST /api/v1/models/uploads`` (the file's name and size), the file goes to the link with
``PUT /api/v1/uploads/{token}`` (``curl -T``), whose token is its only sign-in, and
``GET /api/v1/models/uploads/{upload_id}`` says how the upload stands. The upload page the link
opens in the browser reads the link with ``GET /api/v1/uploads/{token}``, for its user's session
only.

A model's manifest is its own resource, ``/api/v1/models/{model_id}/manifest``: added or
replaced with PUT, which keeps it only when every check passed (else 422
``manifest-check-failed`` with the problems) and then starts its trial run in the worker
(``checks.py``), and read back with GET. ``POST .../manifest/check`` runs the same checks as a
dry run and answers the problems, keeping nothing.
"""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from starlette.requests import ClientDisconnect

from candlestack.accounts import (
    API_KEY_SCHEME,
    CallerDep,
    CurrentUserDep,
    ModelsUserDep,
    Scope,
    SessionUserDep,
)
from candlestack.core import (
    NO_STORE,
    ProblemError,
    SettingsDep,
    problem_doc,
    require_same_origin,
)
from candlestack.ml.dependencies import ModelChecksDep, ModelsServiceDep
from candlestack.ml.errors import (
    AccountGone,
    LargerThanLink,
    ManifestCheckFailed,
    ManifestTooLarge,
    ModelCheckFailed,
    ModelNameTaken,
    ModelNotFound,
    NoManifest,
    UploadLinkGone,
    UploadLinkNotFound,
    UploadLinkOfAnotherUser,
    UploadNotFound,
)
from candlestack.ml.problems import (
    LINK_REFUSALS,
    PROBLEMS_SHOWN,
    UPLOAD_PROBLEMS,
    manifest_check_failed,
    manifest_checked,
    manifest_too_large,
    megabytes,
    model_not_found,
    no_manifest,
    rename_taken,
    upload_not_found,
    upload_problem,
)
from candlestack.ml.schemas import (
    MANIFEST_MAX_BYTES,
    UPLOAD_LINK_MINUTES,
    ManifestCheckOut,
    ModelOut,
    ModelsOut,
    ModelUpdate,
    ModelUploadCreated,
    ModelUploadIn,
    ModelUploadOut,
)
from candlestack.ml.service import INTERRUPTED, UploadFailure
from candlestack.preprocessing import CatalogOut, Manifest, describe_catalog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])
# The manifest belongs to a model, which ml checks on upload; preprocessing is plain functions
# and serves no HTTP itself, so its catalog is served here.
preprocessing_router = APIRouter(tags=["preprocessing"])

# The catalog is code: it changes only with a release.
_CATALOG = describe_catalog()


ModelId = Annotated[str, Path(description="Public ID of the model", examples=["mdl_7f3k2m9qx4"])]
UploadId = Annotated[
    str, Path(description="Public ID of the upload link", examples=["upl_7f3k2m9qx4"])
]
UploadToken = Annotated[str, Path(description="The token of the upload link")]
FileName = Annotated[
    str,
    Query(
        min_length=1,
        max_length=255,
        description="Name of the uploaded file, extension included",
        examples=["lstm-signals.keras"],
    ),
]


_NOT_SIGNED_IN = problem_doc(401, "Not signed in (`not-signed-in`).")
_CROSS_ORIGIN = problem_doc(
    403, "The request does not come from the app's own pages (`cross-origin-request`)."
)
_NOT_FOUND = problem_doc(404, "The user has no model with this ID (`not-found`).")
_UPLOAD_BODY = {
    "requestBody": {
        "required": True,
        "description": "The model file",
        "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
    }
}
# The manifest is read by the preprocessing checks, which say what is wrong in plain words,
# rather than by the route's validation.
_MANIFEST_BODY = {
    "requestBody": {
        "required": True,
        "description": "The manifest (docs/preprocessing.md), as JSON",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Manifest"}}},
    }
}


def _declared_size(request: Request) -> int | None:
    """The size the client announced (Content-Length); None without a usable one."""
    value = request.headers.get("content-length", "")
    return int(value) if value.isdecimal() else None


def _larger_than_link(error: Exception) -> ProblemError:
    assert isinstance(error, LargerThanLink)
    return ProblemError(
        413,
        "larger-than-link",
        "Larger than the link",
        f"The file is larger than the {error.size_bytes} bytes this link was made for. Ask for "
        "a new link with the file's size.",
        extensions={"size_bytes": error.size_bytes},
    )


def _link_account_gone(_error: Exception) -> ProblemError:
    return ProblemError(
        410,
        "upload-link-gone",
        "Upload link gone",
        "The account of this link was deleted while the file was uploading, so it was not kept.",
    )


# The problems of an upload through a link that started: those of an upload, the file larger
# than the link, and the account gone, which answers for the link (there is no session here).
_LINK_PROBLEMS: dict[type[Exception], Callable[[Exception], ProblemError]] = {
    **UPLOAD_PROBLEMS,
    LargerThanLink: _larger_than_link,
    AccountGone: _link_account_gone,
}


def _link_failure(error: Exception) -> UploadFailure | None:
    """What the link records of an upload that failed with `error`: the problem it answers."""
    make = _LINK_PROBLEMS.get(type(error))
    if make is None:
        return None
    problem = make(error)
    check = error.check if isinstance(error, ModelCheckFailed) else None
    return UploadFailure(problem.slug, problem.detail, check)


def _link_gone(error: UploadLinkGone) -> ProblemError:
    detail = (
        "This upload link took its file already: a link takes one upload. Ask for a new link."
        if error.reason == "used"
        else f"This upload link expired: a link takes its file within {UPLOAD_LINK_MINUTES} "
        "minutes. Ask for a new link."
    )
    return ProblemError(
        410, "upload-link-gone", "Upload link gone", detail, extensions={"reason": error.reason}
    )


def _link_not_found() -> ProblemError:
    return ProblemError(
        404,
        "upload-link-not-found",
        "Upload link not found",
        "No upload link has this token. Check that the whole link was copied, or ask for a new "
        "one.",
    )


@router.get("/api/v1/models", responses=_NOT_SIGNED_IN)
async def list_models(
    user: CurrentUserDep, service: ModelsServiceDep, response: Response
) -> ModelsOut:
    """The user's uploaded models, oldest first, and the storage they take."""
    response.headers.update(NO_STORE)
    return await service.models(user.id)


@router.post(
    "/api/v1/models",
    status_code=201,
    responses={
        **problem_doc(
            401,
            "Not signed in, or the account was deleted while the file was uploading "
            "(`not-signed-in`).",
        ),
        **_CROSS_ORIGIN,
        **problem_doc(408, "The file did not arrive within 2 minutes (`upload-timeout`)."),
        **problem_doc(
            409,
            "The file does not fit into the user's storage (`storage-full`; `used_bytes` and "
            "`quota_bytes` say how much is taken), the user has 100 models already "
            "(`too-many-models`; `limit`), another upload of the user is in progress "
            "(`upload-in-progress`), or, rarely, the numbered name the file got is one the user "
            "has in another case (`model-name-taken`).",
        ),
        **problem_doc(413, "The file is larger than 50 MB (`model-too-large`)."),
        **problem_doc(
            415,
            "Not a format the app takes: Keras (`.keras`) and ONNX (`.onnx`), never "
            "pickle-based files (`unsupported-model-format`).",
        ),
        **problem_doc(
            422,
            "A check failed (`model-check-failed`): `check` names it (`opens` or `weights`) and "
            "`detail` says why; or the request is invalid (`validation`).",
        ),
    },
    dependencies=[Depends(require_same_origin)],
    openapi_extra=_UPLOAD_BODY,
)
async def upload_model(
    name: FileName,
    request: Request,
    user: ModelsUserDep,
    service: ModelsServiceDep,
    response: Response,
) -> ModelOut:
    """Uploads a model file, Keras (`.keras`) or ONNX (`.onnx`): the body is the file, `name`
    its file name. The file is checked before it is kept: `opens` (a real model of its format
    with no custom code inside, for ONNX the standard operators only, all in the one file, with
    one float32 input and output; nothing from the file is executed) and `weights` (it holds
    trained weights). A file with a name the user already has gets a number, as
    `lstm (2).keras`. The model is `needs_setup` until its manifest is added
    (`PUT /api/v1/models/{model_id}/manifest`)."""
    response.headers.update(NO_STORE)
    try:
        return await service.upload(user.id, name, _declared_size(request), request.stream())
    except tuple(UPLOAD_PROBLEMS) as error:
        raise upload_problem(user.id, error) from None


@router.post(
    "/api/v1/models/uploads",
    status_code=201,
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **problem_doc(
            409,
            "The file does not fit into the user's storage now (`storage-full`; `used_bytes` "
            "and `quota_bytes`), or the user has 100 models already (`too-many-models`; "
            "`limit`).",
        ),
        **problem_doc(413, "The size is larger than 50 MB (`model-too-large`)."),
        **problem_doc(
            415,
            "The name is not that of a Keras (`.keras`) or ONNX (`.onnx`) file "
            "(`unsupported-model-format`).",
        ),
    },
    dependencies=[Depends(require_same_origin)],
)
async def create_model_upload(
    body: ModelUploadIn,
    user: ModelsUserDep,
    service: ModelsServiceDep,
    settings: SettingsDep,
    response: Response,
) -> ModelUploadCreated:
    """Makes a one-time link to upload a model file through, for a client that cannot send
    the file itself (an agent in a chat): `PUT` the file to `url` within 15 minutes, as
    `command` does with `curl -T`, or have the user drop it on the upload page `page_url` in
    the browser. The link takes one upload of a file no larger than `size_bytes`, with no other
    sign-in, and the upload is checked as `POST /api/v1/models` checks it. The URLs are shown
    only here; `GET /api/v1/models/uploads/{upload_id}` says how the upload stands. The name
    and the size must fit now, and are checked again when the file comes."""
    response.headers.update(NO_STORE)
    try:
        return await service.create_upload(user.id, body, settings.public_url)
    except LINK_REFUSALS as error:
        raise upload_problem(user.id, error) from None


@router.get(
    "/api/v1/models/uploads/{upload_id}",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(404, "The user has no upload link with this ID (`not-found`)."),
    },
)
async def get_model_upload(
    upload_id: UploadId, user: CurrentUserDep, service: ModelsServiceDep, response: Response
) -> ModelUploadOut:
    """How the upload through the link stands: `waiting` for the file, `checking` while it
    comes in and is checked, `done` with the model it made (`model_id`), `failed` with the
    problem it answered (`problem`, `check`, `error`), or `expired` unused. A link stays
    readable for at least a day after it expired."""
    response.headers.update(NO_STORE)
    try:
        return await service.upload_status(user.id, upload_id)
    except UploadNotFound:
        raise upload_not_found() from None


@router.get(
    "/api/v1/uploads/{token}",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            403,
            "Another user made this upload link (`upload-link-of-another-user`): sign in with "
            "the account that asked for it.",
        ),
        **problem_doc(404, "No upload link has this token (`upload-link-not-found`)."),
    },
)
async def get_upload_by_token(
    token: UploadToken, user: SessionUserDep, service: ModelsServiceDep, response: Response
) -> ModelUploadOut:
    """The upload link of the token for the upload page it opens in the browser: the file it
    is made for (`name`, `size_bytes`) and how its upload stands, as
    `GET /api/v1/models/uploads/{upload_id}` says it. Only the session of the user who made the
    link reads it: an API key is refused, and another user learns nothing of the link. The file
    itself never comes back."""
    response.headers.update(NO_STORE)
    try:
        return await service.upload_of_token(user.id, token)
    except UploadLinkNotFound:
        raise _link_not_found() from None
    except UploadLinkOfAnotherUser:
        logger.info("User %s opened the upload link of another user", user.id)
        raise ProblemError(
            403,
            "upload-link-of-another-user",
            "Upload link of another account",
            "Another account asked for this upload link. Sign in with the account that asked "
            "for it, then open the link again.",
        ) from None


@router.put(
    "/api/v1/uploads/{token}",
    status_code=201,
    responses={
        **problem_doc(
            400,
            "The client went away before the whole file came (`upload-interrupted`); the link "
            "is used and reads `failed`.",
        ),
        **problem_doc(
            404,
            "No upload link has this token (`upload-link-not-found`).",
        ),
        **problem_doc(408, "The file did not arrive within 2 minutes (`upload-timeout`)."),
        **problem_doc(
            409,
            "Another upload of the user is in progress (`upload-in-progress`; the link stays "
            "unused), the file does not fit into the user's storage (`storage-full`), the user "
            "has 100 models already (`too-many-models`), or, rarely, the numbered name is one "
            "the user has in another case (`model-name-taken`).",
        ),
        **problem_doc(
            410,
            "The link took its file already or expired (`upload-link-gone`; `reason` says "
            "`used` or `expired`), or its account was deleted while the file was uploading.",
        ),
        **problem_doc(
            413,
            "The file is larger than the link's size (`larger-than-link`; `size_bytes`) or "
            "than 50 MB (`model-too-large`).",
        ),
        **problem_doc(
            422,
            "A check failed (`model-check-failed`): `check` names it (`opens` or `weights`) and "
            "`detail` says why.",
        ),
    },
    openapi_extra=_UPLOAD_BODY,
)
async def upload_by_link(
    token: UploadToken, request: Request, service: ModelsServiceDep, response: Response
) -> ModelOut:
    """Uploads the model file of a one-time link (`POST /api/v1/models/uploads`): the body is
    the file, as `curl -T <file> <url>` sends it. The token in the path is the only sign-in: a
    session or an API key is not needed and does not stand in for it. The link is used once
    the upload starts, whatever comes of it; a file larger than the link's size by its
    `Content-Length`, or another upload of the user in progress, leaves it unused. The file is
    checked as `POST /api/v1/models` checks it and becomes a model of the link's user, under
    the link's name (numbered when the user has it)."""
    response.headers.update(NO_STORE)
    try:
        return await service.upload_by_link(
            token, _declared_size(request), request.stream(), _link_failure
        )
    except UploadLinkNotFound:
        raise _link_not_found() from None
    except UploadLinkGone as gone:
        raise _link_gone(gone) from None
    except ClientDisconnect:
        # The service recorded it on the link; an answer, not an error, as nothing is wrong here.
        raise ProblemError(
            400, INTERRUPTED.problem, "Upload interrupted", INTERRUPTED.error
        ) from None
    except tuple(_LINK_PROBLEMS) as error:
        # The service logged the failure with the link's user.
        raise _LINK_PROBLEMS[type(error)](error) from None


@router.patch(
    "/api/v1/models/{model_id}",
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **_NOT_FOUND,
        **problem_doc(409, "The user has another model with this name (`model-name-taken`)."),
    },
    dependencies=[Depends(require_same_origin)],
)
async def update_model(
    model_id: ModelId,
    body: ModelUpdate,
    user: ModelsUserDep,
    service: ModelsServiceDep,
    response: Response,
) -> ModelOut:
    """Renames the model (the extension stays) or stars and unstars it."""
    response.headers.update(NO_STORE)
    try:
        return await service.update(user.id, model_id, body)
    except ModelNotFound:
        raise model_not_found() from None
    except ModelNameTaken as taken:
        raise rename_taken(taken) from None


@router.delete(
    "/api/v1/models/{model_id}",
    status_code=204,
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN, **_NOT_FOUND},
    dependencies=[Depends(require_same_origin)],
)
async def delete_model(model_id: ModelId, user: ModelsUserDep, service: ModelsServiceDep) -> None:
    """Deletes the model and its file, which frees its storage."""
    try:
        await service.delete(user.id, model_id)
    except ModelNotFound:
        raise model_not_found() from None


@router.put(
    "/api/v1/models/{model_id}/manifest",
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **_NOT_FOUND,
        **problem_doc(
            413,
            f"The manifest is larger than {megabytes(MANIFEST_MAX_BYTES)} (`manifest-too-large`).",
        ),
        **problem_doc(
            422,
            "A check failed (`manifest-check-failed`): `problems` lists what is wrong, at most "
            f"{PROBLEMS_SHOWN}, each with its `check` (`schema`, `catalog`, `params`, `names`, "
            "`signature`, `example` or `model`), the `field` it is about (a path such as "
            "`input.features[2].id`, null for the whole manifest), a `detail` of one sentence "
            "and the values that fit there in `allowed` (null when the check does not say); "
            "`more_problems` counts the rest and `unknown_blocks` names the blocks the catalog "
            "does not have. `detail` says the same in words. "
            "`POST /api/v1/models/{model_id}/manifest/check` runs the same checks and keeps "
            "nothing.",
        ),
    },
    dependencies=[Depends(require_same_origin)],
    openapi_extra=_MANIFEST_BODY,
)
async def set_manifest(
    model_id: ModelId,
    *,
    request: Request,
    user: ModelsUserDep,
    service: ModelsServiceDep,
    checks: ModelChecksDep,
    response: Response,
) -> ModelOut:
    """Adds the model's manifest, or replaces the one it has; the body is the manifest. It is
    kept only when every check passes: its form, the catalog (every block exists, in its
    step), the blocks' parameters, the names of the columns, the signature against the window
    and the columns, the shapes of the example, and the signature against the input and
    output the upload read from the model file. The model is then `ready`. A trial run in the
    worker follows after the answer: the model becomes `verified` when it gives the output of
    the manifest's example, and a failed run leaves it `ready` with its `check_error`."""
    response.headers.update(NO_STORE)
    try:
        model = await service.set_manifest(
            user.id, model_id, _declared_size(request), request.stream()
        )
    except ModelNotFound:
        raise model_not_found() from None
    except ManifestTooLarge:
        raise manifest_too_large() from None
    except ManifestCheckFailed as failed:
        failed_checks = ", ".join(dict.fromkeys(problem.check for problem in failed.problems))
        logger.info("A manifest of user %s was refused (%s)", user.id, failed_checks)
        raise manifest_check_failed(failed) from None
    checks.start(user.id, model.id)
    return model


@router.post(
    "/api/v1/models/{model_id}/manifest/check",
    responses={
        **_NOT_SIGNED_IN,
        **_NOT_FOUND,
        **problem_doc(
            413,
            f"The manifest is larger than {megabytes(MANIFEST_MAX_BYTES)} (`manifest-too-large`).",
        ),
    },
    openapi_extra={**_MANIFEST_BODY, "security": [{API_KEY_SCHEME: [Scope.read.value]}]},
)
async def check_manifest(
    model_id: ModelId,
    *,
    request: Request,
    caller: CallerDep,
    service: ModelsServiceDep,
    response: Response,
) -> ManifestCheckOut:
    """Checks a manifest for the model as `PUT /api/v1/models/{model_id}/manifest` would, and
    keeps nothing: a dry run, for an editor or an agent to fix the manifest before it sets it.
    The body is the manifest. `valid` says whether every check passed; else `problems` lists
    what is wrong, at most 10, each with the `field` it is about, a `detail` of one sentence
    and the values that fit there (`allowed`). It changes nothing, so an API key needs only
    `read`."""
    caller.require(Scope.read)
    response.headers.update(NO_STORE)
    try:
        await service.check_manifest(
            caller.user.id, model_id, _declared_size(request), request.stream()
        )
    except ModelNotFound:
        raise model_not_found() from None
    except ManifestTooLarge:
        raise manifest_too_large() from None
    except ManifestCheckFailed as failed:
        return manifest_checked(failed)
    return manifest_checked(None)


@router.get(
    "/api/v1/models/{model_id}/manifest",
    responses={
        **_NOT_SIGNED_IN,
        **problem_doc(
            404,
            "The user has no model with this ID (`not-found`), or the model has no manifest "
            "yet (`no-manifest`).",
        ),
    },
    response_model_exclude_unset=True,
)
async def get_manifest(
    model_id: ModelId, user: CurrentUserDep, service: ModelsServiceDep, response: Response
) -> Manifest:
    """The model's manifest as it was given, without the defaults it left out."""
    response.headers.update(NO_STORE)
    try:
        return await service.manifest(user.id, model_id)
    except ModelNotFound:
        raise model_not_found() from None
    except NoManifest:
        raise no_manifest() from None


@preprocessing_router.get("/api/v1/preprocessing/catalog")
async def preprocessing_catalog() -> CatalogOut:
    """Every block a model's manifest can name, by step (representation, feature,
    normalization, window): its versioned id, what it does, its warm-up and its parameters
    with their types, ranges and defaults. The app builds the manifest form from it. A block
    that is not listed yet makes a manifest that cannot run."""
    return _CATALOG
