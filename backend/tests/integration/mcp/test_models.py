"""The model tools end to end: the SDK's client over HTTP, signed in with a real API key, on a
real Postgres and Redis (conftest.py), with the model storage in a temporary directory."""

import json
from pathlib import Path
from typing import Any

import pytest
import redis
from mcp import Client
from mcp.types import CallToolResult
from pydantic import SecretStr

from candlestack.core import Settings
from candlestack.ml import ModelUsageStats

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
RAW_CANDLES = (FIXTURES / "raw_candles.keras").read_bytes()


@pytest.fixture
def settings(settings: Settings, redis_url: str, tmp_path: Path) -> Settings:
    """The trial runs a manifest starts go to the test Redis, emptied first; nobody answers."""
    with redis.Redis.from_url(redis_url) as queue:
        queue.flushdb()
    return settings.model_copy(
        update={"jobs_redis_url": SecretStr(redis_url), "job_files_dir": tmp_path / "jobs"}
    )


def raw_manifest() -> dict[str, Any]:
    """The manifest of ``raw_candles.keras``: 25 candles of 5 columns, no preprocessing."""
    return json.loads((FIXTURES / "raw_candles.manifest.json").read_text(encoding="utf-8"))


def tool(name: str, arguments: dict[str, Any] | None = None):
    async def run(session: Client) -> CallToolResult:
        return await session.call_tool(name, arguments or {})

    return run


def answer(result: CallToolResult) -> dict[str, Any]:
    assert not result.is_error, result.content
    assert result.structured_content is not None
    return result.structured_content


def problem(result: CallToolResult) -> dict[str, Any]:
    assert result.is_error
    [content] = result.content
    assert content.type == "text"
    return json.loads(content.text)


def upload(user, name: str = "raw.keras") -> dict[str, Any]:
    response = user.request("POST", "/api/v1/models", params={"name": name}, content=RAW_CANDLES)
    assert response.status_code == 201, response.text
    return response.json()


def test_an_agent_sets_up_a_model(mcp, keys, ada, sql) -> None:
    model_id = upload(ada)["id"]
    key = keys("read", "models")
    wrong = raw_manifest()
    wrong["input"]["features"][0]["id"] = "opne@1"

    listed = answer(mcp(tool("list_models"), key))
    checked = answer(mcp(tool("validate_manifest", {"model_id": model_id, "manifest": wrong}), key))
    valid = answer(
        mcp(tool("validate_manifest", {"model_id": model_id, "manifest": raw_manifest()}), key)
    )
    stored_after_checks = sql("SELECT manifest FROM models")
    ready = answer(
        mcp(tool("set_manifest", {"model_id": model_id, "manifest": raw_manifest()}), key)
    )
    manifest = answer(mcp(tool("get_manifest", {"model_id": model_id}), key))

    assert [(item["id"], item["state"]) for item in listed["models"]] == [(model_id, "needs_setup")]
    assert listed["used_bytes"] == len(RAW_CANDLES)
    assert checked["valid"] is False
    [unknown] = checked["problems"]
    assert unknown["field"] == "input.features[0].id"
    assert "open@1" in unknown["allowed"]
    assert checked["unknown_blocks"] == ["opne@1"]
    assert valid["valid"] is True
    assert stored_after_checks == [(None,)]
    assert ready["state"] == "ready"
    assert ready["manifest_summary"]["timeframe"] == "30m"
    assert manifest["manifest"]["input"]["window"]["length"] == 25
    assert manifest["example_included"] is False
    assert sql("SELECT manifest FROM models") == [(raw_manifest(),)]


def test_a_refused_manifest_names_the_fields_and_keeps_nothing(mcp, keys, ada, sql) -> None:
    model_id = upload(ada)["id"]
    wrong = raw_manifest()
    wrong["input"]["window"]["last"] = "latest"

    result = mcp(tool("set_manifest", {"model_id": model_id, "manifest": wrong}), keys("models"))

    refused = problem(result)
    assert refused["type"] == PROBLEMS + "manifest-check-failed"
    assert refused["problems"][0] == {
        "check": "schema",
        "field": "input.window.last",
        "detail": "input.window.last: Input should be 'closed' or 'current'.",
        "allowed": ["closed", "current"],
    }
    assert sql("SELECT manifest FROM models") == [(None,)]


