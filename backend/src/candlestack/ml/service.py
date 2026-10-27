"""The models of a user: uploading a file, listing, renaming, starring and deleting them,
adding their manifests, and deleting them with the account.

A user uploads one file at a time (``uploads.py``). An upload streams to an incoming file of
the storage while it is counted and hashed, never held in memory, within a time limit, and is
checked there in a child process (``sandbox.py``). Only a file that passed is kept: in one
transaction under the lock of the user's models, so two uploads cannot both pass the quota or
take the same name, its row goes in and the file moves to its place; the file goes again when
the commit fails. The incoming files are removed whatever happens.

A manifest is added to a model that is kept, and replaces the one it had: it is checked alone,
then against the shapes the upload's check read from the file, and kept only when every check
passed, which makes the model ``ready``.
"""

import hashlib
import logging
from collections.abc import AsyncIterable, Collection
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import anyio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.accounts import AfterDeletion
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
    UploadTimeout,
)
from candlestack.ml.inspect import ModelFacts
from candlestack.ml.models import Model, new_model_id
from candlestack.ml.sandbox import check_upload
from candlestack.ml.schemas import (
    FILE_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MODELS_MAX,
    NAME_MAX,
    QUOTA_BYTES,
    SUFFIXES,
    BlockUse,
    CheckPart,
    Framework,
    ManifestSummary,
    ModelCheck,
    ModelOut,
    ModelsOut,
    ModelState,
    ModelUpdate,
    StorageUsage,
    WarmUpOut,
    without_invisible,
)
from candlestack.ml.storage import ModelStorage
from candlestack.ml.store import ModelStore
from candlestack.ml.uploads import UploadLocks
from candlestack.ml.usage import UNUSED, ModelUsageStats
from candlestack.preprocessing import (
    InvalidManifest,
    Manifest,
    ManifestProblem,
    check_manifest,
    check_with_model,
    parse_manifest,
    unknown_blocks,
    warm_up,
)

logger = logging.getLogger(__name__)
# The one character a JSON text may hold that Postgres cannot keep in JSONB.
NUL = "\x00"

# The name a file without a usable one gets (".keras", ".onnx").
DEFAULT_STEM = "model"
# How long the file may take to arrive: 50 MB at about 3.5 Mbit/s.
RECEIVE_TIMEOUT = 120.0
# What a numbered name adds at most: " (101)", as a user has at most 100 models.
_LABEL_MAX = len(f" ({MODELS_MAX + 1})")
# Postgres' error of a foreign key that points to nothing: the user's row is gone.
_FOREIGN_KEY_VIOLATION = "23503"
# Postgres' error of a row that a unique index refuses, and the index of the user's names.
_UNIQUE_VIOLATION = "23505"
_NAME_INDEX = "uq_models_user_id_name"
_EMPTY = "The file is empty. Choose the model's {suffix} file and upload it again."


def _now() -> datetime:
    return datetime.now(UTC)


def upload_name(file_name: str) -> tuple[str, Framework]:
    """The name an uploaded file is kept under: its base name (a client may send a path)
    without control and format characters, the part before the extension cut to what Rename
    saves; and the format its extension says.

    Raises:
        UnsupportedModelFormat: Not a Keras (``.keras``) or ONNX (``.onnx``) file, in any case.
    """
    base = file_name.replace("\\", "/").rsplit("/", 1)[-1]
    framework = next(
        (framework for framework, suffix in SUFFIXES.items() if base.lower().endswith(suffix)),
        None,
    )
    if framework is None:
        raise UnsupportedModelFormat
    stem, suffix = _split(base)
    stem = without_invisible(stem).strip()[:NAME_MAX].rstrip()
    return (stem or DEFAULT_STEM) + suffix, framework


def numbered(name: str, number: int) -> str:
    """`name` with `number` before its extension, ``lstm (2).keras``; the first is `name`.
    The part before the extension stays within what Rename saves."""
    if number == 1:
        return name
    stem, suffix = _split(name)
    label = f" ({number})"
    return stem[: NAME_MAX - len(label)].rstrip() + label + suffix


