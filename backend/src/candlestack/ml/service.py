"""The models of a user: uploading a file, listing, renaming, starring and deleting them,
adding their manifests, and deleting them with the account.

A user uploads one file at a time (``uploads.py``). An upload streams to an incoming file of
the storage while it is counted and hashed, never held in memory, within a time limit, and is
checked there in a child process (``sandbox.py``). Only a file that passed is kept: in one
transaction under the lock of the user's models, so two uploads cannot both pass the quota or
take the same name, its row goes in and the file moves to its place; the file goes again when
the commit fails. The incoming files are removed whatever happens.

A model can also come through a one-time upload link (``create_upload``): made by the user for
one file of a name and a size, it takes one upload (``upload_by_link``) within
``UPLOAD_LINK_MINUTES``, whose request carries no other sign-in than the link's token. That upload
runs as any other, the file at most the size the link was made for, and the link records how it
ended: the model it made, or the problem it answered.

A manifest is added to a model that is kept, and replaces the one it had: it is checked alone,
then against the shapes the upload's check read from the file, and kept only when every check
passed, which makes the model ``ready``. ``check_manifest`` runs the same checks as a dry run
and keeps nothing.
"""

import hashlib
import logging
import re
import shlex
from collections.abc import AsyncIterable, Callable, Collection
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