def test_an_agent_renames_stars_and_deletes_a_model(mcp, keys, ada, sql, settings) -> None:
    model_id = upload(ada)["id"]
    key = keys("models", "read")

    renamed = answer(mcp(tool("rename_model", {"model_id": model_id, "name": "lstm"}), key))
    starred = answer(mcp(tool("star_model", {"model_id": model_id}), key))
    mismatch = problem(
        mcp(tool("delete_model", {"model_id": model_id, "confirm_name": "raw.keras"}), key)
    )
    still_there = sql("SELECT name, starred FROM models")
    deleted = answer(
        mcp(tool("delete_model", {"model_id": model_id, "confirm_name": "lstm.keras"}), key)
    )

    assert renamed["name"] == "lstm.keras"
    assert starred["starred"] is True
    assert mismatch["type"] == PROBLEMS + "confirmation-mismatch"
    assert still_there == [("lstm.keras", True)]
    assert deleted == {"id": model_id, "name": "lstm.keras", "freed_bytes": len(RAW_CANDLES)}
    assert sql("SELECT id FROM models") == []
    assert not (settings.model_storage_dir / ada.id / f"{model_id}.keras").exists()


def test_another_users_model_is_not_found(mcp, keys, account) -> None:
    bob = account("usr_bobbbbbbbb")
    model_id = upload(bob)["id"]
    key = keys("read", "models")

    for name, arguments in (
        ("get_model", {"model_id": model_id}),
        ("get_manifest", {"model_id": model_id}),
        ("validate_manifest", {"model_id": model_id, "manifest": raw_manifest()}),
        ("star_model", {"model_id": model_id}),
        ("delete_model", {"model_id": model_id, "confirm_name": "raw.keras"}),
    ):
        assert problem(mcp(tool(name, arguments), key))["type"] == PROBLEMS + "not-found", name


def test_an_agent_uploads_a_model_through_a_link(mcp, keys, client) -> None:
    key = keys("models", "read")
    arguments = {"file_name": "models/lstm.keras", "size_bytes": len(RAW_CANDLES)}

    link = answer(mcp(tool("create_model_upload", arguments), key))
    waiting = answer(mcp(tool("get_model_upload", {"upload_id": link["id"]}), key))
    # What the user's curl does with the command.
    sent = client.put(link["url"].removeprefix("http://testserver"), content=RAW_CANDLES)
    done = answer(mcp(tool("get_model_upload", {"upload_id": link["id"]}), key))

    token = link["url"].rsplit("/", 1)[1]
    assert link["name"] == "lstm.keras"
    assert link["page_url"] == f"http://testserver/upload/{token}"
    assert link["command"] == f"curl --fail-with-body -T models/lstm.keras {link['url']}"
    assert waiting["status"] == "waiting"
    assert sent.status_code == 201, sent.text
    assert done["status"] == "done"
    assert done["model_id"] == sent.json()["id"]


def test_a_link_needs_the_scope_models(mcp, keys) -> None:
    result = mcp(
        tool("create_model_upload", {"file_name": "lstm.keras", "size_bytes": 10}), keys("read")
    )

    assert problem(result)["scope"] == "models"


class RecordedUsage:
    """The usage of every model as two backtests, recording the models it was asked about."""

    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    async def __call__(self, _db, _user_id, model_ids):
        self.asked.append(list(model_ids))
        return {model_id: ModelUsageStats(2, 1.5) for model_id in model_ids}


def test_get_model_reads_that_model_alone(mcp, keys, ada, app) -> None:
    upload(ada, "first.keras")
    model_id = upload(ada, "second.keras")["id"]
    usage = RecordedUsage()
    app.state.model_usage = usage

    got = answer(mcp(tool("get_model", {"model_id": model_id}), keys("read")))

    assert (got["id"], got["name"], got["backtest_count"]) == (model_id, "second.keras", 2)
    assert usage.asked == [[model_id]]


def test_list_models_answers_a_page_when_asked(mcp, keys, ada, app) -> None:
    upload(ada, "first.keras")
    second = upload(ada, "second.keras")["id"]
    usage = RecordedUsage()
    app.state.model_usage = usage

    page = answer(mcp(tool("list_models", {"page": 2, "per_page": 1}), keys("read")))

    assert [item["id"] for item in page["models"]] == [second]
    assert page["total"] == 2
    assert page["used_bytes"] == 2 * len(RAW_CANDLES)
    assert usage.asked == [[second]]
