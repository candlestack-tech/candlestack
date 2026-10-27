"""The models API over HTTP, against a real Postgres, with the model storage in a temporary
directory (conftest.py): uploads and their checks, the quota, the list, renaming, starring,
deleting, the manifests, and deleting the account."""

import hashlib
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from redis import Redis
from sqlalchemy import text

from candlestack.ml import LocalModelStorage, ModelUsageStats
from candlestack.ml.schemas import FILE_MAX_BYTES, QUOTA_BYTES
from candlestack.ml.store import ModelStore

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
MB = 1024 * 1024
FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"


ONNX_RAW_CANDLES = (
    Path(__file__).parents[2] / "fixtures" / "onnx" / "raw_candles.onnx"
).read_bytes()


def keras_file(name: str) -> bytes:
    return (FIXTURES / f"{name}.keras").read_bytes()


def raw_manifest() -> dict[str, Any]:
    """The manifest of ``raw_candles.keras``: 25 candles of 5 columns, no preprocessing."""
    return json.loads((FIXTURES / "raw_candles.manifest.json").read_text(encoding="utf-8"))


RAW_SUMMARY = {
    "representation": {"id": "candles@1", "params": {}},
    "timeframe": "30m",
    "session": "regular",
    "input_shape": [25, 5],
    "warm_up": {"candles": 24, "kind": "exact"},
}


def chunked(content: bytes, size: int = 64 * 1024) -> Iterator[bytes]:
    """The body as a stream: sent without a Content-Length."""
    for start in range(0, len(content), size):
        yield content[start : start + size]


def fill_storage(sql, user_id: str, size: int, name: str = "big.keras") -> None:
    """A model of `size` bytes, straight in the table (its file does not matter here)."""
    sql(
        "INSERT INTO models (id, user_id, name, framework, size_bytes, sha256, parameters, "
        "keras_version, created_at) VALUES (:id, :user_id, :name, 'keras', :size, :sha, 1, "
        "'3.15.1', now() - interval '1 day')",
        id="mdl_bigbigbigb",
        user_id=user_id,
        name=name,
        size=size,
        sha=b"\0" * 32,
    )


# --- Upload ----------------------------------------------------------------------------------


def test_upload(ada, sql, storage, caplog) -> None:
    content = keras_file("functional")

    with caplog.at_level(logging.INFO):
        response = ada.upload(content, name="C:\\Users\\ada\\my lstm.keras")

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    model = response.json()
    assert model.pop("id").startswith("mdl_")
    assert model.pop("created_at")
    assert model == {
        "name": "my lstm.keras",
        "framework": "keras",
        "size_bytes": len(content),
        "parameters": 9325,
        "input_shape": [25, 23],
        "output_shape": [1],
        "starred": False,
        "backtest_count": 0,
        "best_sharpe": None,
        "state": "needs_setup",
        "manifest_summary": None,
        "checked_at": None,
        "check_failed": None,
        "check_error": None,
    }
    model_id = response.json()["id"]
    [row] = sql(
        "SELECT user_id, sha256, keras_version, input_shape, output_shape FROM models "
        "WHERE id = :id",
        id=model_id,
    )
    assert row == (ada.id, hashlib.sha256(content).digest(), "3.15.1", [25, 23], [1])
    assert storage.file(ada.id, model_id).read_bytes() == content
    assert storage.incoming() == []
    assert f"User {ada.id} uploaded model {model_id} ({len(content)} bytes)" in caplog.text
    assert "my lstm" not in caplog.text
    assert "ada@" not in caplog.text


def test_upload_without_a_content_length(ada, storage) -> None:
    response = ada.upload(chunked(keras_file("sequential")), name="seq.keras")

    assert response.status_code == 201
    assert response.json()["output_shape"] == [3]
    assert storage.file(ada.id, response.json()["id"]).exists()


