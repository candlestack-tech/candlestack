"""Keeping a checked upload when Postgres refuses the row: a name its unique index compares as
taken is a taken name, anything else goes on as it is; the state and the summary of a model's
manifest. The flows with a real Postgres: tests/integration/ml."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError

from candlestack.ml.errors import ModelNameTaken, UploadLinkGone
from candlestack.ml.inspect import ModelFacts
from candlestack.ml.models import Model, ModelUpload
from candlestack.ml.schemas import CheckPart, Framework, ModelCheck, ModelState, UploadStatus
from candlestack.ml.service import ModelsService, _checked_alone, _out, _upload_out
from candlestack.preprocessing import Manifest, ManifestProblem, parse_manifest

if TYPE_CHECKING:
    from candlestack.ml.storage import ModelStorage
    from candlestack.ml.store import ModelStore
    from candlestack.ml.uploads import UploadLocks

pytestmark = pytest.mark.anyio


class UniqueViolation(Exception):
    sqlstate = "23505"


class FakeStore:
    def __init__(self, refusal: Exception) -> None:
        self._refusal = refusal

    async def lock_user(self, user_id: str) -> None:
        pass

    async def usage(self, user_id: str) -> tuple[int, int]:
        return 0, 0

    async def names_starting(self, user_id: str, prefix: str) -> list[str]:
        return []

    async def lowered(self, text: str) -> str:
        return text.lower()

    def add(self, model: object) -> None:
        pass

    async def flush(self) -> None:
        raise IntegrityError("INSERT INTO models", None, self._refusal)


async def keep(tmp_path: Path, refusal: Exception) -> None:
    service = ModelsService(
        cast("ModelStore", FakeStore(refusal)),
        cast("ModelStorage", None),
        cast("UploadLocks", None),
    )
    await service._keep(
        "usr_7f3k2m9qx4",
        tmp_path / "upload.part",
        name="lstm.keras",
        framework=Framework.keras,
        size=10,
        sha256=b"\0" * 32,
        facts=ModelFacts("3.15.1", 1, None, None),
    )


async def test_a_name_the_index_refuses_is_taken(tmp_path: Path) -> None:
    refusal = UniqueViolation(
        'duplicate key value violates unique constraint "uq_models_user_id_name"'
    )

    with pytest.raises(ModelNameTaken) as taken:
        await keep(tmp_path, refusal)

    assert taken.value.name == "lstm.keras"


async def test_another_integrity_error_goes_on(tmp_path: Path) -> None:
    refusal = UniqueViolation('duplicate key value violates unique constraint "pk_models"')

    with pytest.raises(IntegrityError):
        await keep(tmp_path, refusal)


FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"


@pytest.fixture
def raw() -> Manifest:
    return parse_manifest((FIXTURES / "raw_candles.manifest.json").read_bytes())


def model(verified_at: datetime | None = None) -> Model:
    return Model(
        id="mdl_7f3k2m9qx4",
        name="raw.keras",
        framework="keras",
        size_bytes=10,
        parameters=1,
        input_shape=[25, 5],
        output_shape=[1],
        starred=False,
        created_at=datetime(2026, 10, 25, tzinfo=UTC),
        verified_at=verified_at,
    )


def test_a_model_without_a_manifest_needs_setup() -> None:
    out = _out(model())

    assert out.state == ModelState.needs_setup
    assert (out.manifest_summary, out.checked_at, out.check_failed, out.check_error) == (
        None,
        None,
        None,
        None,
    )


def test_a_model_with_a_manifest_is_ready_until_a_trial_run_verifies_it(raw: Manifest) -> None:
    assert _out(model(), manifest=raw).state == ModelState.ready
    assert _out(model(datetime.now(UTC)), manifest=raw).state == ModelState.verified


def test_a_failed_trial_run_leaves_the_model_ready_with_its_error(raw: Manifest) -> None:
    failed = model()
    failed.checked_at = datetime(2026, 10, 26, tzinfo=UTC)
    failed.check_failed = "trial_run"
    failed.check_error = "The model failed on its input."

    out = _out(failed, manifest=raw)

    assert (out.state, out.check_failed, out.check_error) == (
        ModelState.ready,
        CheckPart.trial_run,
        "The model failed on its input.",
    )
    assert out.checked_at == datetime(2026, 10, 26, tzinfo=UTC)


def test_a_check_that_passed_without_an_example_says_when_it_ran(raw: Manifest) -> None:
    passed = model()
    passed.checked_at = datetime(2026, 10, 26, tzinfo=UTC)

    out = _out(passed, manifest=raw)

    assert (out.state, out.checked_at, out.check_failed, out.check_error) == (
        ModelState.ready,
        datetime(2026, 10, 26, tzinfo=UTC),
        None,
        None,
    )


def test_the_summary_says_what_the_manifest_reads(raw: Manifest) -> None:
    summary = _out(model(), manifest=raw).manifest_summary

    assert summary is not None
    assert json.loads(summary.model_dump_json()) == {
        "representation": {"id": "candles@1", "params": {}},
        "timeframe": "30m",
        "session": "regular",
        "input_shape": [25, 5],
        "warm_up": {"candles": 24, "kind": "exact"},
    }


def test_a_text_holding_nul_is_a_problem_of_the_form() -> None:
    # JSON takes the character, but Postgres cannot keep it in a JSONB column.
    document = json.loads((FIXTURES / "raw_candles.manifest.json").read_bytes())
    document["input"]["features"][0]["name"] = "open\u0000"
    document["about"] = {"note": "a\u0000b", "trained\u0000on": "AAPL"}

    manifest, problems = _checked_alone(json.dumps(document).encode())

    assert manifest is not None
    cannot_keep = "holds the character NUL, which CandleStack cannot keep. Remove it."
    assert problems == [
        ManifestProblem(
            "schema",
            (
                f"input.features[0].name {cannot_keep}",
                f"A field name in about {cannot_keep}",
                f"about.note {cannot_keep}",
            ),
        )
    ]


class RecordingStore:
    """What a deletion asks of the store, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def lock_user(self, user_id: str) -> None:
        self.calls.append("lock_user")

    async def delete(self, user_id: str, model_id: str) -> bool:
        self.calls.append("delete")
        return True

    async def commit(self) -> None:
        self.calls.append("commit")


