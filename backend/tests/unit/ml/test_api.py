"""The models routes up to where they would need Postgres; the flows with it are integration
tests (tests/integration/ml)."""

import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candlestack.accounts import User
from candlestack.accounts.dependencies import current_user, session_user
from candlestack.core import Settings
from candlestack.ml.api import _link_failure
from candlestack.ml.errors import (
    AccountGone,
    LargerThanLink,
    ManifestCheckFailed,
    ModelCheckFailed,
    ModelNameTaken,
    ModelTooLarge,
    StorageFull,
    TooManyModels,
    UnsupportedModelFormat,
    UploadInProgress,
    UploadTimeout,
)
from candlestack.ml.problems import manifest_check_failed, upload_problem
from candlestack.ml.schemas import ModelCheck
from candlestack.ml.service import UploadFailure
from candlestack.preprocessing import Finding, ManifestProblem

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}
MB = 1024 * 1024
USER = User(
    id="usr_7f3k2m9qx4",
    display_name="Ada",
    email="ada@example.com",
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    last_sign_in_at=datetime(2026, 10, 1, tzinfo=UTC),
)


@pytest.fixture
def signed_in(app: FastAPI) -> FastAPI:
    app.dependency_overrides[current_user] = lambda: USER
    return app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/models"),
        ("POST", "/api/v1/models?name=lstm.keras"),
        ("PATCH", "/api/v1/models/mdl_7f3k2m9qx4"),
        ("DELETE", "/api/v1/models/mdl_7f3k2m9qx4"),
        ("PUT", "/api/v1/models/mdl_7f3k2m9qx4/manifest"),
        ("GET", "/api/v1/models/mdl_7f3k2m9qx4/manifest"),
        ("POST", "/api/v1/models/uploads"),
        ("GET", "/api/v1/models/uploads/upl_7f3k2m9qx4"),
        ("GET", "/api/v1/uploads/" + "q" * 40),
    ],
)
def test_the_models_need_a_session(client: TestClient, method: str, path: str) -> None:
    json = {"starred": True} if method == "PATCH" else None

    response = client.request(method, path, headers=ORIGIN, json=json)

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/models?name=lstm.keras"),
        ("PATCH", "/api/v1/models/mdl_7f3k2m9qx4"),
        ("DELETE", "/api/v1/models/mdl_7f3k2m9qx4"),
        ("PUT", "/api/v1/models/mdl_7f3k2m9qx4/manifest"),
        ("POST", "/api/v1/models/uploads"),
    ],
)
@pytest.mark.usefixtures("signed_in")
def test_changes_need_the_apps_own_origin(client: TestClient, method: str, path: str) -> None:
    json = {"starred": True} if method == "PATCH" else None

    response = client.request(method, path, headers={"Origin": "https://evil.example"}, json=json)

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"


@pytest.mark.parametrize(
    "name", ["photo.png", "model.pt", "weights.h5", "model.onnx.zip", "lstm.keras.zip"]
)
@pytest.mark.usefixtures("signed_in")
def test_other_files_are_not_supported(client: TestClient, name: str) -> None:
    response = client.post("/api/v1/models", params={"name": name}, content=b"x", headers=ORIGIN)

    assert response.status_code == 415
    assert response.json() == {
        "type": PROBLEMS + "unsupported-model-format",
        "title": "Unsupported model format",
        "status": 415,
        "detail": "This file is not supported. CandleStack takes Keras models (.keras) and ONNX "
        "models (.onnx). Export PyTorch, TensorFlow and scikit-learn models to ONNX. "
        "Pickle-based files (.pt, .pth, .pkl, .joblib) are never taken, as loading them can run "
        "any code.",
    }


