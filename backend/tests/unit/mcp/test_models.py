"""The model tools through the SDK's client over HTTP, with the models service faked: what each
tool sends the service, what it answers, and the problems of its errors. The tools on Postgres
are integration tests (tests/integration/mcp/test_models.py)."""

import json
from collections.abc import AsyncIterable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, RequestParamsMeta

from candlestack.accounts import Caller, Scope, User
from candlestack.core import ProblemError
from candlestack.mcp import http, models
from candlestack.mcp.apps import CHART_KEY
from candlestack.ml import (
    Framework,
    ManifestCheckFailed,
    ManifestSummary,
    ManifestTooLarge,
    ModelNameTaken,
    ModelNotFound,
    ModelOut,
    ModelState,
    ModelUpdate,
    ModelUploadIn,
    NoManifest,
    UploadNotFound,
    UploadStatus,
)
from candlestack.ml.errors import StorageFull
from candlestack.ml.schemas import ModelUploadCreated, ModelUploadOut, StorageUsage
from candlestack.ml.service import ModelsPage
from candlestack.preprocessing import InvalidManifest, parse_manifest

PROBLEMS = "https://candlestack.tech/problems/"
ADA = User(id="usr_adaaaaaaaa", display_name="Ada", email="ada@example.com")
READ_KEY = "cs_readaaaa" + "a" * 32
MODELS_KEY = "cs_modelsaa" + "a" * 32
CREATED = datetime(2026, 11, 2, 9, 30, tzinfo=UTC)
MANIFEST: dict[str, Any] = {
    "version": 1,
    "input": {
        "timeframe": "30m",
        "session": "regular",
        "representation": {"id": "candles@1"},
        "features": [{"name": "close", "id": "close@1"}],
        "window": {"length": 25},
    },
    "signature": {
        "input": {"shape": [25, 1], "dtype": "float32"},
        "output": {"shape": [1], "dtype": "float32", "meaning": "score"},
    },
    "example": {"input": [[[1.0]] * 25], "output": [[0.5]]},
}


def model(model_id: str = "mdl_7f3k2m9qx4", name: str = "lstm.keras") -> ModelOut:
    return ModelOut(
        id=model_id,
        name=name,
        framework=Framework.keras,
        size_bytes=1000,
        parameters=10,
        input_shape=[25, 1],
        output_shape=[1],
        starred=False,
        backtest_count=2,
        best_sharpe=1.5,
        created_at=CREATED,
        state=ModelState.needs_setup,
        manifest_summary=None,
        checked_at=None,
        check_failed=None,
        check_error=None,
    )


