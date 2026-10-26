"""Uploading a model over HTTP with the built image: its migrated table, its read-only root
filesystem with the models volume, and the upload check without Keras or onnxruntime in the
image (an ONNX file is read with onnx)."""

from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

ORIGIN = {"Origin": "http://testserver"}
FIXTURES = Path(__file__).parents[1] / "fixtures" / "keras"


def test_upload_list_and_delete_a_model(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)
    content = (FIXTURES / "functional.keras").read_bytes()

    uploaded = http.post(
        "/api/v1/models",
        params={"name": "functional.keras"},
        content=content,
        headers={**ORIGIN, "Content-Type": "application/octet-stream"},
    )

    assert uploaded.status_code == 201
    model = uploaded.json()
    assert model["parameters"] == 9325
    assert model["input_shape"] == [25, 23]
    listed = http.get("/api/v1/models").json()
    assert model in listed["models"]

    deleted = http.delete(f"/api/v1/models/{model['id']}", headers=ORIGIN)

    assert deleted.status_code == 204
    assert model["id"] not in [item["id"] for item in http.get("/api/v1/models").json()["models"]]


def test_a_model_with_custom_code_is_refused(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)

    response = http.post(
        "/api/v1/models",
        params={"name": "lambda_layer.keras"},
        content=(FIXTURES / "lambda_layer.keras").read_bytes(),
        headers=ORIGIN,
    )

    assert response.status_code == 422
    assert response.json()["check"] == "opens"


def test_an_onnx_model_is_checked_in_the_image(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)
    content = (FIXTURES.parent / "onnx" / "raw_candles.onnx").read_bytes()

    uploaded = http.post(
        "/api/v1/models", params={"name": "raw_candles.onnx"}, content=content, headers=ORIGIN
    )

    assert uploaded.status_code == 201, uploaded.text
    model = uploaded.json()
    assert (model["framework"], model["input_shape"], model["output_shape"]) == (
        "onnx",
        [25, 5],
        [1],
    )
    assert http.delete(f"/api/v1/models/{model['id']}", headers=ORIGIN).status_code == 204
