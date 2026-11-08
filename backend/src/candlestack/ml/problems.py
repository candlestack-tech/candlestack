"""The problems the models service's errors answer, as the API and the MCP tools both send
them, so an agent reads the same problem whichever way it came in."""

import logging
from collections.abc import Callable

from candlestack.core import NO_STORE, ProblemError
from candlestack.ml.errors import (
    AccountGone,
    ManifestCheckFailed,
    ModelCheckFailed,
    ModelNameTaken,
    ModelTooLarge,
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
    ManifestCheckOut,
    ManifestProblemOut,
)
from candlestack.ml.service import RECEIVE_TIMEOUT

logger = logging.getLogger(__name__)

# The problems of a failed manifest shown at most, so a manifest wrong throughout does not
# flood the page; the rest are counted.
PROBLEMS_SHOWN = 10


def megabytes(size: int) -> str:
    return f"{size / MB:.1f}".removesuffix(".0") + " MB"


def model_not_found() -> ProblemError:
    return ProblemError(404, "not-found", "Not Found", "You have no model with this ID.")


def upload_not_found() -> ProblemError:
    return ProblemError(404, "not-found", "Not Found", "You have no upload link with this ID.")


def no_manifest() -> ProblemError:
    return ProblemError(
        404,
        "no-manifest",
        "No manifest",
        "This model has no manifest yet. Add one to tell CandleStack how to build its input.",
        headers=NO_STORE,
    )


def manifest_too_large() -> ProblemError:
    return ProblemError(
        413,
        "manifest-too-large",
        "Manifest too large",
        f"The manifest is larger than {megabytes(MANIFEST_MAX_BYTES)}, the largest one "
        "CandleStack keeps. Give it a smaller example.",
    )


def rename_taken(error: ModelNameTaken) -> ProblemError:
    return ProblemError(
        409,
        "model-name-taken",
        "Model name taken",
        f"You already have a model named {error.name}. Choose another name.",
        headers=NO_STORE,  # it names a model of the user
    )


def _shown(error: ManifestCheckFailed) -> tuple[list[ManifestProblemOut], int]:
    """The first ``PROBLEMS_SHOWN`` things wrong, one for each finding, and how many more."""
    found = [
        ManifestProblemOut(
            check=problem.check,
            field=finding.field or None,
            detail=finding.sentence,
            allowed=None if finding.allowed is None else list(finding.allowed),
        )
        for problem in error.problems
        for finding in problem.findings
    ]
    return found[:PROBLEMS_SHOWN], max(len(found) - PROBLEMS_SHOWN, 0)


def manifest_check_failed(error: ManifestCheckFailed) -> ProblemError:
    """The problem of a refused manifest: what is wrong, the first ``PROBLEMS_SHOWN`` things
    with their fields and what fits there, how many more there are, and the same in words."""
    shown, more = _shown(error)
    detail = " ".join(problem.detail for problem in shown)
    if more > 0:
        detail += f" And {more} more {'problem' if more == 1 else 'problems'}."
    return ProblemError(
        422,
        "manifest-check-failed",
        "Manifest check failed",
        detail,
        extensions={
            "problems": [problem.model_dump(mode="json") for problem in shown],
            "more_problems": more,
            "unknown_blocks": error.unknown_blocks,
        },
    )


def manifest_checked(error: ManifestCheckFailed | None) -> ManifestCheckOut:
    """The result of a dry run that failed with `error`, or passed without one."""
    if error is None:
        return ManifestCheckOut(valid=True, problems=[], more_problems=0, unknown_blocks=[])
    shown, more = _shown(error)
    return ManifestCheckOut(
        valid=False, problems=shown, more_problems=more, unknown_blocks=error.unknown_blocks
    )


def _unsupported(_error: Exception) -> ProblemError:
    return ProblemError(
        415,
        "unsupported-model-format",
        "Unsupported model format",
        "This file is not supported. CandleStack takes Keras models (.keras) and ONNX models "
        "(.onnx). Export PyTorch, TensorFlow and scikit-learn models to ONNX. Pickle-based "
        "files (.pt, .pth, .pkl, .joblib) are never taken, as loading them can run any code.",
    )


def _too_large(_error: Exception) -> ProblemError:
    return ProblemError(
        413,
        "model-too-large",
        "Model too large",
        f"The file is larger than {megabytes(FILE_MAX_BYTES)}, the largest file an upload "
        "takes. Upload a smaller model.",
    )


def _storage_full(error: Exception) -> ProblemError:
    assert isinstance(error, StorageFull)
    return ProblemError(
        409,
        "storage-full",
        "Storage full",
        f"The file does not fit into your storage: {megabytes(error.used_bytes)} of "
        f"{megabytes(error.quota_bytes)} are taken. Delete models you no longer need and "
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
UPLOAD_PROBLEMS: dict[type[Exception], Callable[[Exception], ProblemError]] = {
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
# What making an upload link refuses: the name and the size must fit now.
LINK_REFUSALS = (UnsupportedModelFormat, ModelTooLarge, StorageFull, TooManyModels)


def upload_problem(user_id: str, error: Exception) -> ProblemError:
    """The problem of an upload that failed with `error`, one of the service's errors; logged
    with the user's ID only."""
    problem = UPLOAD_PROBLEMS[type(error)](error)
    check = f" {error.check}" if isinstance(error, ModelCheckFailed) else ""
    logger.info("An upload of user %s was refused (%s%s)", user_id, problem.slug, check)
    return problem