def test_an_onnx_upload(ada, sql, storage) -> None:
    response = ada.upload(ONNX_RAW_CANDLES, name="raw candles.onnx")

    assert response.status_code == 201
    model = response.json()
    assert (model["name"], model["framework"], model["size_bytes"]) == (
        "raw candles.onnx",
        "onnx",
        len(ONNX_RAW_CANDLES),
    )
    assert (model["parameters"], model["input_shape"], model["output_shape"]) == (128, [25, 5], [1])
    assert model["state"] == "needs_setup"
    [row] = sql("SELECT framework, keras_version FROM models WHERE id = :id", id=model["id"])
    assert row == ("onnx", None)
    assert storage.file(ada.id, model["id"], ".onnx").read_bytes() == ONNX_RAW_CANDLES
    assert not storage.file(ada.id, model["id"]).exists()
    assert storage.incoming() == []


def test_an_onnx_model_gets_its_manifest_a_name_and_goes(ada, storage) -> None:
    model_id = ada.upload(ONNX_RAW_CANDLES, name="mixer.onnx").json()["id"]

    manifest = ada.set_manifest(model_id, json.dumps(raw_manifest()))
    renamed = ada.update(model_id, name="mixer v2")
    second = ada.upload(ONNX_RAW_CANDLES, name="mixer v2.onnx")

    assert (manifest.status_code, manifest.json()["state"]) == (200, "ready")
    assert renamed.json()["name"] == "mixer v2.onnx"
    assert second.json()["name"] == "mixer v2 (2).onnx"
    assert ada.delete(model_id).status_code == 204
    assert not storage.file(ada.id, model_id, ".onnx").exists()


def test_a_failed_check_of_an_onnx_file_is_named(ada, sql, storage) -> None:
    response = ada.upload(keras_file("functional"), name="functional.onnx")

    assert response.status_code == 422
    assert response.json()["check"] == "opens"
    assert response.json()["detail"].startswith("The file is not a readable ONNX model.")
    assert sql("SELECT id FROM models") == []
    assert storage.files() == []


def test_an_empty_onnx_file_names_its_extension(ada) -> None:
    response = ada.upload(b"", name="mixer.onnx")

    assert response.json()["detail"] == (
        "The file is empty. Choose the model's .onnx file and upload it again."
    )


def test_the_list_and_the_storage_used(ada, bob) -> None:
    first = ada.upload(keras_file("functional")).json()
    second = ada.upload(keras_file("sequential"), name="sequential.keras").json()
    bob.upload(keras_file("two_inputs"), name="two_inputs.keras")

    response = ada.models()

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "models": [first, second],
        "usage": {
            "used_bytes": first["size_bytes"] + second["size_bytes"],
            "quota_bytes": QUOTA_BYTES,
            "file_max_bytes": FILE_MAX_BYTES,
        },
    }


def test_no_models_yet(ada) -> None:
    assert ada.models().json() == {
        "models": [],
        "usage": {"used_bytes": 0, "quota_bytes": 100 * MB, "file_max_bytes": 50 * MB},
    }


def test_a_taken_name_gets_a_number(ada, bob) -> None:
    names = [ada.upload(keras_file("functional")).json()["name"] for _ in range(3)]
    names.append(ada.upload(keras_file("functional"), name="FUNCTIONAL.keras").json()["name"])
    other = bob.upload(keras_file("functional")).json()["name"]

    assert names == [
        "functional.keras",
        "functional (2).keras",
        "functional (3).keras",
        "FUNCTIONAL (4).keras",
    ]
    assert other == "functional.keras"  # names are per user


def test_names_that_python_lowercases_otherwise_are_numbered(ada) -> None:
    # Python lowercases "İ" to two letters; Postgres compares the names by its own lower().
    names = [
        ada.upload(keras_file("functional"), name="İnci.keras").json()["name"] for _ in range(3)
    ]

    # Postgres may lowercase "İ" to "i" (its collation decides): then this name is taken too.
    other = ada.upload(keras_file("functional"), name="inci.keras")

    assert names == ["İnci.keras", "İnci (2).keras", "İnci (3).keras"]
    assert other.status_code == 201


def test_a_freed_name_is_taken_again(ada) -> None:
    first = ada.upload(keras_file("functional")).json()
    ada.upload(keras_file("functional"))
    ada.delete(first["id"])

    assert ada.upload(keras_file("functional")).json()["name"] == "functional.keras"