@pytest.mark.parametrize("name", ["lstm.keras", "mixer.onnx"])
@pytest.mark.usefixtures("signed_in")
def test_a_file_announced_larger_than_50_mb_is_refused_at_once(
    client: TestClient, name: str
) -> None:
    response = client.post(
        "/api/v1/models",
        params={"name": name},
        content=b"x",
        headers={**ORIGIN, "Content-Length": str(50 * MB + 1)},
    )

    assert response.status_code == 413
    assert response.json() == {
        "type": PROBLEMS + "model-too-large",
        "title": "Model too large",
        "status": 413,
        "detail": "The file is larger than 50 MB, the largest file an upload takes. Upload a "
        "smaller model.",
    }


@pytest.mark.usefixtures("signed_in")
def test_a_manifest_announced_larger_than_1_mb_is_refused_at_once(client: TestClient) -> None:
    response = client.put(
        "/api/v1/models/mdl_7f3k2m9qx4/manifest",
        content=b"{}",
        headers={**ORIGIN, "Content-Length": str(MB + 1)},
    )

    assert response.status_code == 413
    assert response.json()["type"] == PROBLEMS + "manifest-too-large"


def test_a_refused_manifest_shows_10_problems_and_counts_the_rest() -> None:
    problems = [
        ManifestProblem(
            "params",
            tuple(
                Finding(f"Problem {number}.", f"input.features[{number}].params.window", ())
                for number in range(1, 13)
            ),
        ),
        ManifestProblem(
            "model",
            (Finding("The manifest gives [25, 5], the model takes [25, 23].", "x", ([25, 23],)),),
        ),
    ]

    problem = manifest_check_failed(ManifestCheckFailed(problems, ["rsi@1"]))

    assert (problem.status, problem.slug, problem.title) == (
        422,
        "manifest-check-failed",
        "Manifest check failed",
    )
    assert problem.detail == (
        "Problem 1. Problem 2. Problem 3. Problem 4. Problem 5. Problem 6. Problem 7. "
        "Problem 8. Problem 9. Problem 10. And 3 more problems."
    )
    assert problem.extensions == {
        "problems": [
            {
                "check": "params",
                "field": f"input.features[{number}].params.window",
                "detail": f"Problem {number}.",
                "allowed": [],
            }
            for number in range(1, 11)
        ],
        "more_problems": 3,
        "unknown_blocks": ["rsi@1"],
    }


def test_one_problem_more_is_one_problem() -> None:
    problems = [
        ManifestProblem("params", tuple(Finding(f"Problem {number}.") for number in range(11)))
    ]

    problem = manifest_check_failed(ManifestCheckFailed(problems, []))

    assert problem.detail.endswith("Problem 9. And 1 more problem.")
    assert problem.extensions["more_problems"] == 1


