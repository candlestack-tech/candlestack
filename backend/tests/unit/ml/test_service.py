"""Keeping a checked upload when Postgres refuses the row: a name its unique index compares as
taken is a taken name, anything else goes on as it is; the state and the summary of a model's
manifest. The flows with a real Postgres: tests/integration/ml."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy.exc import IntegrityError

from candlestack.ml.errors import ModelNameTaken
from candlestack.ml.inspect import ModelFacts
from candlestack.ml.models import Model
from candlestack.ml.schemas import ModelState
from candlestack.ml.service import ModelsService, _checked_alone, _out
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
    assert (out.manifest_summary, out.check_error) == (None, None)


def test_a_model_with_a_manifest_is_ready_until_a_trial_run_verifies_it(raw: Manifest) -> None:
    assert _out(model(), manifest=raw).state == ModelState.ready
    assert _out(model(datetime.now(UTC)), manifest=raw).state == ModelState.verified


def test_a_failed_trial_run_leaves_the_model_ready_with_its_error(raw: Manifest) -> None:
    failed = model()
    failed.check_error = "The model failed on its input."

    out = _out(failed, manifest=raw)

    assert (out.state, out.check_error) == (ModelState.ready, "The model failed on its input.")


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
