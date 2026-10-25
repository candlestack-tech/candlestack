"""HTTP API of the ml module: the signed-in user's models under ``/api/v1/models``, and the
catalog their manifests are made of under ``/api/v1/preprocessing/catalog``.

An upload is the file itself as the request body (``application/octet-stream``) with its name
in ``?name=``, so the server streams it to disk instead of holding a multipart form in memory.
It runs two checks on the file, ``opens`` and ``weights`` (``ModelCheck``), without executing
anything from it; a failed check answers 422 ``model-check-failed`` naming the check.

A model's manifest is its own resource, ``/api/v1/models/{model_id}/manifest``: added or
replaced with PUT, which keeps it only when every check passed (else 422
``manifest-check-failed`` with the problems) and then starts its trial run in the worker
(``checks.py``), and read back with GET.
"""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from candlestack.accounts import CurrentUserDep
from candlestack.core import NO_STORE, ProblemError, problem_doc, require_same_origin
from candlestack.ml.dependencies import ModelChecksDep, ModelsServiceDep
from candlestack.ml.errors import (
    AccountGone,
    ManifestCheckFailed,
    ManifestTooLarge,
    ModelCheckFailed,
    ModelNameTaken,
    ModelNotFound,
    ModelTooLarge,
    NoManifest,
    StorageFull,
    TooManyModels,
    UnsupportedModelFormat,
    UploadInProgress,
    UploadTimeout,
)
from candlestack.ml.schemas import (
    FILE_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MB,
    MODELS_MAX,
    ModelOut,
    ModelsOut,
    ModelUpdate,
)
from candlestack.ml.service import RECEIVE_TIMEOUT
from candlestack.preprocessing import CatalogOut, Manifest, describe_catalog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])
# The manifest belongs to a model, which ml checks on upload; preprocessing is plain functions
# and serves no HTTP itself, so its catalog is served here.
preprocessing_router = APIRouter(tags=["preprocessing"])

# The catalog is code: it changes only with a release.
_CATALOG = describe_catalog()
# The problems of a failed manifest shown at most, so a manifest wrong throughout does not
# flood the page; the rest are counted.
PROBLEMS_SHOWN = 10


ModelId = Annotated[str, Path(description="Public ID of the model", examples=["mdl_7f3k2m9qx4"])]
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


def _megabytes(size: int) -> str:
    return f"{size / MB:.1f}".removesuffix(".0") + " MB"


def _not_found() -> ProblemError:
    return ProblemError(404, "not-found", "Not Found", "You have no model with this ID.")


def _declared_size(request: Request) -> int | None:
    """The size the client announced (Content-Length); None without a usable one."""
    value = request.headers.get("content-length", "")
    return int(value) if value.isdecimal() else None


def _unsupported(_error: Exception) -> ProblemError:
    return ProblemError(
        415,
        "unsupported-model-format",
        "Unsupported model format",
        "This file is not supported. CandleStack takes Keras models (.keras) now and ONNX "
        "models (.onnx) later. Export PyTorch, TensorFlow and scikit-learn models to ONNX. "
        "Pickle-based files (.pt, .pth, .pkl, .joblib) are never taken, as loading them can "
        "run any code.",
    )


def _too_large(_error: Exception) -> ProblemError:
    return ProblemError(
        413,
        "model-too-large",
        "Model too large",
        f"The file is larger than {_megabytes(FILE_MAX_BYTES)}, the largest file an upload "
        "takes. Upload a smaller model.",
    )


def _storage_full(error: Exception) -> ProblemError:
    assert isinstance(error, StorageFull)
    return ProblemError(
        409,
        "storage-full",
        "Storage full",
        f"The file does not fit into your storage: {_megabytes(error.used_bytes)} of "
        f"{_megabytes(error.quota_bytes)} are taken. Delete models you no longer need and "
        "upload it again.",
        headers=NO_STORE,  # it tells what the user's storage holds
        extensions={"used_bytes": error.used_bytes, "quota_bytes": error.quota_bytes},
    )