@pytest.mark.parametrize(
    "params", [{}, {"name": ""}, {"name": "a" * 250 + ".keras"}], ids=["none", "empty", "long"]
)
@pytest.mark.usefixtures("signed_in")
def test_an_upload_needs_a_file_name(client: TestClient, params: dict[str, str]) -> None:
    response = client.post("/api/v1/models", params=params, content=b"x", headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.usefixtures("signed_in")
def test_a_rename_is_validated(client: TestClient) -> None:
    response = client.patch("/api/v1/models/mdl_7f3k2m9qx4", json={"name": "a/b"}, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


def test_the_app_starts_with_its_model_storage(settings: Settings, client: TestClient) -> None:
    assert settings.model_storage_dir.is_dir()
    assert (settings.model_storage_dir / ".incoming").is_dir()


def test_the_default_model_storage_is_relative() -> None:
    assert Settings().model_storage_dir == Path("var/models")


@pytest.mark.parametrize(
    ("error", "status", "slug"),
    [
        (UnsupportedModelFormat(), 415, "unsupported-model-format"),
        (ModelTooLarge(), 413, "model-too-large"),
        (StorageFull(10, 20), 409, "storage-full"),
        (TooManyModels(), 409, "too-many-models"),
        (UploadInProgress(), 409, "upload-in-progress"),
        (UploadTimeout(), 408, "upload-timeout"),
        (ModelCheckFailed(ModelCheck.weights, "No weights."), 422, "model-check-failed"),
        (AccountGone(), 401, "not-signed-in"),
        (ModelNameTaken("lstm.keras"), 409, "model-name-taken"),
    ],
)
def test_each_refused_upload_has_its_problem_and_is_logged_by_ids(
    error: Exception, status: int, slug: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        problem = upload_problem("usr_7f3k2m9qx4", error)

    assert (problem.status, problem.slug) == (status, slug)
    assert ";" not in problem.detail
    assert f"An upload of user usr_7f3k2m9qx4 was refused ({slug}" in caplog.text


def test_the_timeout_problem_names_the_minutes() -> None:
    assert upload_problem("usr_7f3k2m9qx4", UploadTimeout()).detail == (
        "The file did not arrive within 2 minutes. Check the connection and upload it again."
    )


# --- Upload links --------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["short", "Q" * 40, "q" * 41, "q" * 39 + "1"])
def test_an_upload_link_needs_no_session_nor_the_apps_origin(
    client: TestClient, token: str
) -> None:
    response = client.put(
        f"/api/v1/uploads/{token}", content=b"x", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 404
    assert response.json() == {
        "type": PROBLEMS + "upload-link-not-found",
        "title": "Upload link not found",
        "status": 404,
        "detail": "No upload link has this token. Check that the whole link was copied, or ask "
        "for a new one.",
    }


def test_an_upload_link_is_read_by_its_token_only_with_the_session(client: TestClient) -> None:
    response = client.get("/api/v1/uploads/" + "q" * 40, headers={"Authorization": "Bearer cs_any"})

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "session-required"


@pytest.mark.parametrize("token", ["short", "Q" * 40, "q" * 41])
def test_a_token_not_of_its_form_is_not_found(app: FastAPI, client: TestClient, token: str) -> None:
    app.dependency_overrides[session_user] = lambda: USER

    response = client.get(f"/api/v1/uploads/{token}")

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "upload-link-not-found"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "lstm.keras"},
        {"size_bytes": 10},
        {"name": "", "size_bytes": 10},
        {"name": "lstm.keras", "size_bytes": 0},
        {"name": "lstm.keras", "size_bytes": 10, "url": "https://evil.example"},
    ],
    ids=["no size", "no name", "empty name", "empty file", "unknown field"],
)
@pytest.mark.usefixtures("signed_in")
def test_an_upload_link_is_validated(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/api/v1/models/uploads", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.usefixtures("signed_in")
def test_an_upload_link_for_another_format_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/models/uploads", json={"name": "model.pt", "size_bytes": 10}, headers=ORIGIN
    )

    assert response.status_code == 415
    assert response.json()["type"] == PROBLEMS + "unsupported-model-format"


@pytest.mark.usefixtures("signed_in")
def test_an_upload_link_for_more_than_50_mb_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/models/uploads",
        json={"name": "lstm.keras", "size_bytes": 50 * MB + 1},
        headers=ORIGIN,
    )

    assert response.status_code == 413
    assert response.json()["type"] == PROBLEMS + "model-too-large"


@pytest.mark.parametrize(
    ("error", "failure"),
    [
        (
            ModelCheckFailed(ModelCheck.weights, "No weights."),
            UploadFailure("model-check-failed", "No weights.", ModelCheck.weights),
        ),
        (
            LargerThanLink(1000),
            UploadFailure(
                "larger-than-link",
                "The file is larger than the 1000 bytes this link was made for. Ask for a new "
                "link with the file's size.",
            ),
        ),
        (
            AccountGone(),
            UploadFailure(
                "upload-link-gone",
                "The account of this link was deleted while the file was uploading, so it was "
                "not kept.",
            ),
        ),
        (
            UploadTimeout(),
            UploadFailure(
                "upload-timeout",
                "The file did not arrive within 2 minutes. Check the connection and upload it "
                "again.",
            ),
        ),
        (RuntimeError("unexpected"), None),
    ],
)
def test_what_a_link_records_of_a_failed_upload(
    error: Exception, failure: UploadFailure | None
) -> None:
    assert _link_failure(error) == failure
