"""Uploading a model through a one-time link over HTTP, against a real Postgres and Redis
(conftest.py): making the link, the upload with no other sign-in than its token, how the
upload stands, and the links that are used, expired, too small or refused."""

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from redis import Redis
from starlette.requests import ClientDisconnect

from candlestack.core import Settings
from candlestack.ml.schemas import QUOTA_BYTES

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"
FUNCTIONAL = (FIXTURES / "functional.keras").read_bytes()
LINKS = "/api/v1/models/uploads"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Ada is on the beta list, so her API keys sign in."""
    return settings.model_copy(update={"beta_allowlist": frozenset({"usr_adaaaaaaaa@example.com"})})


def make_link(user, name: str = "functional.keras", size: int = len(FUNCTIONAL)) -> dict[str, Any]:
    response = user.request("POST", LINKS, json={"name": name, "size_bytes": size})
    assert response.status_code == 201, response.text
    return response.json()


def status(user, link: dict[str, Any]) -> dict[str, Any]:
    response = user.request("GET", f"{LINKS}/{link['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def put(client: TestClient, link: dict[str, Any], content: Any, **headers: str) -> httpx2.Response:
    """The upload as ``curl -T`` sends it: the file and nothing that signs it in."""
    client.cookies = httpx2.Cookies()
    return client.put(link["url"], content=content, headers=headers)


def chunked(content: bytes, size: int = 64 * 1024) -> Any:
    """The body as a stream: sent without a Content-Length."""
    for start in range(0, len(content), size):
        yield content[start : start + size]


def problem(response: httpx2.Response, status_code: int, slug: str) -> dict[str, Any]:
    assert response.status_code == status_code, response.text
    body = response.json()
    assert body["type"] == PROBLEMS + slug
    return body


def fill_storage(sql, user_id: str, size: int) -> None:
    """A model of `size` bytes, straight in the table."""
    sql(
        "INSERT INTO models (id, user_id, name, framework, size_bytes, sha256, parameters, "
        "keras_version, created_at) VALUES ('mdl_bigbigbigb', :user_id, 'big.keras', 'keras', "
        ":size, :sha, 1, '3.15.1', now())",
        user_id=user_id,
        size=size,
        sha=b"\0" * 32,
    )


def token(link: dict[str, Any]) -> str:
    return link["url"].rsplit("/", 1)[1]


# --- Making a link ------------------------------------------------------------------------------


def test_a_link_is_made_and_keeps_only_its_hash(ada, sql) -> None:
    response = ada.request(
        "POST", LINKS, json={"name": "C:\\models\\my lstm.keras", "size_bytes": 1234}
    )

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    link = response.json()
    secret = token(link)
    assert link["id"].startswith("upl_")
    assert link["url"] == f"http://testserver/api/v1/uploads/{secret}"
    assert len(secret) == 40
    assert link["command"] == f"curl --fail-with-body -T 'C:\\models\\my lstm.keras' {link['url']}"
    created = datetime.fromisoformat(link["created_at"])
    assert datetime.fromisoformat(link["expires_at"]) - created == timedelta(minutes=15)
    assert {key: link[key] for key in ("name", "size_bytes", "status", "model_id", "problem")} == {
        "name": "my lstm.keras",
        "size_bytes": 1234,
        "status": "waiting",
        "model_id": None,
        "problem": None,
    }
    [(user_id, token_hash, framework)] = sql(
        "SELECT user_id, token_hash, framework FROM model_uploads"
    )
    assert (user_id, bytes(token_hash), framework) == (
        ada.id,
        hashlib.sha256(secret.encode()).digest(),
        "keras",
    )
    shown = status(ada, link)
    assert shown == {key: value for key, value in link.items() if key not in ("url", "command")}
    assert secret not in str(shown)


def test_a_link_must_fit_the_storage(ada, sql) -> None:
    fill_storage(sql, ada.id, QUOTA_BYTES - 100)

    body = problem(
        ada.request("POST", LINKS, json={"name": "lstm.keras", "size_bytes": 101}),
        409,
        "storage-full",
    )

    assert body["used_bytes"] == QUOTA_BYTES - 100
    assert sql("SELECT id FROM model_uploads") == []
    assert ada.request("POST", LINKS, json={"name": "lstm.keras", "size_bytes": 100}).is_success


def test_a_link_is_made_for_a_long_path(ada) -> None:
    path = "/home/agent/" + "runs/" * 60 + "lstm.keras"

    assert make_link(ada, name=path)["name"] == "lstm.keras"


def test_a_key_with_the_scope_models_makes_a_link(ada, client) -> None:
    def key(scopes: list[str]) -> str:
        created = ada.request("POST", "/api/v1/me/api-keys", json={"name": "a", "scopes": scopes})
        return created.json()["secret"]

    models, read = key(["models"]), key(["read"])
    client.cookies = httpx2.Cookies()
    body = {"name": "lstm.keras", "size_bytes": 10}

    made = client.post(LINKS, json=body, headers={"Authorization": f"Bearer {models}"})
    refused = client.post(LINKS, json=body, headers={"Authorization": f"Bearer {read}"})
    read_back = client.get(
        f"{LINKS}/{made.json()['id']}", headers={"Authorization": f"Bearer {read}"}
    )

    assert made.status_code == 201
    assert problem(refused, 403, "missing-scope")["scope"] == "models"
    assert read_back.json()["status"] == "waiting"


def test_links_that_expired_a_day_ago_go_with_the_next_one(ada, sql) -> None:
    old, recent = make_link(ada), make_link(ada)
    sql(
        "UPDATE model_uploads SET expires_at = :at WHERE id = :id",
        at=datetime.now(UTC) - timedelta(days=1, minutes=1),
        id=old["id"],
    )

    newest = make_link(ada)

    assert {row[0] for row in sql("SELECT id FROM model_uploads")} == {recent["id"], newest["id"]}
    assert ada.request("GET", f"{LINKS}/{old['id']}").status_code == 404


def test_another_users_link_is_not_found(ada, bob) -> None:
    link = make_link(ada)

    problem(bob.request("GET", f"{LINKS}/{link['id']}"), 404, "not-found")


# --- The upload ---------------------------------------------------------------------------------


def test_the_upload_needs_only_the_link(ada, client, storage, sql, caplog) -> None:
    link = make_link(ada, name="my lstm.keras")

    with caplog.at_level(logging.INFO):
        response = put(client, link, FUNCTIONAL)

    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    model = response.json()
    assert (model["name"], model["size_bytes"], model["parameters"]) == (
        "my lstm.keras",
        len(FUNCTIONAL),
        9325,
    )
    assert storage.file(ada.id, model["id"]).read_bytes() == FUNCTIONAL
    assert storage.incoming() == []
    assert ada.request("GET", "/api/v1/models").json()["models"] == [model]
    done = status(ada, link)
    assert (done["status"], done["model_id"], done["problem"]) == ("done", model["id"], None)
    [(started, finished)] = sql("SELECT started_at, finished_at FROM model_uploads")
    assert started is not None
    assert finished is not None
    ours = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("candlestack"))
    assert token(link) not in ours
    assert f"through the link {link['id']}" in ours


def test_a_session_or_a_key_does_not_change_whose_model_it_is(ada, bob, client) -> None:
    link = make_link(ada)

    response = bob.request(
        "PUT", link["url"], content=FUNCTIONAL, headers={"Authorization": "Bearer cs_wrong"}
    )

    assert response.status_code == 201
    assert [m["id"] for m in ada.request("GET", "/api/v1/models").json()["models"]] == [
        response.json()["id"]
    ]
    assert bob.request("GET", "/api/v1/models").json()["models"] == []


def test_a_session_does_not_stand_in_for_the_token(ada, client) -> None:
    make_link(ada)

    response = ada.request("PUT", "/api/v1/uploads/" + "q" * 40, content=FUNCTIONAL)

    problem(response, 404, "upload-link-not-found")
    assert ada.request("GET", "/api/v1/models").json()["models"] == []


def test_a_link_takes_one_upload(ada, client) -> None:
    link = make_link(ada)
    assert put(client, link, FUNCTIONAL).status_code == 201

    body = problem(put(client, link, FUNCTIONAL), 410, "upload-link-gone")

    assert body["reason"] == "used"
    assert body["detail"] == (
        "This upload link took its file already: a link takes one upload. Ask for a new link."
    )
    assert len(ada.request("GET", "/api/v1/models").json()["models"]) == 1


def test_an_expired_link(ada, client, sql) -> None:
    link = make_link(ada)
    sql("UPDATE model_uploads SET expires_at = now() - interval '1 second'")

    body = problem(put(client, link, FUNCTIONAL), 410, "upload-link-gone")

    assert body["reason"] == "expired"
    assert "within 15 minutes" in body["detail"]
    assert status(ada, link)["status"] == "expired"
    assert ada.request("GET", "/api/v1/models").json()["models"] == []


def test_a_file_announced_larger_than_the_link_leaves_it_unused(ada, client) -> None:
    link = make_link(ada, size=len(FUNCTIONAL))

    body = problem(put(client, link, FUNCTIONAL + b"\0" * 10), 413, "larger-than-link")

    assert body["size_bytes"] == len(FUNCTIONAL)
    assert status(ada, link)["status"] == "waiting"
    assert put(client, link, FUNCTIONAL).status_code == 201


def test_a_file_larger_than_the_link_stops_while_it_streams(ada, client, storage) -> None:
    link = make_link(ada, size=len(FUNCTIONAL) - 1)

    problem(put(client, link, chunked(FUNCTIONAL)), 413, "larger-than-link")

    failed = status(ada, link)
    assert (failed["status"], failed["problem"], failed["check"]) == (
        "failed",
        "larger-than-link",
        None,
    )
    assert failed["error"].startswith("The file is larger than the")
    assert storage.incoming() == []
    problem(put(client, link, FUNCTIONAL), 410, "upload-link-gone")


def test_a_failed_check_is_recorded(ada, client, storage) -> None:
    link = make_link(ada, name="functional.onnx")

    response = put(client, link, FUNCTIONAL)

    assert problem(response, 422, "model-check-failed")["check"] == "opens"
    failed = status(ada, link)
    assert (failed["status"], failed["problem"], failed["check"], failed["error"]) == (
        "failed",
        "model-check-failed",
        "opens",
        response.json()["detail"],
    )
    assert storage.files() == []


def test_the_quota_is_checked_again_when_the_file_comes(ada, client, sql) -> None:
    link = make_link(ada)
    fill_storage(sql, ada.id, QUOTA_BYTES - 100)

    problem(put(client, link, FUNCTIONAL), 409, "storage-full")

    assert status(ada, link)["problem"] == "storage-full"


def test_another_upload_in_progress_leaves_the_link_unused(ada, client, redis_url, caplog) -> None:
    link = make_link(ada)
    with Redis.from_url(redis_url) as redis, caplog.at_level(logging.INFO):
        redis.set(f"ml:upload:{ada.id}", "another", ex=60)
        response = put(client, link, FUNCTIONAL)
        redis.delete(f"ml:upload:{ada.id}")

    problem(response, 409, "upload-in-progress")
    assert (
        f"An upload of user {ada.id} through the link {link['id']} was refused "
        "(another upload in progress)"
    ) in caplog.messages
    assert status(ada, link)["status"] == "waiting"
    assert put(client, link, FUNCTIONAL).status_code == 201


@pytest.mark.parametrize(
    ("stop", "status_code"),
    [
        # The client went away (Starlette's error for it): an answer nobody reads.
        (ClientDisconnect(), 400),
        # Anything else that breaks the stream: an internal error.
        (ConnectionResetError("reset by peer"), 500),
    ],
)
def test_an_upload_that_stops_midway_is_recorded_as_interrupted(
    ada, client, storage, redis_url, stop: Exception, status_code: int
) -> None:
    link = make_link(ada)

    def stops() -> Any:
        yield FUNCTIONAL[:1024]
        yield FUNCTIONAL[1024:2048]
        raise stop

    response = put(client, link, stops())

    assert response.status_code == status_code
    failed = status(ada, link)
    assert (failed["status"], failed["problem"], failed["model_id"]) == (
        "failed",
        "upload-interrupted",
        None,
    )
    assert failed["error"].startswith("The upload stopped before the file was checked.")
    assert storage.incoming() == []
    with Redis.from_url(redis_url) as redis:
        assert redis.get(f"ml:upload:{ada.id}") is None
    problem(put(client, link, FUNCTIONAL), 410, "upload-link-gone")
    assert put(client, make_link(ada), FUNCTIONAL).status_code == 201


def test_an_upload_being_checked(ada, sql) -> None:
    link = make_link(ada)
    sql("UPDATE model_uploads SET status = 'checking', started_at = now()")

    assert status(ada, link)["status"] == "checking"


def test_links_go_with_the_account(ada, sql) -> None:
    make_link(ada)

    assert ada.request("DELETE", "/api/v1/me").status_code == 204
    assert sql("SELECT id FROM model_uploads") == []