def test_streaming_more_than_50_mb_stops_at_the_limit(ada, sql, storage) -> None:
    response = ada.upload(chunked(b"\0" * (FILE_MAX_BYTES + 1), size=MB))

    assert response.status_code == 413
    assert response.json()["type"] == PROBLEMS + "model-too-large"
    assert sql("SELECT id FROM models") == []
    assert storage.incoming() == []


def test_exactly_50_mb_passes_the_size_limit(ada, storage) -> None:
    # Not a model, so the check refuses it, but only after the whole file came in.
    response = ada.upload(chunked(b"\0" * FILE_MAX_BYTES, size=MB))

    assert response.status_code == 422
    assert storage.incoming() == []


def test_a_file_that_does_not_fit_is_refused_at_once(ada, sql) -> None:
    used = QUOTA_BYTES - 100_000
    fill_storage(sql, ada.id, used)

    response = ada.upload(keras_file("functional"))  # 145 KB, announced

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "type": PROBLEMS + "storage-full",
        "title": "Storage full",
        "status": 409,
        "detail": "The file does not fit into your storage: 99.9 MB of 100 MB are taken. Delete "
        "models you no longer need and upload it again.",
        "used_bytes": used,
        "quota_bytes": QUOTA_BYTES,
    }


def test_a_full_storage_refuses_any_file_at_once(ada, sql, storage, caplog) -> None:
    fill_storage(sql, ada.id, QUOTA_BYTES)

    with caplog.at_level(logging.INFO):
        response = ada.upload(chunked(keras_file("functional")))  # no Content-Length

    assert response.status_code == 409
    assert response.json()["type"] == PROBLEMS + "storage-full"
    assert storage.incoming() == []
    assert f"An upload of user {ada.id} was refused (storage-full)" in caplog.text


def test_100_models_are_the_most(ada, sql) -> None:
    for number in range(100):
        sql(
            "INSERT INTO models (id, user_id, name, framework, size_bytes, sha256, parameters, "
            "keras_version, created_at) VALUES (:id, :user_id, :name, 'keras', 1, :sha, 1, "
            "'3.15.1', now())",
            id=f"mdl_{number:010d}",
            user_id=ada.id,
            name=f"m{number}.keras",
            sha=b"\0" * 32,
        )

    response = ada.upload(keras_file("functional"))

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "type": PROBLEMS + "too-many-models",
        "title": "Too many models",
        "status": 409,
        "detail": "You have 100 models, the most CandleStack keeps for a user. Delete models you "
        "no longer need and upload it again.",
        "limit": 100,
    }


def test_an_upload_while_another_is_in_progress(ada, redis_url, storage) -> None:
    redis = Redis.from_url(redis_url)
    redis.set(f"ml:upload:{ada.id}", "another upload", ex=60)
    try:
        response = ada.upload(keras_file("functional"))

        assert response.status_code == 409
        assert response.json() == {
            "type": PROBLEMS + "upload-in-progress",
            "title": "Upload in progress",
            "status": 409,
            "detail": "Another upload of yours is still in progress. Wait until it finishes and "
            "upload this file again.",
        }
        assert redis.get(f"ml:upload:{ada.id}") == b"another upload"  # not this upload's
        assert storage.files() == []
    finally:
        redis.delete(f"ml:upload:{ada.id}")
        redis.close()


def test_the_upload_lock_goes_with_the_upload(ada, redis_url) -> None:
    redis = Redis.from_url(redis_url)
    try:
        assert ada.upload(keras_file("functional")).status_code == 201
        assert ada.upload(keras_file("lambda_layer")).status_code == 422

        assert redis.get(f"ml:upload:{ada.id}") is None
    finally:
        redis.close()


def test_a_streamed_file_that_does_not_fit_is_refused(ada, sql, storage) -> None:
    fill_storage(sql, ada.id, 50 * MB)
    sql(
        "UPDATE models SET size_bytes = :size WHERE user_id = :id",
        size=QUOTA_BYTES - 100_000,
        id=ada.id,
    )

    response = ada.upload(chunked(keras_file("functional")))

    assert response.status_code == 409
    assert response.json()["used_bytes"] == QUOTA_BYTES - 100_000
    assert len(sql("SELECT id FROM models")) == 1
    assert storage.incoming() == []
    assert storage.files() == []