def free_name(name: str, lowered: str, taken: Collection[str]) -> str:
    """`name`, or with the first number that makes it none of `taken`, in any case. `lowered`
    is `name` and `taken` are the names the user has, lowercased by Postgres, which compares
    them so in its unique index; Python's lowercasing differs for some letters (``İ``).
    Postgres lowercases letter by letter, so numbering the lowercased name is lowercasing the
    numbered one."""
    number = 1
    while numbered(lowered, number) in taken:
        number += 1
    return numbered(name, number)


def _split(name: str) -> tuple[str, str]:
    """A model's name as the part Rename changes and the extension, which stays; a name
    always ends with the extension of its format."""
    stem, dot, extension = name.rpartition(".")
    return stem, dot + extension


def _spec(model: Model) -> Manifest | None:
    """The model's manifest without its example and notes, as the store's queries load it."""
    return None if model.manifest_spec is None else parse_manifest(model.manifest_spec)


def _summary(manifest: Manifest) -> ManifestSummary:
    spec, start = manifest.input, warm_up(manifest)
    return ManifestSummary(
        representation=BlockUse(id=spec.representation.id, params=spec.representation.params),
        timeframe=spec.timeframe,
        session=spec.session,
        input_shape=manifest.signature.input.shape,
        warm_up=WarmUpOut(candles=start.candles, kind=start.kind),
    )


def _state(model: Model, manifest: Manifest | None) -> ModelState:
    if manifest is None:
        return ModelState.needs_setup
    return ModelState.ready if model.verified_at is None else ModelState.verified


def _out(
    model: Model, usage: ModelUsageStats = UNUSED, manifest: Manifest | None = None
) -> ModelOut:
    """The model as the API shows it; `manifest` is its manifest, or the part of it the store's
    queries load (``_spec``)."""
    return ModelOut(
        id=model.id,
        name=model.name,
        framework=Framework(model.framework),
        size_bytes=model.size_bytes,
        parameters=model.parameters,
        input_shape=model.input_shape,
        output_shape=model.output_shape,
        starred=model.starred,
        backtest_count=usage.backtest_count,
        best_sharpe=usage.best_sharpe,
        created_at=model.created_at,
        state=_state(model, manifest),
        manifest_summary=None if manifest is None else _summary(manifest),
        checked_at=model.checked_at,
        check_failed=None if model.check_failed is None else CheckPart(model.check_failed),
        check_error=model.check_error,
    )


def _check_room(used: int, count: int, size: int) -> None:
    """Fails unless a file of `size` bytes fits next to `count` models taking `used` bytes.

    Raises:
        TooManyModels: The user has as many models as a user may keep.
        StorageFull: The file does not fit into the user's storage.
    """
    if count >= MODELS_MAX:
        raise TooManyModels
    if used + size > QUOTA_BYTES:
        raise StorageFull(used, QUOTA_BYTES)


async def _receive(
    chunks: AsyncIterable[bytes], path: Path, framework: Framework
) -> tuple[int, bytes]:
    """Writes the uploaded file, of the format `framework`, to `path`; its size and SHA-256.

    Raises:
        UploadTimeout: The file did not arrive within ``RECEIVE_TIMEOUT``.
        ModelTooLarge: It is larger than an upload may be (it stops reading there).
        ModelCheckFailed: It is empty.
    """
    digest = hashlib.sha256()
    size = 0
    try:
        # The file is closed outside the time limit: a close it cancelled would leave it open.
        async with await anyio.open_file(path, "wb") as file:
            with anyio.fail_after(RECEIVE_TIMEOUT):
                async for chunk in chunks:
                    size += len(chunk)
                    if size > FILE_MAX_BYTES:
                        raise ModelTooLarge
                    digest.update(chunk)
                    await file.write(chunk)
    except TimeoutError:
        raise UploadTimeout from None
    if size == 0:
        raise ModelCheckFailed(ModelCheck.opens, _EMPTY.format(suffix=SUFFIXES[framework]))
    return size, digest.digest()