class NoFiles:
    async def delete(self, user_id: str, model_id: str) -> None:
        pass


async def test_a_deletion_waits_for_the_lock_of_the_users_models() -> None:
    # Else a rename or a manifest being kept would update a row that is gone (a 500).
    store = RecordingStore()
    service = ModelsService(
        cast("ModelStore", store), cast("ModelStorage", NoFiles()), cast("UploadLocks", None)
    )

    await service.delete("usr_7f3k2m9qx4", "mdl_7f3k2m9qx4")

    assert store.calls == ["lock_user", "delete", "commit"]


# --- Upload links ----------------------------------------------------------------------------

MADE = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def link(status: str = "waiting", **fields: object) -> ModelUpload:
    values: dict[str, object] = {
        "id": "upl_7f3k2m9qx4",
        "user_id": "usr_7f3k2m9qx4",
        "name": "lstm.keras",
        "framework": "keras",
        "size_bytes": 1000,
        "token_hash": b"\0" * 32,
        "status": status,
        "created_at": MADE,
        "expires_at": MADE + timedelta(minutes=15),
        "started_at": None,
        "model_id": None,
        "problem": None,
        "failed_check": None,
        "error": None,
    }
    return ModelUpload(**{**values, **fields})


def test_a_link_waits_until_it_expires() -> None:
    assert _upload_out(link(), MADE + timedelta(minutes=14)).status is UploadStatus.waiting
    assert _upload_out(link(), MADE + timedelta(minutes=15)).status is UploadStatus.expired


def test_a_used_link_does_not_expire() -> None:
    done = link("done", model_id="mdl_7f3k2m9qx4")

    out = _upload_out(done, MADE + timedelta(hours=1))

    assert (out.status, out.model_id, out.problem) == (UploadStatus.done, "mdl_7f3k2m9qx4", None)


def test_a_failed_link_says_the_problem_and_the_check() -> None:
    failed = link("failed", problem="model-check-failed", failed_check="opens", error="Not a zip.")

    out = _upload_out(failed, MADE + timedelta(minutes=1))

    assert (out.status, out.problem, out.check, out.error) == (
        UploadStatus.failed,
        "model-check-failed",
        ModelCheck.opens,
        "Not a zip.",
    )


def test_a_link_checking_longer_than_an_upload_may_take_was_interrupted() -> None:
    started = MADE + timedelta(minutes=1)
    checking = link("checking", started_at=started)

    assert _upload_out(checking, started + timedelta(minutes=5)).status is UploadStatus.checking
    out = _upload_out(checking, started + timedelta(minutes=5, seconds=1))
    assert (out.status, out.problem) == (UploadStatus.failed, "upload-interrupted")
    assert out.error is not None
    assert out.error.startswith("The upload stopped before the file was checked.")


class RacedStore:
    """A waiting link that another request starts first."""

    def __init__(self, expires_at: datetime) -> None:
        self._link = link(expires_at=expires_at)

    async def upload_by_token(self, token_hash: bytes) -> ModelUpload:
        return self._link

    async def claim_upload(self, upload_id: str, now: datetime) -> bool:
        return False

    async def commit(self) -> None:
        pass


class NoLock:
    @asynccontextmanager
    async def hold(self, user_id: str) -> AsyncIterator[None]:
        yield


@pytest.mark.parametrize(
    ("expires_in", "reason"), [(timedelta(minutes=10), "used"), (timedelta(0), "expired")]
)
async def test_a_link_another_request_started_first_is_gone(
    monkeypatch: pytest.MonkeyPatch, expires_in: timedelta, reason: str
) -> None:
    now = datetime.now(UTC)
    # The link reads as waiting, then `now` reaches its expiry before the claim.
    times = iter([now - timedelta(seconds=1), now])
    monkeypatch.setattr("candlestack.ml.service._now", lambda: next(times))
    service = ModelsService(
        cast("ModelStore", RacedStore(now + expires_in)),
        cast("ModelStorage", None),
        cast("UploadLocks", NoLock()),
    )

    with pytest.raises(UploadLinkGone) as gone:
        await service.upload_by_link("q" * 40, None, empty(), lambda _error: None)

    assert gone.value.reason == reason


async def empty() -> AsyncIterator[bytes]:
    yield b""