def _too_many(_error: Exception) -> ProblemError:
    return ProblemError(
        409,
        "too-many-models",
        "Too many models",
        f"You have {MODELS_MAX} models, the most CandleStack keeps for a user. Delete models "
        "you no longer need and upload it again.",
        headers=NO_STORE,
        extensions={"limit": MODELS_MAX},
    )


def _in_progress(_error: Exception) -> ProblemError:
    return ProblemError(
        409,
        "upload-in-progress",
        "Upload in progress",
        "Another upload of yours is still in progress. Wait until it finishes and upload this "
        "file again.",
    )


def _timeout(_error: Exception) -> ProblemError:
    return ProblemError(
        408,
        "upload-timeout",
        "Upload timed out",
        f"The file did not arrive within {RECEIVE_TIMEOUT // 60:.0f} minutes. Check the "
        "connection and upload it again.",
    )


def _check_failed(error: Exception) -> ProblemError:
    assert isinstance(error, ModelCheckFailed)
    return ProblemError(
        422,
        "model-check-failed",
        "Model check failed",
        error.detail,
        extensions={"check": error.check.value},
    )


def _account_gone(_error: Exception) -> ProblemError:
    return ProblemError(
        401,
        "not-signed-in",
        "Not signed in",
        "Your account was deleted while the file was uploading, so it was not kept.",
    )


def _name_taken(error: Exception) -> ProblemError:
    assert isinstance(error, ModelNameTaken)
    return ProblemError(
        409,
        "model-name-taken",
        "Model name taken",
        f"You already have a model named {error.name}. Rename that model or this file and "
        "upload it again.",
        headers=NO_STORE,  # it names a model of the user
    )


# The problems of a refused upload, by the service's error.
_UPLOAD_PROBLEMS: dict[type[Exception], Callable[[Exception], ProblemError]] = {
    UnsupportedModelFormat: _unsupported,
    ModelTooLarge: _too_large,
    StorageFull: _storage_full,
    TooManyModels: _too_many,
    UploadInProgress: _in_progress,
    UploadTimeout: _timeout,
    ModelCheckFailed: _check_failed,
    AccountGone: _account_gone,
    ModelNameTaken: _name_taken,
}


def _upload_problem(user_id: str, error: Exception) -> ProblemError:
    """The problem of an upload that failed with `error`, one of the service's errors; logged
    with the user's ID only."""
    problem = _UPLOAD_PROBLEMS[type(error)](error)
    check = f" {error.check}" if isinstance(error, ModelCheckFailed) else ""
    logger.info("An upload of user %s was refused (%s%s)", user_id, problem.slug, check)
    return problem


def _manifest_problem(error: ManifestCheckFailed) -> ProblemError:
    """The problem of a refused manifest: one sentence for each thing wrong, the first
    ``PROBLEMS_SHOWN`` of them, and how many more there are."""
    found = [
        (problem.check, sentence) for problem in error.problems for sentence in problem.sentences
    ]
    shown, more = found[:PROBLEMS_SHOWN], len(found) - PROBLEMS_SHOWN
    detail = " ".join(sentence for _, sentence in shown)
    if more > 0:
        detail += f" And {more} more {'problem' if more == 1 else 'problems'}."
    return ProblemError(
        422,
        "manifest-check-failed",
        "Manifest check failed",
        detail,
        extensions={
            "problems": [{"check": check, "detail": sentence} for check, sentence in shown],
            "more_problems": max(more, 0),
            "unknown_blocks": error.unknown_blocks,
        },
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
            "Not a format the app takes: Keras (`.keras`) now, ONNX (`.onnx`) later, never "
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
    user: CurrentUserDep,
    service: ModelsServiceDep,
    response: Response,
) -> ModelOut:
    """Uploads a model file: the body is the file, `name` its file name. The file is checked
    before it is kept: `opens` (a real Keras model with no custom code inside, nothing from
    the file is executed) and `weights` (it holds trained weights). A file with a name the user
    already has gets a number, as `lstm (2).keras`. The model is `needs_setup` until its
    manifest is added (`PUT /api/v1/models/{model_id}/manifest`)."""
    response.headers.update(NO_STORE)
    try:
        return await service.upload(user.id, name, _declared_size(request), request.stream())
    except tuple(_UPLOAD_PROBLEMS) as error:
        raise _upload_problem(user.id, error) from None


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
    user: CurrentUserDep,
    service: ModelsServiceDep,
    response: Response,
) -> ModelOut:
    """Renames the model (the extension stays) or stars and unstars it."""
    response.headers.update(NO_STORE)
    try:
        return await service.update(user.id, model_id, body)
    except ModelNotFound:
        raise _not_found() from None
    except ModelNameTaken as taken:
        raise ProblemError(
            409,
            "model-name-taken",
            "Model name taken",
            f"You already have a model named {taken.name}. Choose another name.",
            headers=NO_STORE,  # it names a model of the user
        ) from None