async def _read_manifest(chunks: AsyncIterable[bytes]) -> bytes:
    """The manifest the client sends, held in memory as it is small.

    Raises:
        ManifestTooLarge: It is larger than a manifest may be (it stops reading there).
    """
    document = bytearray()
    async for chunk in chunks:
        document += chunk
        if len(document) > MANIFEST_MAX_BYTES:
            raise ManifestTooLarge
    return bytes(document)


def _checked_alone(document: bytes) -> tuple[Manifest | None, list[ManifestProblem]]:
    """The manifest in `document` and the problems of the checks that need no model: its form
    (no manifest when that fails), then the catalog, the parameters, the names, the signature
    and the example."""
    try:
        manifest = parse_manifest(document)
    except InvalidManifest as invalid:
        return None, [invalid.problem]
    return manifest, check_manifest(manifest) + _with_nul(manifest)


def _with_nul(manifest: Manifest) -> list[ManifestProblem]:
    """A problem of the form for the texts of the manifest that hold the character NUL: JSON
    takes it, but Postgres cannot keep it in a JSONB column. The example holds numbers only."""
    texts = _texts_with_nul(manifest.model_dump(mode="json", exclude={"example"}), "")
    sentences = tuple(
        f"{where} holds the character NUL, which CandleStack cannot keep. Remove it."
        for where in texts
    )
    return [ManifestProblem("schema", sentences)] if sentences else []


def _texts_with_nul(value: object, where: str) -> list[str]:
    """Where in `value` a text, or the name of a field, holds NUL."""
    if isinstance(value, str):
        return [where] if NUL in value else []
    if isinstance(value, dict):
        found = [f"A field name in {where}" for key in value if NUL in key]
        return found + [
            text
            for key, item in value.items()
            if NUL not in key
            for text in _texts_with_nul(item, f"{where}.{key}" if where else key)
        ]
    if isinstance(value, list):
        return [
            text for i, item in enumerate(value) for text in _texts_with_nul(item, f"{where}[{i}]")
        ]
    return []


