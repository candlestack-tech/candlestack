"""Errors of the models service. They know nothing about HTTP: the API turns each into its
problem."""

from collections.abc import Sequence

from candlestack.ml.schemas import ModelCheck
from candlestack.preprocessing import ManifestProblem


class UnsupportedModelFormat(Exception):
    """The file is not a Keras model (``.keras``), the only format taken for now."""


class ManifestTooLarge(Exception):
    """The manifest is larger than a manifest may be."""


class ManifestCheckFailed(Exception):
    """The manifest failed checks: ``problems`` says which and why, in plain sentences;
    ``unknown_blocks`` are the blocks it names that the catalog does not have."""

    def __init__(self, problems: Sequence[ManifestProblem], unknown_blocks: Sequence[str]) -> None:
        super().__init__(", ".join(problem.check for problem in problems))
        self.problems = list(problems)
        self.unknown_blocks = list(unknown_blocks)


class NoManifest(Exception):
    """The model has no manifest yet."""


class ModelTooLarge(Exception):
    """The file is larger than an upload may be."""


class StorageFull(Exception):
    """The file does not fit into the user's storage."""

    def __init__(self, used_bytes: int, quota_bytes: int) -> None:
        super().__init__(f"{used_bytes} of {quota_bytes} bytes taken")
        self.used_bytes = used_bytes
        self.quota_bytes = quota_bytes


class TooManyModels(Exception):
    """The user has as many models as a user may keep."""


class UploadInProgress(Exception):
    """Another upload of the user is still in progress."""


class UploadTimeout(Exception):
    """The file did not arrive in time."""


class AccountGone(Exception):
    """The user's account was deleted while the file was uploading."""


class ModelCheckFailed(Exception):
    """A check of the uploaded file failed: ``check`` names it, ``detail`` tells the user what
    is wrong with the file and what to do, in plain words."""

    def __init__(self, check: ModelCheck, detail: str) -> None:
        super().__init__(f"{check}: {detail}")
        self.check = check
        self.detail = detail


class ModelNotFound(Exception):
    """The user has no model with the ID (another user's model included)."""


class ModelNameTaken(Exception):
    """The user has another model with the name."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name