def test_a_file_that_fills_the_storage_exactly_fits(ada, sql) -> None:
    content = keras_file("functional")
    fill_storage(sql, ada.id, QUOTA_BYTES - len(content))

    assert ada.upload(content).status_code == 201
    assert ada.models().json()["usage"]["used_bytes"] == QUOTA_BYTES


@pytest.mark.parametrize(
    ("name", "check", "detail"),
    [
        ("lambda_layer", "opens", 'The layer "lambda" uses custom code (Lambda)'),
        ("custom_layer", "opens", 'The layer "scale" uses custom code (Scale)'),
        ("no_weights", "weights", "The file holds the layers only."),
    ],
)
def test_a_failed_check_is_named(ada, sql, storage, caplog, name, check, detail) -> None:
    with caplog.at_level(logging.INFO):
        response = ada.upload(keras_file(name))

    assert response.status_code == 422
    problem = response.json()
    assert problem["type"] == PROBLEMS + "model-check-failed"
    assert problem["title"] == "Model check failed"
    assert problem["check"] == check
    assert problem["detail"].startswith(detail)
    assert sql("SELECT id FROM models") == []
    assert storage.incoming() == []
    assert storage.files() == []
    assert f"An upload of user {ada.id} was refused (model-check-failed {check})" in caplog.text


def test_an_empty_file_fails_opens(ada, storage) -> None:
    response = ada.upload(b"")

    assert response.status_code == 422
    assert response.json()["check"] == "opens"
    assert response.json()["detail"] == (
        "The file is empty. Choose the model's .keras file and upload it again."
    )
    assert storage.incoming() == []


def test_a_broken_file_fails_opens(ada) -> None:
    response = ada.upload(keras_file("functional")[:50_000])

    assert response.status_code == 422
    assert response.json()["check"] == "opens"


def test_a_failed_commit_leaves_no_file(ada, sql, storage, monkeypatch) -> None:
    commit = ModelStore.commit
    calls = []

    async def fail_the_second(self: ModelStore) -> None:
        calls.append(1)
        if len(calls) == 2:  # the first ends the read of the storage used
            raise ConnectionError("Postgres went away")
        await commit(self)

    monkeypatch.setattr(ModelStore, "commit", fail_the_second)

    response = ada.upload(keras_file("functional"))

    assert response.status_code == 500
    assert sql("SELECT id FROM models") == []
    assert storage.files() == []
    assert storage.incoming() == []


# --- Rename, star, delete --------------------------------------------------------------------


def test_rename_keeps_the_extension(ada, sql) -> None:
    model = ada.upload(keras_file("functional"), name="lstm.KERAS").json()

    response = ada.update(model["id"], name=" tsmixer v2 ")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {**model, "name": "tsmixer v2.KERAS"}
    assert sql("SELECT name FROM models") == [("tsmixer v2.KERAS",)]


def test_rename_to_its_own_name_in_another_case(ada) -> None:
    model = ada.upload(keras_file("functional"), name="lstm.keras").json()

    assert ada.update(model["id"], name="lstm").json()["name"] == "lstm.keras"
    assert ada.update(model["id"], name="LSTM").json()["name"] == "LSTM.keras"


def test_rename_to_a_taken_name(ada, bob) -> None:
    ada.upload(keras_file("functional"), name="lstm.keras")
    model = ada.upload(keras_file("functional"), name="gru.keras").json()
    bob.upload(keras_file("functional"), name="tcn.keras")

    taken = ada.update(model["id"], name="LSTM")
    others = ada.update(model["id"], name="tcn")

    assert taken.status_code == 409
    assert taken.headers["cache-control"] == "no-store"
    assert taken.json() == {
        "type": PROBLEMS + "model-name-taken",
        "title": "Model name taken",
        "status": 409,
        "detail": "You already have a model named LSTM.keras. Choose another name.",
    }
    assert others.json()["name"] == "tcn.keras"  # another user's names do not count


