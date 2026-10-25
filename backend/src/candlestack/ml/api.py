"""HTTP API of the ml module: the signed-in user's models under ``/api/v1/models``, and the
catalog their manifests are made of under ``/api/v1/preprocessing/catalog``.

An upload is the file itself as the request body (``application/octet-stream``) with its name
in ``?name=``, so the server streams it to disk instead of holding a multipart form in memory.
It runs two checks on the file, ``opens`` and ``weights`` (``ModelCheck``), without executing
anything from it; a failed check answers 422 ``model-check-failed`` naming the check.
"""

import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from candlestack.accounts import CurrentUserDep
from candlestack.core import NO_STORE, ProblemError, problem_doc, require_same_origin
from candlestack.ml.dependencies import ModelsServiceDep
from candlestack.ml.errors import (
    AccountGone,
    ModelCheckFailed,
    ModelNameTaken,
    ModelNotFound,
    ModelTooLarge,
    StorageFull,
    TooManyModels,
    UnsupportedModelFormat,
    UploadInProgress,
    UploadTimeout,
)
from candlestack.ml.schemas import (
    FILE_MAX_BYTES,
    MB,
    MODELS_MAX,
    ModelOut,
    ModelsOut,
    ModelUpdate,
)
from candlestack.ml.service import RECEIVE_TIMEOUT
from candlestack.preprocessing import CatalogOut, describe_catalog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])
# The manifest belongs to a model, which ml checks on upload; preprocessing is plain functions
# and serves no HTTP itself, so its catalog is served here.
preprocessing_router = APIRouter(tags=["preprocessing"])

# The catalog is code: it changes only with a release.
_CATALOG = describe_catalog()


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
        "Support for this format is coming soon. For now CandleStack takes Keras models (.keras).",
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
            "Not a format the app takes yet, only Keras (`.keras`) for now "
            "(`unsupported-model-format`).",
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
    already has gets a number, as `lstm (2).keras`."""
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


@preprocessing_router.get("/api/v1/preprocessing/catalog")
async def preprocessing_catalog() -> CatalogOut:
    """Every block a model's manifest can name, by step (representation, feature,
    normalization, window): its versioned id, what it does, its warm-up and its parameters
    with their types, ranges and defaults. The app builds the manifest form from it. A block
    that is not listed yet makes a manifest that cannot run."""
    return _CATALOG