@router.delete(
    "/api/v1/models/{model_id}",
    status_code=204,
    responses={**_NOT_SIGNED_IN, **_CROSS_ORIGIN, **_NOT_FOUND},
    dependencies=[Depends(require_same_origin)],
)
async def delete_model(model_id: ModelId, user: CurrentUserDep, service: ModelsServiceDep) -> None:
    """Deletes the model and its file, which frees its storage."""
    try:
        await service.delete(user.id, model_id)
    except ModelNotFound:
        raise _not_found() from None


@router.put(
    "/api/v1/models/{model_id}/manifest",
    responses={
        **_NOT_SIGNED_IN,
        **_CROSS_ORIGIN,
        **_NOT_FOUND,
        **problem_doc(
            413,
            f"The manifest is larger than {_megabytes(MANIFEST_MAX_BYTES)} (`manifest-too-large`).",
        ),
        **problem_doc(
            422,
            "A check failed (`manifest-check-failed`): `problems` lists what is wrong, at most "
            f"{PROBLEMS_SHOWN}, each with its `check` (`schema`, `catalog`, `params`, `names`, "
            "`signature`, `example` or `model`) and a `detail` of one sentence; "
            "`more_problems` counts the rest and `unknown_blocks` names the blocks the catalog "
            "does not have. `detail` says the same in words.",
        ),
    },
    dependencies=[Depends(require_same_origin)],
    openapi_extra=_MANIFEST_BODY,
)
async def set_manifest(
    model_id: ModelId,
    *,
    request: Request,
    user: CurrentUserDep,
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
        raise _not_found() from None
    except ManifestTooLarge:
        raise ProblemError(
            413,
            "manifest-too-large",
            "Manifest too large",
            f"The manifest is larger than {_megabytes(MANIFEST_MAX_BYTES)}, the largest one "
            "CandleStack keeps. Give it a smaller example.",
        ) from None
    except ManifestCheckFailed as failed:
        failed_checks = ", ".join(dict.fromkeys(problem.check for problem in failed.problems))
        logger.info("A manifest of user %s was refused (%s)", user.id, failed_checks)
        raise _manifest_problem(failed) from None
    checks.start(user.id, model.id)
    return model


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
        raise _not_found() from None
    except NoManifest:
        raise ProblemError(
            404,
            "no-manifest",
            "No manifest",
            "This model has no manifest yet. Add one to tell CandleStack how to build its input.",
            headers=NO_STORE,
        ) from None


@preprocessing_router.get("/api/v1/preprocessing/catalog")
async def preprocessing_catalog() -> CatalogOut:
    """Every block a model's manifest can name, by step (representation, feature,
    normalization, window): its versioned id, what it does, its warm-up and its parameters
    with their types, ranges and defaults. The app builds the manifest form from it. A block
    that is not listed yet makes a manifest that cannot run."""
    return _CATALOG