def test_star_and_unstar(ada) -> None:
    model = ada.upload(keras_file("functional")).json()

    starred = ada.update(model["id"], starred=True)
    unchanged = ada.update(model["id"])

    assert starred.json() == {**model, "starred": True}
    assert unchanged.json()["starred"] is True
    assert ada.models().json()["models"][0]["starred"] is True
    assert ada.update(model["id"], starred=False).json()["starred"] is False


def test_another_users_model_is_not_found(ada, bob, storage) -> None:
    model = bob.upload(keras_file("functional")).json()

    renamed = ada.update(model["id"], name="mine")
    deleted = ada.delete(model["id"])

    for response in (renamed, deleted):
        assert response.status_code == 404
        assert response.json() == {
            "type": PROBLEMS + "not-found",
            "title": "Not Found",
            "status": 404,
            "detail": "You have no model with this ID.",
        }
    assert bob.models().json()["models"] == [model]
    assert storage.file(bob.id, model["id"]).exists()


def test_an_unknown_model_is_not_found(ada) -> None:
    assert ada.update("mdl_zzzzzzzzzz", starred=True).status_code == 404
    assert ada.delete("mdl_zzzzzzzzzz").status_code == 404


def test_delete_removes_the_row_and_the_file(ada, sql, storage, caplog) -> None:
    kept = ada.upload(keras_file("sequential"), name="sequential.keras").json()
    model = ada.upload(keras_file("functional")).json()

    with caplog.at_level(logging.INFO):
        response = ada.delete(model["id"])

    assert response.status_code == 204
    assert sql("SELECT id FROM models") == [(kept["id"],)]
    assert storage.files() == [storage.file(ada.id, kept["id"])]
    assert ada.models().json()["usage"]["used_bytes"] == kept["size_bytes"]
    assert f"User {ada.id} deleted model {model['id']}" in caplog.text
    assert ada.delete(model["id"]).status_code == 404


def test_a_file_that_cannot_be_removed_stays_an_orphan(ada, sql, monkeypatch, caplog) -> None:
    model = ada.upload(keras_file("functional")).json()

    async def fail(*_args) -> None:
        raise PermissionError("/data/models/usr_adaaaaaaaa/x.keras")

    monkeypatch.setattr(LocalModelStorage, "delete", fail)

    with caplog.at_level(logging.WARNING):
        response = ada.delete(model["id"])

    assert response.status_code == 204
    assert sql("SELECT id FROM models") == []
    assert f"The file of the deleted model {model['id']} was not removed (PermissionError)" in (
        caplog.text
    )
    assert "/data/models" not in caplog.text


# --- Usage by backtests ----------------------------------------------------------------------


class FakeUsage:
    """Counts backtests of the models it is told about and records what it was asked."""

    def __init__(self, stats: dict[str, ModelUsageStats]) -> None:
        self.stats = stats
        self.calls: list[tuple[str, list[str]]] = []

    async def __call__(self, db, user_id, model_ids):
        assert await db.scalar(text("SELECT 1")) == 1  # the request's session
        self.calls.append((user_id, list(model_ids)))
        return {key: value for key, value in self.stats.items() if key in model_ids}


def test_the_list_asks_the_usage_once_for_every_model(app, ada) -> None:
    used = ada.upload(keras_file("functional")).json()
    unused = ada.upload(keras_file("sequential"), name="sequential.keras").json()
    usage = FakeUsage({used["id"]: ModelUsageStats(3, 1.12)})
    app.state.model_usage = usage

    models = ada.models().json()["models"]

    assert [(model["backtest_count"], model["best_sharpe"]) for model in models] == [
        (3, 1.12),
        (0, None),
    ]
    assert usage.calls == [(ada.id, [used["id"], unused["id"]])]


def test_no_models_ask_no_usage(app, ada) -> None:
    usage = FakeUsage({})
    app.state.model_usage = usage

    assert ada.models().json()["models"] == []
    assert usage.calls == []