class ModelsService:
    def __init__(self, store: ModelStore, storage: ModelStorage, uploads: UploadLocks) -> None:
        self._store = store
        self._storage = storage
        self._uploads = uploads

    async def models(self, user_id: str) -> ModelsOut:
        """The user's models, oldest first, and the storage they take."""
        models = await self._store.models(user_id)
        usage = await self._store.backtest_usage(user_id, [model.id for model in models])
        return ModelsOut(
            models=[_out(model, usage.get(model.id, UNUSED), _spec(model)) for model in models],
            usage=StorageUsage(
                used_bytes=sum(model.size_bytes for model in models),
                quota_bytes=QUOTA_BYTES,
                file_max_bytes=FILE_MAX_BYTES,
            ),
        )

    async def upload(
        self,
        user_id: str,
        file_name: str,
        declared_size: int | None,
        chunks: AsyncIterable[bytes],
    ) -> ModelOut:
        """Checks the uploaded file and keeps it as a model of the user. `declared_size` is the
        size the client announced (Content-Length), if any: one too large fails before anything
        is read, as does any file when the storage is full or the user has the most models.

        Raises:
            UnsupportedModelFormat: `file_name` is not that of a Keras or ONNX file; nothing is
                read.
            ModelTooLarge: The file is larger than an upload may be.
            UploadInProgress: Another upload of the user is in progress.
            TooManyModels: The user has as many models as a user may keep.
            StorageFull: The file does not fit into the user's storage.
            UploadTimeout: The file did not arrive in time.
            ModelCheckFailed: The file failed a check.
            AccountGone: The account was deleted meanwhile.
            ModelNameTaken: Postgres compares the name it got as one the user has.
        """
        name, framework = upload_name(file_name)
        if declared_size is not None and declared_size > FILE_MAX_BYTES:
            raise ModelTooLarge
        async with self._uploads.hold(user_id):
            used, count = await self._store.usage(user_id)
            # Ends the transaction, so its connection goes back to the pool while the file
            # comes in.
            await self._store.commit()
            # Without a size announced, a full storage still fails at once (a file is 1 byte
            # at least).
            _check_room(used, count, declared_size or 1)
            model = await self._receive_and_keep(user_id, name, framework, chunks)
        logger.info("User %s uploaded model %s (%d bytes)", user_id, model.id, model.size_bytes)
        return _out(model)

    async def _receive_and_keep(
        self, user_id: str, name: str, framework: Framework, chunks: AsyncIterable[bytes]
    ) -> Model:
        incoming = self._storage.incoming_path()
        scratch = self._storage.incoming_path()
        try:
            size, sha256 = await _receive(chunks, incoming, framework)
            facts = await check_upload(incoming, scratch, framework)
            return await self._keep(
                user_id,
                incoming,
                name=name,
                framework=framework,
                size=size,
                sha256=sha256,
                facts=facts,
            )
        finally:
            # Also when the request is cancelled (the client went away).
            with anyio.CancelScope(shield=True):
                await self._storage.discard(incoming)
                await self._storage.discard(scratch)

    async def _keep(  # noqa: PLR0913 (keyword-only: what the upload and its check found)
        self,
        user_id: str,
        incoming: Path,
        *,
        name: str,
        framework: Framework,
        size: int,
        sha256: bytes,
        facts: ModelFacts,
    ) -> Model:
        """Adds the model of a checked file and moves the file to its place, in one transaction
        under the lock of the user's models.

        Raises:
            TooManyModels: The user has as many models as a user may keep.
            StorageFull: The file does not fit into the user's storage.
            AccountGone: The account was deleted meanwhile.
            ModelNameTaken: Postgres compares the name it got as one the user has.
        """
        await self._store.lock_user(user_id)
        _check_room(*await self._store.usage(user_id), size)
        model = Model(
            id=new_model_id(),
            user_id=user_id,
            name=await self._free_name(user_id, name),
            framework=framework.value,
            size_bytes=size,
            sha256=sha256,
            parameters=facts.parameters,
            input_shape=facts.input_shape,
            output_shape=facts.output_shape,
            keras_version=facts.keras_version,
            starred=False,
            created_at=_now(),
        )
        self._store.add(model)
        try:
            await self._store.flush()
        except IntegrityError as error:
            state = getattr(error.orig, "sqlstate", None)
            if state == _FOREIGN_KEY_VIOLATION:
                raise AccountGone from None
            # A name the numbering took for free that Postgres compares as taken: a lowercasing
            # that is not letter by letter (an ICU collation) could do it.
            if state == _UNIQUE_VIOLATION and _NAME_INDEX in str(error.orig):
                raise ModelNameTaken(model.name) from None
            raise
        await self._storage.save(incoming, user_id, model.id, framework)
        try:
            await self._store.commit()
        except BaseException:
            with anyio.CancelScope(shield=True):
                await self._storage.delete(user_id, model.id)
            raise
        return model

    async def _free_name(self, user_id: str, name: str) -> str:
        """`name`, or with the first number that makes it one the user does not have yet: the
        names it may become are looked up at once."""
        stem = _split(name)[0]
        taken = await self._store.names_starting(user_id, stem[: NAME_MAX - _LABEL_MAX].rstrip())
        return free_name(name, await self._store.lowered(name), set(taken))

    async def update(self, user_id: str, model_id: str, update: ModelUpdate) -> ModelOut:
        """Renames the user's model (its extension stays) or stars or unstars it; what
        `update` leaves out stays.

        Raises:
            ModelNotFound: The user has no model with the ID.
            ModelNameTaken: Another model of the user has the new name, in any case.
        """
        await self._store.lock_user(user_id)
        model = await self._store.model(user_id, model_id)
        if model is None:
            raise ModelNotFound
        # Read before the change: writing the row resets what the query loaded with it.
        manifest = _spec(model)
        if update.name is not None:
            name = update.name + _split(model.name)[1]
            if await self._store.name_taken(user_id, name, other_than=model.id):
                raise ModelNameTaken(name)
            model.name = name
        if update.starred is not None:
            model.starred = update.starred
        usage = await self._store.backtest_usage(user_id, [model.id])
        await self._store.commit()
        return _out(model, usage.get(model.id, UNUSED), manifest)

    async def set_manifest(
        self,
        user_id: str,
        model_id: str,
        declared_size: int | None,
        chunks: AsyncIterable[bytes],
    ) -> ModelOut:
        """Checks the manifest the client sends against the user's model and keeps it in place
        of the one the model had, if any: the model is ``ready``, unchecked, until a trial run
        verifies it again (``checks.ModelChecks``, which the route starts). `declared_size` is
        the size the client announced (Content-Length), if any.

        Raises:
            ManifestTooLarge: The manifest is larger than a manifest may be.
            ModelNotFound: The user has no model with the ID.
            ManifestCheckFailed: A check failed; nothing changes.
        """
        if declared_size is not None and declared_size > MANIFEST_MAX_BYTES:
            raise ManifestTooLarge
        manifest, problems = _checked_alone(await _read_manifest(chunks))
        await self._store.lock_user(user_id)
        model = await self._store.model(user_id, model_id)
        if model is None:
            raise ModelNotFound
        if manifest is None:
            raise ManifestCheckFailed(problems, [])
        problems += check_with_model(manifest, model.input_shape, model.output_shape)
        if problems:
            raise ManifestCheckFailed(problems, unknown_blocks(manifest))
        # What the user wrote, without the defaults filled in, so it reads back as it was given.
        model.manifest = manifest.model_dump(mode="json", exclude_unset=True)
        model.verified_at = None
        model.checked_at = None
        model.check_failed = None
        model.check_error = None
        usage = await self._store.backtest_usage(user_id, [model.id])
        await self._store.commit()
        logger.info("User %s set the manifest of model %s", user_id, model.id)
        return _out(model, usage.get(model.id, UNUSED), manifest)

    async def manifest(self, user_id: str, model_id: str) -> Manifest:
        """The manifest of the user's model.

        Raises:
            ModelNotFound: The user has no model with the ID.
            NoManifest: The model has no manifest yet.
        """
        row = await self._store.manifest(user_id, model_id)
        if row is None:
            raise ModelNotFound
        if row[0] is None:
            raise NoManifest
        return parse_manifest(row[0])

    async def delete(self, user_id: str, model_id: str) -> None:
        """Deletes the user's model, then its file; a file that cannot be removed stays as an
        orphan (a warning), the model is gone.

        Raises:
            ModelNotFound: The user has no model with the ID.
        """
        # Under the lock of the user's models, so a rename or a manifest being kept finishes
        # first instead of updating a row that is gone.
        await self._store.lock_user(user_id)
        if not await self._store.delete(user_id, model_id):
            raise ModelNotFound
        await self._store.commit()
        logger.info("User %s deleted model %s", user_id, model_id)
        try:
            await self._storage.delete(user_id, model_id)
        except OSError as error:
            logger.warning(
                "The file of the deleted model %s was not removed (%s)",
                model_id,
                type(error).__name__,
            )


class ModelsAccountDeletion:
    """Deletes a user's models with their account (an ``AccountDeletionHook``): the rows in the
    deletion's transaction, the files once it is committed. It takes the lock of the user's
    models first, so an upload that is being kept finishes before, or finds the user gone."""

    def __init__(self, storage: ModelStorage) -> None:
        self._storage = storage

    async def __call__(self, user_id: str, db: AsyncSession, /) -> AfterDeletion:
        store = ModelStore(db)
        await store.lock_user(user_id)
        await store.delete_all(user_id)
        return partial(self._storage.delete_user, user_id)