import anyio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.accounts import AfterDeletion
from candlestack.ml.errors import (
    AccountGone,
    LargerThanLink,
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
    UploadLinkGone,
    UploadLinkNotFound,
    UploadLinkOfAnotherUser,
    UploadNotFound,
    UploadTimeout,
)
from candlestack.ml.inspect import ModelFacts
from candlestack.ml.models import (
    UPLOAD_TOKEN_LENGTH,
    Model,
    ModelUpload,
    new_model_id,
    new_upload_id,
    new_upload_token,
)
from candlestack.ml.sandbox import check_upload
from candlestack.ml.schemas import (
    FILE_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    MODELS_MAX,
    NAME_MAX,
    QUOTA_BYTES,
    SUFFIXES,
    UPLOAD_LINK_MINUTES,
    BlockUse,
    CheckPart,
    Framework,
    ManifestSummary,
    ModelCheck,
    ModelOut,
    ModelsOut,
    ModelState,
    ModelUpdate,
    ModelUploadCreated,
    ModelUploadIn,
    ModelUploadOut,
    StorageUsage,
    UploadStatus,
    WarmUpOut,
    without_invisible,
)
from candlestack.ml.storage import ModelStorage
from candlestack.ml.store import ModelStore
from candlestack.ml.uploads import UPLOAD_LOCK_TTL, UploadLocks
from candlestack.ml.usage import UNUSED, ModelUsageStats
from candlestack.preprocessing import (
    Finding,
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
# Where an upload link points, after the public URL, and where its upload page in the app is.
UPLOAD_PATH = "/api/v1/uploads/"
UPLOAD_PAGE_PATH = "/upload/"
_TOKEN = re.compile(rf"[a-z2-7]{{{UPLOAD_TOKEN_LENGTH}}}")
# How long a link that expired stays readable at least; older ones go when the user makes a new
# one.
UPLOAD_KEPT = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class UploadFailure:
    """How an upload through a link failed, as its link records it: the slug of the problem
    the upload answered, the problem's detail, and the check that failed, if any."""

    problem: str
    error: str
    check: ModelCheck | None = None


# What an upload through a link records for the error it failed with; None for an error it did
# not expect. The API gives it, as it words the problems.
DescribeFailure = Callable[[Exception], UploadFailure | None]
# What a link records when its upload stopped otherwise: the client went away, or the process
# died during it (a link ``checking`` for longer than an upload may take).
INTERRUPTED = UploadFailure(
    "upload-interrupted",
    "The upload stopped before the file was checked. Ask for a new link and upload the file again.",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _refused(link: ModelUpload, why: str) -> None:
    """Logs an upload through `link` refused before it started, which leaves the link unused."""
    logger.info(
        "An upload of user %s through the link %s was refused (%s)", link.user_id, link.id, why
    )


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


# A path from the user's home, kept outside the quotes so the shell expands it.
_HOME_PREFIXES = {"~/": "~/", "$HOME/": '"$HOME"/'}


def upload_curl(path: str) -> str:
    """The upload command up to the link's URL, which follows it after a space: curl sending
    the file at `path`, for a POSIX shell (not PowerShell or cmd.exe). The path is quoted, but a
    leading `~/` or `$HOME/` is left for the shell to expand. `--fail-with-body` prints the
    problem when the upload is refused. The URL is not an argument: no local variable holds it
    (Sentry filters only the token's by its name)."""
    for prefix, kept in _HOME_PREFIXES.items():
        if path.startswith(prefix) and len(path) > len(prefix):
            return f"curl --fail-with-body -T {kept}{shlex.quote(path.removeprefix(prefix))}"
    return f"curl --fail-with-body -T {shlex.quote(path)}"


def upload_token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def _upload_out(upload: ModelUpload, now: datetime) -> ModelUploadOut:
    """The upload link as the API shows it at `now`."""
    status = UploadStatus(upload.status)
    problem, error = upload.problem, upload.error
    if status is UploadStatus.waiting and upload.expires_at <= now:
        status = UploadStatus.expired
    stalled = upload.started_at is not None and now - upload.started_at > timedelta(
        seconds=UPLOAD_LOCK_TTL
    )
    if status is UploadStatus.checking and stalled:
        status, problem, error = UploadStatus.failed, INTERRUPTED.problem, INTERRUPTED.error
    return ModelUploadOut(
        id=upload.id,
        name=upload.name,
        size_bytes=upload.size_bytes,
        status=status,
        created_at=upload.created_at,
        expires_at=upload.expires_at,
        finished_at=upload.finished_at,
        model_id=upload.model_id,
        problem=problem,
        check=None if upload.failed_check is None else ModelCheck(upload.failed_check),
        error=error,
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
    chunks: AsyncIterable[bytes], path: Path, framework: Framework, limit: int | None = None
) -> tuple[int, bytes]:
    """Writes the uploaded file, of the format `framework`, to `path`; its size and SHA-256.
    `limit` is the size of the upload link it comes through, if any.

    Raises:
        UploadTimeout: The file did not arrive within ``RECEIVE_TIMEOUT``.
        LargerThanLink: It is larger than `limit` (it stops reading there).
        ModelTooLarge: It is larger than an upload may be (it stops reading there).
        ModelCheckFailed: It is empty.
    """
    digest = hashlib.sha256()
    size = 0
    file = await anyio.open_file(path, "wb")
    try:
        with anyio.fail_after(RECEIVE_TIMEOUT):
            async for chunk in chunks:
                size += len(chunk)
                if limit is not None and size > limit:
                    raise LargerThanLink(limit)
                if size > FILE_MAX_BYTES:
                    raise ModelTooLarge
                digest.update(chunk)
                await file.write(chunk)
    except TimeoutError:
        raise UploadTimeout from None
    finally:
        # Outside the time limit and shielded from the request's cancellation (the client went
        # away): a close they cancelled would leave the file open, and it could not go.
        with anyio.CancelScope(shield=True):
            await file.aclose()
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


async def _received_manifest(
    declared_size: int | None, chunks: AsyncIterable[bytes]
) -> tuple[Manifest | None, list[ManifestProblem]]:
    """The manifest the client sends, checked alone (``_checked_alone``); `declared_size` is
    the size it announced (Content-Length), if any.

    Raises:
        ManifestTooLarge: It is larger than a manifest may be.
    """
    if declared_size is not None and declared_size > MANIFEST_MAX_BYTES:
        raise ManifestTooLarge
    return _checked_alone(await _read_manifest(chunks))


def _passed(model: Model, manifest: Manifest | None, problems: list[ManifestProblem]) -> Manifest:
    """`manifest` once it passed the checks against `model` too, after `problems`, those of
    the checks alone.

    Raises:
        ManifestCheckFailed: A check failed.
    """
    if manifest is None:
        raise ManifestCheckFailed(problems, [])
    problems = problems + check_with_model(manifest, model.input_shape, model.output_shape)
    if problems:
        raise ManifestCheckFailed(problems, unknown_blocks(manifest))
    return manifest


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
    findings = tuple(
        Finding(f"{what} holds the character NUL, which CandleStack cannot keep. Remove it.", field)
        for field, what in texts
    )
    return [ManifestProblem("schema", findings)] if findings else []


def _texts_with_nul(value: object, where: str) -> list[tuple[str, str]]:
    """Where in `value` a text, or the name of a field, holds NUL: the field, and what holds
    it in words."""
    if isinstance(value, str):
        return [(where, where)] if NUL in value else []
    if isinstance(value, dict):
        found = [(where, f"A field name in {where}") for key in value if NUL in key]
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


@dataclass(frozen=True, slots=True)
class ModelsPage:
    """A page of the user's models, how many there are and the storage they take."""

    models: list[ModelOut]
    total: int
    usage: StorageUsage


class ModelsService:
    def __init__(self, store: ModelStore, storage: ModelStorage, uploads: UploadLocks) -> None:
        self._store = store
        self._storage = storage
        self._uploads = uploads

    async def models(self, user_id: str) -> ModelsOut:
        """The user's models, oldest first, and the storage they take."""
        listed = await self.models_page(user_id)
        return ModelsOut(models=listed.models, usage=listed.usage)

    async def models_page(
        self, user_id: str, *, offset: int = 0, limit: int | None = None
    ) -> ModelsPage:
        """The user's models, oldest first, from `offset` and at most `limit` of them (all by
        default); how many there are and the storage all of them take. Only the models of the
        page are counted in the backtests."""
        models = await self._store.models(user_id)
        shown = models[offset:] if limit is None else models[offset : offset + limit]
        usage = await self._store.backtest_usage(user_id, [model.id for model in shown])
        return ModelsPage(
            models=[_out(model, usage.get(model.id, UNUSED), _spec(model)) for model in shown],
            total=len(models),
            usage=StorageUsage(
                used_bytes=sum(model.size_bytes for model in models),
                quota_bytes=QUOTA_BYTES,
                file_max_bytes=FILE_MAX_BYTES,
            ),
        )

    async def model(self, user_id: str, model_id: str) -> ModelOut:
        """The user's model with the ID, as the list shows it.

        Raises:
            ModelNotFound: The user has no model with the ID.
        """
        model = await self._store.model(user_id, model_id)
        if model is None:
            raise ModelNotFound
        usage = await self._store.backtest_usage(user_id, [model.id])
        return _out(model, usage.get(model.id, UNUSED), _spec(model))

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
        self,
        user_id: str,
        name: str,
        framework: Framework,
        chunks: AsyncIterable[bytes],
        link: ModelUpload | None = None,
    ) -> Model:
        incoming = self._storage.incoming_path()
        scratch = self._storage.incoming_path()
        limit = None if link is None else link.size_bytes
        try:
            size, sha256 = await _receive(chunks, incoming, framework, limit)
            facts = await check_upload(incoming, scratch, framework)
            return await self._keep(
                user_id,
                incoming,
                name=name,
                framework=framework,
                size=size,
                sha256=sha256,
                facts=facts,
                upload_id=None if link is None else link.id,
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
        upload_id: str | None = None,
    ) -> Model:
        """Adds the model of a checked file and moves the file to its place, in one transaction
        under the lock of the user's models; with the upload link it came through, which is
        ``done`` in the same transaction.

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
        if upload_id is not None:
            await self._store.finish_upload(upload_id, model.id, _now())
        await self._storage.save(incoming, user_id, model.id, framework)
        try:
            await self._store.commit()
        except BaseException:
            with anyio.CancelScope(shield=True):
                await self._storage.delete(user_id, model.id)
            raise
        return model

    async def create_upload(
        self, user_id: str, new: ModelUploadIn, public_url: str
    ) -> ModelUploadCreated:
        """Makes a one-time link that takes the upload of the file `new` within
        ``UPLOAD_LINK_MINUTES``; the answer holds its URL, of the app at `public_url`, which
        nothing keeps. The user's links that expired more than ``UPLOAD_KEPT`` ago go.

        Raises:
            UnsupportedModelFormat: Not the name of a Keras or ONNX file.
            ModelTooLarge: The size is larger than an upload may be.
            TooManyModels: The user has as many models as a user may keep.
            StorageFull: The file does not fit into the user's storage now.
        """
        name, framework = upload_name(new.name)
        if new.size_bytes > FILE_MAX_BYTES:
            raise ModelTooLarge
        _check_room(*await self._store.usage(user_id), new.size_bytes)
        now = _now()
        token = new_upload_token()
        upload = ModelUpload(
            id=new_upload_id(),
            user_id=user_id,
            name=name,
            framework=framework.value,
            size_bytes=new.size_bytes,
            token_hash=upload_token_hash(token),
            status=UploadStatus.waiting.value,
            created_at=now,
            expires_at=now + timedelta(minutes=UPLOAD_LINK_MINUTES),
        )
        await self._store.delete_uploads_before(user_id, now - UPLOAD_KEPT)
        self._store.add(upload)
        await self._store.commit()
        logger.info("User %s made the upload link %s", user_id, upload.id)
        # No local variable holds the URL: Sentry filters only the token's by its name.
        return ModelUploadCreated(
            **_upload_out(upload, now).model_dump(),
            url=public_url + UPLOAD_PATH + token,
            page_url=public_url + UPLOAD_PAGE_PATH + token,
            command=f"{upload_curl(new.name)} {public_url}{UPLOAD_PATH}{token}",
        )

    async def upload_status(self, user_id: str, upload_id: str) -> ModelUploadOut:
        """The user's upload link and how its upload stands.

        Raises:
            UploadNotFound: The user has no upload link with the ID.
        """
        upload = await self._store.upload(user_id, upload_id)
        if upload is None:
            raise UploadNotFound
        return _upload_out(upload, _now())

    async def upload_of_token(self, user_id: str, token: str) -> ModelUploadOut:
        """The user's upload link with `token` and how its upload stands, for the upload page
        the link opens in the browser.

        Raises:
            UploadLinkNotFound: No link has the token.
            UploadLinkOfAnotherUser: Another user made the link.
        """
        link = (
            await self._store.upload_by_token(upload_token_hash(token))
            if _TOKEN.fullmatch(token)
            else None
        )
        if link is None:
            raise UploadLinkNotFound
        if link.user_id != user_id:
            raise UploadLinkOfAnotherUser
        return _upload_out(link, _now())

    async def upload_by_link(
        self,
        token: str,
        declared_size: int | None,
        chunks: AsyncIterable[bytes],
        describe: DescribeFailure,
    ) -> ModelOut:
        """Takes the upload of the link with `token` and keeps the file as a model of the
        link's user, as ``upload`` does. A link is used once its upload starts, whatever comes
        of it: the link records the model it made, or how it failed (`describe` words the
        error). A link refused before its upload starts (a file larger than its size by
        `declared_size`, another upload of the user in progress) stays as it was.

        Raises:
            UploadLinkNotFound: No link has the token.
            UploadLinkGone: The link took its file already, or expired.
            LargerThanLink: The file is larger than the link's size.
            UploadInProgress: Another upload of the user is in progress.
            TooManyModels, StorageFull, UploadTimeout, ModelTooLarge, ModelCheckFailed,
                AccountGone, ModelNameTaken: As for ``upload``, once the upload started.
        """
        link = await self._link(token) if _TOKEN.fullmatch(token) else None
        if link is None:
            raise UploadLinkNotFound
        if declared_size is not None and declared_size > link.size_bytes:
            _refused(link, "the file is larger than the link")
            raise LargerThanLink(link.size_bytes)
        try:
            async with self._uploads.hold(link.user_id):
                model = await self._upload_through(link, declared_size, chunks, describe)
        except UploadInProgress:
            _refused(link, "another upload in progress")
            raise
        logger.info(
            "User %s uploaded model %s through the link %s (%d bytes)",
            model.user_id,
            model.id,
            link.id,
            model.size_bytes,
        )
        return _out(model)

    async def _upload_through(
        self,
        link: ModelUpload,
        declared_size: int | None,
        chunks: AsyncIterable[bytes],
        describe: DescribeFailure,
    ) -> Model:
        """Takes `link` and keeps its file, under the lock of the user's uploads; the link
        records how the upload failed whatever stops it, the request's cancellation included.

        Raises:
            UploadLinkGone: Another request took the link first, or it expired.
        """
        now = _now()
        claimed = await self._store.claim_upload(link.id, now)
        await self._store.commit()
        if not claimed:
            raise UploadLinkGone("expired" if link.expires_at <= now else "used")
        try:
            used, count = await self._store.usage(link.user_id)
            await self._store.commit()
            _check_room(used, count, declared_size or 1)
            return await self._receive_and_keep(
                link.user_id, link.name, Framework(link.framework), chunks, link
            )
        except BaseException as error:
            failure = describe(error) if isinstance(error, Exception) else None
            with anyio.CancelScope(shield=True):
                await self._fail_upload(link, failure or INTERRUPTED)
            raise

    async def _link(self, token: str) -> ModelUpload | None:
        """The link with `token` while it may still take its file; None when no link has it.

        Raises:
            UploadLinkGone: The link took its file already, or expired.
        """
        link = await self._store.upload_by_token(upload_token_hash(token))
        # Ends the transaction, so its connection goes back to the pool while the file comes in.
        await self._store.commit()
        if link is None:
            return None
        if link.status != UploadStatus.waiting:
            raise UploadLinkGone("used")
        if link.expires_at <= _now():
            raise UploadLinkGone("expired")
        return link

    async def _fail_upload(self, link: ModelUpload, failure: UploadFailure) -> None:
        """Records that the upload of `link` failed, after rolling back the transaction it
        failed in. A link whose account was deleted meanwhile went with it."""
        # Read first: the rollback expires what the session loaded.
        link_id, user_id = link.id, link.user_id
        await self._store.rollback()
        await self._store.fail_upload(
            link_id,
            problem=failure.problem,
            check=None if failure.check is None else failure.check.value,
            error=failure.error,
            now=_now(),
        )
        await self._store.commit()
        check = "" if failure.check is None else f" {failure.check}"
        logger.info(
            "The upload of user %s through the link %s failed (%s%s)",
            user_id,
            link_id,
            failure.problem,
            check,
        )

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
        manifest, problems = await _received_manifest(declared_size, chunks)
        await self._store.lock_user(user_id)
        model = await self._store.model(user_id, model_id)
        if model is None:
            raise ModelNotFound
        manifest = _passed(model, manifest, problems)
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

    async def check_manifest(
        self,
        user_id: str,
        model_id: str,
        declared_size: int | None,
        chunks: AsyncIterable[bytes],
    ) -> None:
        """Runs the checks of ``set_manifest`` on the manifest the client sends, against the
        user's model, and keeps nothing: a dry run.

        Raises:
            ManifestTooLarge: The manifest is larger than a manifest may be.
            ModelNotFound: The user has no model with the ID.
            ManifestCheckFailed: A check failed.
        """
        manifest, problems = await _received_manifest(declared_size, chunks)
        model = await self._store.model(user_id, model_id)
        if model is None:
            raise ModelNotFound
        _passed(model, manifest, problems)

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
