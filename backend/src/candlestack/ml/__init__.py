"""ML: the models users upload, their files and the checks of an upload.

Other modules import only from here (``from candlestack.ml import models_router``), never from
the submodules. Nothing here imports Keras or TensorFlow: an upload is checked by reading the
file (its ``config.json`` and weights), not by loading it.

``candlestack.main`` puts the ``ModelStorage`` on ``app.state.model_storage``, prepares it at
startup, sweeps it while the app runs (``keep_incoming_swept``) and registers
``ModelsAccountDeletion`` among the account deletion hooks. It also puts a ``ModelUsage`` on
``app.state.model_usage``: what counts the backtests of each model, which ``ml`` cannot import
(``no_usage`` counts none).
"""

from candlestack.ml.api import preprocessing_router
from candlestack.ml.api import router as models_router
from candlestack.ml.models import Model
from candlestack.ml.schemas import ModelOut, ModelsOut
from candlestack.ml.service import ModelsAccountDeletion
from candlestack.ml.storage import LocalModelStorage, ModelStorage, keep_incoming_swept
from candlestack.ml.usage import ModelUsage, ModelUsageStats, no_usage

__all__ = [
    "LocalModelStorage",
    "Model",
    "ModelOut",
    "ModelStorage",
    "ModelUsage",
    "ModelUsageStats",
    "ModelsAccountDeletion",
    "ModelsOut",
    "keep_incoming_swept",
    "models_router",
    "no_usage",
    "preprocessing_router",
]