class FakeService:
    """Stands in for ``ModelsService``: Ada has the models of ``models_``; it records what the
    tools ask of it."""

    def __init__(self) -> None:
        self.models_ = [model(), model("mdl_secondxxxx", "mixer.onnx")]
        self.calls: list[tuple[Any, ...]] = []
        self.error: Exception | None = None
        self.stored_manifest: dict[str, Any] | None = MANIFEST
        # The upload made its model, instead of failing its check.
        self.upload_done = False

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def models_page(
        self, user_id: str, *, offset: int = 0, limit: int | None = None
    ) -> ModelsPage:
        self.calls.append(("models_page", user_id, offset, limit))
        end = None if limit is None else offset + limit
        return ModelsPage(
            models=self.models_[offset:end],
            total=len(self.models_),
            usage=StorageUsage(used_bytes=2000, quota_bytes=100, file_max_bytes=50),
        )

    async def model(self, user_id: str, model_id: str) -> ModelOut:
        self.calls.append(("model", user_id, model_id))
        return self._model(model_id)

    def _model(self, model_id: str) -> ModelOut:
        for item in self.models_:
            if item.id == model_id:
                return item
        raise ModelNotFound

    async def manifest(self, user_id: str, model_id: str) -> Any:
        self.calls.append(("manifest", user_id, model_id))
        self._model(model_id)
        if self.stored_manifest is None:
            raise NoManifest
        return parse_manifest(self.stored_manifest)

    async def _checked(self, model_id: str, chunks: AsyncIterable[bytes]) -> bytes:
        document = b"".join([chunk async for chunk in chunks])
        self._model(model_id)
        self._raise()
        try:
            parse_manifest(document)
        except InvalidManifest as invalid:
            raise ManifestCheckFailed([invalid.problem], []) from None
        return document

    async def check_manifest(
        self, user_id: str, model_id: str, size: int | None, chunks: AsyncIterable[bytes]
    ) -> None:
        document = await self._checked(model_id, chunks)
        self.calls.append(("check_manifest", user_id, model_id, json.loads(document)))

    async def set_manifest(
        self, user_id: str, model_id: str, size: int | None, chunks: AsyncIterable[bytes]
    ) -> ModelOut:
        document = await self._checked(model_id, chunks)
        self.calls.append(("set_manifest", user_id, model_id, json.loads(document)))
        return self._model(model_id).model_copy(update={"state": ModelState.ready})

    async def update(self, user_id: str, model_id: str, update: ModelUpdate) -> ModelOut:
        self.calls.append(("update", user_id, model_id, update))
        found = self._model(model_id)
        self._raise()
        changed = update.model_dump(exclude_none=True)
        if "name" in changed:
            changed["name"] += ".keras"
        return found.model_copy(update=changed)

    async def delete(self, user_id: str, model_id: str) -> None:
        self.calls.append(("delete", user_id, model_id))
        self._model(model_id)

    async def create_upload(
        self, user_id: str, new: ModelUploadIn, public_url: str
    ) -> ModelUploadCreated:
        self.calls.append(("create_upload", user_id, new, public_url))
        self._raise()
        url = f"{public_url}/api/v1/uploads/{'t' * 40}"
        return ModelUploadCreated(
            id="upl_7f3k2m9qx4",
            name=new.name,
            size_bytes=new.size_bytes,
            status=UploadStatus.waiting,
            created_at=CREATED,
            expires_at=datetime(2026, 11, 2, 9, 45, tzinfo=UTC),
            finished_at=None,
            model_id=None,
            problem=None,
            check=None,
            error=None,
            url=url,
            page_url=f"{public_url}/upload/{'t' * 40}",
            command=f"curl --fail-with-body -T {new.name} {url}",
        )

    async def upload_status(self, user_id: str, upload_id: str) -> ModelUploadOut:
        self.calls.append(("upload_status", user_id, upload_id))
        if upload_id != "upl_7f3k2m9qx4":
            raise UploadNotFound
        failed = ModelUploadOut(
            id=upload_id,
            name="lstm.keras",
            size_bytes=1000,
            status=UploadStatus.failed,
            created_at=CREATED,
            expires_at=datetime(2026, 11, 2, 9, 45, tzinfo=UTC),
            finished_at=CREATED,
            model_id=None,
            problem="model-check-failed",
            check=None,
            error="The file holds no trained weights.",
        )
        if not self.upload_done:
            return failed
        return failed.model_copy(
            update={
                "status": UploadStatus.done,
                "model_id": "mdl_7f3k2m9qx4",
                "problem": None,
                "error": None,
            }
        )


class FakeChecks:
    def __init__(self) -> None:
        self.started: list[tuple[str, str]] = []

    def start(self, user_id: str, model_id: str) -> None:
        self.started.append((user_id, model_id))


class FakeKeys:
    """Stands in for ``accounts.key_caller``: a read key and a models key of Ada's."""

    async def __call__(self, token: str, **_clients: Any) -> Caller:
        scopes = {READ_KEY: Scope.read, MODELS_KEY: Scope.models}
        if token not in scopes:
            raise ProblemError(401, "invalid-api-key", "Invalid API key", "Unknown key.")
        return Caller(ADA, frozenset({scopes[token]}))


@pytest.fixture
def keys(monkeypatch: pytest.MonkeyPatch) -> FakeKeys:
    fake = FakeKeys()
    monkeypatch.setattr(http, "key_caller", fake)
    return fake


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> FakeService:
    fake = FakeService()
    monkeypatch.setattr(models, "_service", lambda _call, _db: fake)
    return fake


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> FakeChecks:
    fake = FakeChecks()
    monkeypatch.setattr(models, "_checks", lambda _call: fake)
    return fake


def call(mcp, name: str, arguments: dict[str, Any], key: str = READ_KEY) -> CallToolResult:
    async def run(session: Client) -> CallToolResult:
        return await session.call_tool(name, arguments)

    return mcp(run, key=key)