def test_a_change_keeps_the_usage(app, ada) -> None:
    model = ada.upload(keras_file("functional")).json()
    app.state.model_usage = FakeUsage({model["id"]: ModelUsageStats(2, None)})

    response = ada.update(model["id"], starred=True)

    assert response.json()["backtest_count"] == 2
    assert response.json()["best_sharpe"] is None


def test_without_backtests_every_model_is_unused(ada) -> None:
    ada.upload(keras_file("functional"))

    [model] = ada.models().json()["models"]

    assert (model["backtest_count"], model["best_sharpe"]) == (0, None)


# --- Manifests -------------------------------------------------------------------------------


def test_a_manifest_makes_the_model_ready(ada, sql, caplog) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()

    with caplog.at_level(logging.INFO):
        response = ada.set_manifest(model["id"], json.dumps(raw_manifest()))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    ready = {**model, "state": "ready", "manifest_summary": RAW_SUMMARY}
    assert response.json() == ready
    assert ada.models().json()["models"] == [ready]
    assert sql("SELECT manifest, verified_at FROM models") == [(raw_manifest(), None)]
    assert f"User {ada.id} set the manifest of model {model['id']}" in caplog.text


def test_the_manifest_reads_back_as_it_was_given(ada) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    given = raw_manifest()
    del given["input"]["features"][0]["norm"]  # a default the manifest leaves out
    ada.set_manifest(model["id"], json.dumps(given))

    response = ada.manifest(model["id"])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == given
    assert list(response.json()) == ["version", "input", "signature", "about"]


def test_a_model_without_a_manifest_needs_setup(ada) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()

    response = ada.manifest(model["id"])

    assert ada.models().json()["models"][0]["state"] == "needs_setup"
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "type": PROBLEMS + "no-manifest",
        "title": "No manifest",
        "status": 404,
        "detail": "This model has no manifest yet. Add one to tell CandleStack how to build its "
        "input.",
    }


def test_a_manifest_that_does_not_fit_the_model_is_refused(ada, sql, caplog) -> None:
    model = ada.upload(keras_file("functional")).json()  # takes [25, 23]

    with caplog.at_level(logging.INFO):
        response = ada.set_manifest(model["id"], json.dumps(raw_manifest()))

    assert response.status_code == 422
    assert response.json() == {
        "type": PROBLEMS + "manifest-check-failed",
        "title": "Manifest check failed",
        "status": 422,
        "detail": "The manifest gives [25, 5], the model takes [25, 23].",
        "problems": [
            {"check": "model", "detail": "The manifest gives [25, 5], the model takes [25, 23]."}
        ],
        "more_problems": 0,
        "unknown_blocks": [],
    }
    assert sql("SELECT manifest FROM models") == [(None,)]
    assert f"A manifest of user {ada.id} was refused (model)" in caplog.text


def test_every_check_runs_and_names_the_unknown_blocks(ada) -> None:
    model = ada.upload(keras_file("functional")).json()
    manifest = raw_manifest()
    manifest["input"]["features"][4]["id"] = "pocket-pivot@1"
    manifest["input"]["features"][1]["name"] = "open"

    problems = ada.set_manifest(model["id"], json.dumps(manifest)).json()

    assert problems["problems"] == [
        {
            "check": "catalog",
            "detail": "The catalog has no block pocket-pivot@1 yet, so it cannot run.",
        },
        {
            "check": "names",
            "detail": "The feature name open is used 2 times. Each feature needs a name of its "
            "own.",
        },
        {"check": "model", "detail": "The manifest gives [25, 5], the model takes [25, 23]."},
    ]
    assert problems["unknown_blocks"] == ["pocket-pivot@1"]


def test_a_document_that_is_no_manifest_is_refused(ada) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()

    response = ada.set_manifest(model["id"], b'{"version": 1}')

    assert response.status_code == 422
    assert [problem["check"] for problem in response.json()["problems"]] == ["schema"] * 2
    assert response.json()["detail"] == "input is missing. signature is missing."


