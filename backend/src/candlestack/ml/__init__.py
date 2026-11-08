"""ML: the models users upload, their files and the checks of an upload.

Other modules import only from here (``from candlestack.ml import models_router``), never from
the submodules. Nothing here imports Keras, TensorFlow or onnxruntime: an upload is checked by
reading the file (a Keras file's ``config.json`` and weights, an ONNX file's graph), not by
running it.

``candlestack.main`` puts the ``ModelStorage`` on ``app.state.model_storage``, prepares it at
startup, sweeps it while the app runs (``keep_incoming_swept``) and registers
``ModelsAccountDeletion`` among the account deletion hooks. It also puts a ``ModelUsage`` on
``app.state.model_usage``: what counts the backtests of each model, which ``ml`` cannot import
(``no_usage`` counts none), and the ``ModelChecks`` on ``app.state.model_checks``: the trial
runs of the models in the worker, recovered at startup and closed on shutdown.

The MCP tools call the service the routes use (``get_models_service``) and answer its errors
with the problems the routes answer (``problems``), so an agent reads the same either way.
"""

from candlestack.ml.api import preprocessing_router
from candlestack.ml.api import router as models_router
from candlestack.ml.checks import ModelChecks
from candlestack.ml.dependencies import get_models_service
from candlestack.ml.errors import (
    ManifestCheckFailed,
    ManifestTooLarge,
    ModelNameTaken,
    ModelNotFound,
    NoManifest,
    UploadNotFound,
)
from candlestack.ml.models import Model
from candlestack.ml.problems import (
    LINK_REFUSALS,
    manifest_check_failed,
    manifest_checked,
    manifest_too_large,
    model_not_found,
    no_manifest,
    rename_taken,
    upload_not_found,
    upload_problem,
)
from candlestack.ml.schemas import (
    MANIFEST_MAX_BYTES,
    MODELS_MAX,
    NAME_MAX,
    UPLOAD_LINK_MINUTES,
    Framework,
    ManifestCheckOut,
    ManifestProblemOut,
    ManifestSummary,
    ModelOut,
    ModelsOut,
    ModelState,
    ModelUpdate,
    ModelUploadIn,
    UploadStatus,
)
from candlestack.ml.service import ModelsAccountDeletion, ModelsService, upload_curl
from candlestack.ml.storage import (
    LocalModelStorage,
    ModelStorage,
    keep_incoming_swept,
    model_file,
)
from candlestack.ml.usage import ModelUsage, ModelUsageStats, no_usage

__all__ = [
    "LINK_REFUSALS",
    "MANIFEST_MAX_BYTES",
    "MODELS_MAX",
    "NAME_MAX",
    "UPLOAD_LINK_MINUTES",
    "Framework",
    "LocalModelStorage",
    "ManifestCheckFailed",
    "ManifestCheckOut",
    "ManifestProblemOut",
    "ManifestSummary",
    "ManifestTooLarge",
    "Model",
    "ModelChecks",
    "ModelNameTaken",
    "ModelNotFound",
    "ModelOut",
    "ModelState",
    "ModelStorage",
    "ModelUpdate",
    "ModelUploadIn",
    "ModelUsage",
    "ModelUsageStats",
    "ModelsAccountDeletion",
    "ModelsOut",
    "ModelsService",
    "NoManifest",
    "UploadNotFound",
    "UploadStatus",
    "get_models_service",
    "keep_incoming_swept",
    "manifest_check_failed",
    "manifest_checked",
    "manifest_too_large",
    "model_file",
    "model_not_found",
    "models_router",
    "no_manifest",
    "no_usage",
    "preprocessing_router",
    "rename_taken",
    "upload_curl",
    "upload_not_found",
    "upload_problem",
]