def problem(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error
    [content] = result.content
    assert content.type == "text"
    return json.loads(content.text)


def answer(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def test_the_tools_are_listed_with_their_annotations(mcp) -> None:
    async def run(session: Client) -> Any:
        return await session.list_tools()

    tools = {tool.name: tool for tool in mcp(run).tools}

    hints = {
        name: (
            tools[name].annotations.read_only_hint,
            tools[name].annotations.destructive_hint,
            tools[name].annotations.idempotent_hint,
        )
        for name in (
            "list_models",
            "get_model",
            "get_manifest",
            "validate_manifest",
            "set_manifest",
            "rename_model",
            "star_model",
            "delete_model",
            "create_model_upload",
            "get_model_upload",
        )
    }
    assert hints == {
        "list_models": (True, False, True),
        "get_model": (True, False, True),
        "get_manifest": (True, False, True),
        "validate_manifest": (True, False, True),
        "set_manifest": (False, True, True),
        "rename_model": (False, False, True),
        "star_model": (False, False, True),
        "delete_model": (False, True, True),
        "create_model_upload": (False, False, False),
        "get_model_upload": (True, False, True),
    }
    assert tools["delete_model"].input_schema["required"] == ["model_id", "confirm_name"]


def test_list_models_answers_them_in_short(mcp, service: FakeService) -> None:
    service.models_[0] = service.models_[0].model_copy(
        update={
            "manifest_summary": ManifestSummary.model_validate(
                {
                    "representation": {"id": "candles@1", "params": {}},
                    "timeframe": "30m",
                    "session": "regular",
                    "input_shape": [25, 1],
                    "warm_up": {"candles": 24, "kind": "exact"},
                }
            ),
            "state": ModelState.ready,
        }
    )

    listed = answer(call(mcp, "list_models", {}))

    assert listed["used_bytes"] == 2000
    assert listed["models"][0] == {
        "id": "mdl_7f3k2m9qx4",
        "name": "lstm.keras",
        "framework": "keras",
        "state": "ready",
        "starred": False,
        "timeframe": "30m",
        "session": "regular",
        "backtest_count": 2,
        "best_sharpe": 1.5,
        "created_at": "2026-11-02T09:30:00Z",
        # Its page, to show the user.
        "url": "http://testserver/models/mdl_7f3k2m9qx4",
    }
    assert listed["models"][1]["timeframe"] is None
    assert listed["total"] == 2
    assert service.calls == [("models_page", ADA.id, 0, None)]


def test_list_models_answers_a_page_when_asked(mcp, service: FakeService) -> None:
    listed = answer(call(mcp, "list_models", {"page": 2, "per_page": 1}))

    assert [item["name"] for item in listed["models"]] == ["mixer.onnx"]
    assert (listed["total"], listed["used_bytes"]) == (2, 2000)
    assert service.calls == [("models_page", ADA.id, 1, 1)]


@pytest.mark.parametrize("arguments", [{"per_page": 0}, {"per_page": 101}, {"page": 0}])
def test_list_models_refuses_a_page_out_of_range(
    mcp, service: FakeService, arguments: dict[str, int]
) -> None:
    assert problem(call(mcp, "list_models", arguments))["type"] == PROBLEMS + "validation"
    assert service.calls == []


def test_get_model_answers_one_model_in_full(mcp, service: FakeService) -> None:
    got = answer(call(mcp, "get_model", {"model_id": "mdl_secondxxxx"}))

    assert got["name"] == "mixer.onnx"
    assert got["input_shape"] == [25, 1]
    assert got["url"] == "http://testserver/models/mdl_secondxxxx"
    # It reads that one model, not the whole list.
    assert service.calls == [("model", ADA.id, "mdl_secondxxxx")]


class FakeBacktests:
    """Stands in for the backtests service the widget's part of get_model lists from."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, Any]] = []

    async def find(self, user_id: str, query: Any) -> Any:
        self.queries.append((user_id, query))
        setup = SimpleNamespace(threshold=0.05, direction=SimpleNamespace(value="both"))
        best = SimpleNamespace(
            id="bt_7f3k2m9qx4",
            name="Silent Orion",
            instruments=[SimpleNamespace(symbol="SPY")],
            total_return=-0.0563,
            sharpe=-0.44,
            setup=setup,
        )
        return SimpleNamespace(backtests=[best])


@pytest.mark.parametrize("chatgpt", [False, True])
def test_get_model_gives_the_widget_its_best_backtests_and_the_model_the_same_answer(
    mcp, service: FakeService, monkeypatch: pytest.MonkeyPatch, chatgpt: bool
) -> None:
    backtests = FakeBacktests()
    monkeypatch.setattr(models, "get_backtests_service", lambda _db: backtests)

    async def run(session: Client) -> CallToolResult:
        meta: RequestParamsMeta | None = {"openai/subject": "v1/abc"} if chatgpt else None
        return await session.call_tool("get_model", {"model_id": "mdl_secondxxxx"}, meta=meta)

    plain = call(mcp, "get_model", {"model_id": "mdl_secondxxxx"})
    widget = mcp(run, key=READ_KEY, apps=not chatgpt)

    assert widget.content == plain.content
    assert widget.structured_content == plain.structured_content
    assert CHART_KEY not in (plain.meta or {})
    assert (widget.meta or {})[CHART_KEY] == {
        "backtests": [
            {
                "id": "bt_7f3k2m9qx4",
                "name": "Silent Orion",
                "symbols": ["SPY"],
                "total_return": -0.0563,
                "sharpe": -0.44,
                "url": "http://testserver/backtests/bt_7f3k2m9qx4",
            }
        ],
        "decision": {"threshold": 0.05, "direction": "both"},
    }
    # Only the widget's call lists them: its finished ones, the best by the Sharpe ratio.
    [(user_id, query)] = backtests.queries
    assert user_id == ADA.id
    assert (query.sort, query.order, query.per_page) == ("sharpe", "desc", models.WIDGET_BACKTESTS)
    assert query.more.model_id == "mdl_secondxxxx"
    assert query.more.statuses == ("finished",)


def test_get_model_says_it_counts_the_backtests() -> None:
    """It answers how many backtests used the model, not the list: that is list_backtests."""
    text = description("get_model")

    assert "how many backtests used it (`backtest_count`)" in text
    assert "list_backtests with `model` set to the model's name" in text
    assert "backtest_count" in models.ModelOut.model_fields


def test_another_s_or_an_unknown_model_is_not_found(mcp, service: FakeService) -> None:
    result = call(mcp, "get_model", {"model_id": "mdl_nobodyxxxx"})

    assert problem(result) == {
        "type": PROBLEMS + "not-found",
        "title": "Not Found",
        "status": 404,
        "detail": "You have no model with this ID.",
    }


def test_get_manifest_leaves_the_example_out_unless_asked(mcp, service: FakeService) -> None:
    short = answer(call(mcp, "get_manifest", {"model_id": "mdl_7f3k2m9qx4"}))
    whole = answer(
        call(mcp, "get_manifest", {"model_id": "mdl_7f3k2m9qx4", "include_example": True})
    )

    assert short["manifest"]["example"] is None
    assert (short["example_samples"], short["example_included"]) == (1, False)
    assert short["manifest"]["input"]["timeframe"] == "30m"
    assert whole["manifest"]["example"]["output"] == [[0.5]]
    assert whole["example_included"] is True


def test_a_model_without_a_manifest_answers_no_manifest(mcp, service: FakeService) -> None:
    service.stored_manifest = None

    result = call(mcp, "get_manifest", {"model_id": "mdl_7f3k2m9qx4"})

    assert problem(result)["type"] == PROBLEMS + "no-manifest"


def test_validate_manifest_answers_a_valid_one(mcp, service: FakeService) -> None:
    checked = answer(
        call(mcp, "validate_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": MANIFEST})
    )

    assert checked == {"valid": True, "problems": [], "more_problems": 0, "unknown_blocks": []}
    assert service.calls == [("check_manifest", ADA.id, "mdl_7f3k2m9qx4", MANIFEST)]


def test_validate_manifest_answers_the_fields_to_fix_and_what_fits(
    mcp, service: FakeService
) -> None:
    wrong = {**MANIFEST, "input": {**MANIFEST["input"], "session": "us", "speed": 1}}

    checked = answer(
        call(mcp, "validate_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": wrong})
    )

    assert checked["valid"] is False
    assert checked["problems"] == [
        {
            "check": "schema",
            "field": "input.speed",
            "detail": "input.speed is not a field of the manifest.",
            "allowed": ["timeframe", "session", "representation", "features", "window"],
        },
        {
            "check": "schema",
            "field": "input.session",
            "detail": "input.session: Input should be 'regular' or '24/7'.",
            "allowed": ["regular", "24/7"],
        },
    ]


def test_validate_manifest_needs_only_read(mcp, service: FakeService) -> None:
    result = call(mcp, "validate_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": MANIFEST})

    assert not result.is_error


def test_a_manifest_too_large_is_its_problem(mcp, service: FakeService) -> None:
    service.error = ManifestTooLarge()

    result = call(mcp, "validate_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": MANIFEST})

    assert problem(result)["type"] == PROBLEMS + "manifest-too-large"


def test_set_manifest_keeps_it_and_starts_the_trial_run(
    mcp, service: FakeService, checks: FakeChecks
) -> None:
    result = call(
        mcp, "set_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": MANIFEST}, MODELS_KEY
    )

    assert answer(result)["state"] == "ready"
    assert answer(result)["url"] == "http://testserver/models/mdl_7f3k2m9qx4"
    assert service.calls == [("set_manifest", ADA.id, "mdl_7f3k2m9qx4", MANIFEST)]
    assert checks.started == [(ADA.id, "mdl_7f3k2m9qx4")]


def test_a_refused_manifest_is_a_problem_with_the_fields_to_fix(
    mcp, service: FakeService, checks: FakeChecks
) -> None:
    wrong = {**MANIFEST, "version": 2}

    result = call(
        mcp, "set_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": wrong}, MODELS_KEY
    )

    refused = problem(result)
    assert refused["type"] == PROBLEMS + "manifest-check-failed"
    assert refused["problems"] == [
        {
            "check": "schema",
            "field": "version",
            "detail": "This manifest has version 2. CandleStack reads version 1.",
            "allowed": [1],
        }
    ]
    assert checks.started == []


def test_set_manifest_needs_the_scope_models(mcp, service: FakeService) -> None:
    result = call(mcp, "set_manifest", {"model_id": "mdl_7f3k2m9qx4", "manifest": MANIFEST})

    refused = problem(result)
    assert refused["type"] == PROBLEMS + "missing-scope"
    assert refused["scope"] == "models"
    assert service.calls == []


def test_rename_model_keeps_the_extension(mcp, service: FakeService) -> None:
    result = call(
        mcp, "rename_model", {"model_id": "mdl_7f3k2m9qx4", "name": " lstm-v2 "}, MODELS_KEY
    )

    assert answer(result)["name"] == "lstm-v2.keras"
    assert answer(result)["url"] == "http://testserver/models/mdl_7f3k2m9qx4"
    assert service.calls == [
        ("update", ADA.id, "mdl_7f3k2m9qx4", ModelUpdate(name="lstm-v2", starred=None))
    ]


def test_a_name_taken_is_its_problem(mcp, service: FakeService) -> None:
    service.error = ModelNameTaken("mixer.keras")

    result = call(mcp, "rename_model", {"model_id": "mdl_7f3k2m9qx4", "name": "mixer"}, MODELS_KEY)

    assert problem(result)["detail"] == (
        "You already have a model named mixer.keras. Choose another name."
    )


@pytest.mark.parametrize("name", ["a/b", "", "zero\u200bwidth"])
def test_a_name_the_app_refuses_is_a_validation_problem(
    mcp, service: FakeService, name: str
) -> None:
    result = call(mcp, "rename_model", {"model_id": "mdl_7f3k2m9qx4", "name": name}, MODELS_KEY)

    assert problem(result)["type"] == PROBLEMS + "validation"
    assert service.calls == []


def test_star_model_stars_and_unstars(mcp, service: FakeService) -> None:
    starred = call(mcp, "star_model", {"model_id": "mdl_7f3k2m9qx4"}, MODELS_KEY)
    unstarred = call(
        mcp, "star_model", {"model_id": "mdl_7f3k2m9qx4", "starred": False}, MODELS_KEY
    )

    assert answer(starred)["starred"] is True
    assert answer(unstarred)["starred"] is False


def test_delete_model_needs_the_model_s_name_back(mcp, service: FakeService) -> None:
    result = call(
        mcp,
        "delete_model",
        {"model_id": "mdl_7f3k2m9qx4", "confirm_name": "lstm"},
        MODELS_KEY,
    )

    refused = problem(result)
    assert refused["type"] == PROBLEMS + "confirmation-mismatch"
    assert refused["status"] == 409
    assert "nothing was deleted" in refused["detail"]
    assert ("delete", ADA.id, "mdl_7f3k2m9qx4") not in service.calls


def test_delete_model_deletes_it_with_its_name(mcp, service: FakeService) -> None:
    result = call(
        mcp,
        "delete_model",
        {"model_id": "mdl_7f3k2m9qx4", "confirm_name": "lstm.keras"},
        MODELS_KEY,
    )

    assert answer(result) == {"id": "mdl_7f3k2m9qx4", "name": "lstm.keras", "freed_bytes": 1000}
    assert service.calls[-1] == ("delete", ADA.id, "mdl_7f3k2m9qx4")


def test_create_model_upload_answers_the_link_the_command_and_the_page(
    mcp, service: FakeService
) -> None:
    result = call(
        mcp, "create_model_upload", {"file_name": "lstm.keras", "size_bytes": 1000}, MODELS_KEY
    )

    link = answer(result)
    token = "t" * 40
    assert link["url"] == f"http://testserver/api/v1/uploads/{token}"
    assert link["page_url"] == f"http://testserver/upload/{token}"
    # A bare name is rarely where the file is: the command says where its path goes.
    assert link["command"] == f"curl --fail-with-body -T ./path/to/lstm.keras {link['url']}"
    assert link["expires_at"] == "2026-11-02T09:45:00Z"
    assert "upl_7f3k2m9qx4" in link["next_steps"]
    assert "09:45 UTC" in link["next_steps"]
    [(_, user_id, new, _)] = service.calls
    assert (user_id, new) == (ADA.id, ModelUploadIn(name="lstm.keras", size_bytes=1000))


@pytest.mark.parametrize(
    ("file_name", "path"),
    [
        ("models/lstm.keras", "models/lstm.keras"),
        ("/home/ada/my models/lstm.keras", "'/home/ada/my models/lstm.keras'"),
        (r"C:\Users\Ada\lstm.onnx", r"'C:\Users\Ada\lstm.onnx'"),
        # The home is left for the shell to expand: quoted, ~ stays a plain character.
        ("~/models/lstm.keras", "~/models/lstm.keras"),
        ("~/my models/lstm.keras", "~/'my models/lstm.keras'"),
        ("$HOME/models/lstm.keras", '"$HOME"/models/lstm.keras'),
    ],
)
def test_the_command_sends_the_file_at_the_path_given(
    mcp, service: FakeService, file_name: str, path: str
) -> None:
    result = call(
        mcp, "create_model_upload", {"file_name": file_name, "size_bytes": 1000}, MODELS_KEY
    )

    link = answer(result)
    assert link["command"] == f"curl --fail-with-body -T {path} {link['url']}"


def description(name: str) -> str:
    return next(spec.description for spec in models.TOOLS if spec.name == name)


def test_the_description_says_where_the_path_goes() -> None:
    command = models.UploadLink.model_json_schema()["properties"]["command"]

    assert "`./path/to/<name>` for a bare name" in command["description"]
    assert "replace ./path/to/ with the folder the file is in" in description("create_model_upload")


def test_a_link_that_does_not_fit_is_the_upload_s_problem(mcp, service: FakeService) -> None:
    service.error = StorageFull(90, 100)

    result = call(
        mcp, "create_model_upload", {"file_name": "lstm.keras", "size_bytes": 1000}, MODELS_KEY
    )

    refused = problem(result)
    assert refused["type"] == PROBLEMS + "storage-full"
    assert (refused["used_bytes"], refused["quota_bytes"]) == (90, 100)


def test_create_model_upload_needs_the_scope_models(mcp, service: FakeService) -> None:
    result = call(mcp, "create_model_upload", {"file_name": "lstm.keras", "size_bytes": 1000})

    assert problem(result)["type"] == PROBLEMS + "missing-scope"


def test_get_model_upload_answers_how_it_stands(mcp, service: FakeService) -> None:
    got = answer(call(mcp, "get_model_upload", {"upload_id": "upl_7f3k2m9qx4"}))

    assert got == {
        "id": "upl_7f3k2m9qx4",
        "name": "lstm.keras",
        "status": "failed",
        "expires_at": "2026-11-02T09:45:00Z",
        "model_id": None,
        "model_url": None,
        "problem": "model-check-failed",
        "error": "The file holds no trained weights.",
    }


def test_a_done_upload_answers_the_new_model_s_page(mcp, service: FakeService) -> None:
    service.upload_done = True

    got = answer(call(mcp, "get_model_upload", {"upload_id": "upl_7f3k2m9qx4"}))

    assert (got["status"], got["model_id"]) == ("done", "mdl_7f3k2m9qx4")
    assert got["model_url"] == "http://testserver/models/mdl_7f3k2m9qx4"


def test_an_unknown_upload_is_not_found(mcp, service: FakeService) -> None:
    result = call(mcp, "get_model_upload", {"upload_id": "upl_nobodyxxxx"})

    assert problem(result)["detail"] == "You have no upload link with this ID."