def test_a_long_list_of_problems_is_cut(ada) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    manifest = raw_manifest()
    for feature in manifest["input"]["features"]:
        feature["params"] = {"a": 1, "b": 2, "c": 3}

    problem = ada.set_manifest(model["id"], json.dumps(manifest)).json()

    assert len(problem["problems"]) == 10
    assert problem["more_problems"] == 5
    assert problem["detail"].endswith(
        "input.features[3].params.a is not a parameter of close@1. And 5 more problems."
    )


def test_a_manifest_larger_than_1_mb_is_refused(ada, sql) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    manifest = raw_manifest()
    manifest["about"]["note"] = "x" * MB

    announced = ada.set_manifest(model["id"], json.dumps(manifest))
    streamed = ada.set_manifest(model["id"], chunked(json.dumps(manifest).encode()))

    for response in (announced, streamed):
        assert response.status_code == 413
        assert response.json() == {
            "type": PROBLEMS + "manifest-too-large",
            "title": "Manifest too large",
            "status": 413,
            "detail": "The manifest is larger than 1 MB, the largest one CandleStack keeps. Give "
            "it a smaller example.",
        }
    assert sql("SELECT manifest FROM models") == [(None,)]


def test_a_replaced_manifest_is_no_longer_verified(ada, sql) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    ada.set_manifest(model["id"], json.dumps(raw_manifest()))
    # What the trial run of the worker will do (#268).
    sql("UPDATE models SET verified_at = now()")
    assert ada.models().json()["models"][0]["state"] == "verified"
    manifest = raw_manifest()
    manifest["about"] = {"note": "Trained again"}

    response = ada.set_manifest(model["id"], json.dumps(manifest))

    assert response.json()["state"] == "ready"
    assert ada.manifest(model["id"]).json()["about"] == {"note": "Trained again"}
    assert sql("SELECT verified_at FROM models") == [(None,)]


def test_a_change_keeps_the_manifest(ada) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    ada.set_manifest(model["id"], json.dumps(raw_manifest()))

    response = ada.update(model["id"], starred=True)

    assert response.json()["state"] == "ready"
    assert response.json()["manifest_summary"] == RAW_SUMMARY


def test_the_manifest_of_another_users_model_is_not_found(ada, bob, sql) -> None:
    model = bob.upload(keras_file("raw_candles"), name="raw.keras").json()

    responses = [
        ada.set_manifest(model["id"], json.dumps(raw_manifest())),
        ada.manifest(model["id"]),
        ada.set_manifest("mdl_zzzzzzzzzz", json.dumps(raw_manifest())),
        ada.manifest("mdl_zzzzzzzzzz"),
    ]

    for response in responses:
        assert response.status_code == 404
        assert response.json()["type"] == PROBLEMS + "not-found"
    assert sql("SELECT manifest FROM models") == [(None,)]


# --- Deleting the account --------------------------------------------------------------------


def test_deleting_the_account_deletes_the_models_and_their_files(ada, bob, sql, storage) -> None:
    ada.upload(keras_file("functional"))
    ada.upload(keras_file("sequential"), name="sequential.keras")
    kept = bob.upload(keras_file("functional")).json()

    response = ada.request("DELETE", "/api/v1/me")

    assert response.status_code == 204
    assert sql("SELECT id FROM models") == [(kept["id"],)]
    assert not (storage.root / ada.id).exists()
    assert storage.files() == [storage.file(bob.id, kept["id"])]


def test_deleting_an_account_without_models(ada, storage) -> None:
    assert ada.request("DELETE", "/api/v1/me").status_code == 204
    assert storage.files() == []


def test_the_account_goes_even_when_its_files_cannot(ada, sql, storage, monkeypatch, caplog):
    ada.upload(keras_file("functional"))

    async def fail(*_args) -> None:
        raise PermissionError("/data/models/usr_adaaaaaaaa")

    monkeypatch.setattr(LocalModelStorage, "delete_user", fail)

    with caplog.at_level(logging.ERROR):
        response = ada.request("DELETE", "/api/v1/me")

    assert response.status_code == 204
    assert sql("SELECT id FROM users") == []
    assert sql("SELECT id FROM models") == []
    assert len(storage.files()) == 1  # an orphan, at worst
    assert "Deleting an account's data after the commit failed (PermissionError)" in caplog.text
