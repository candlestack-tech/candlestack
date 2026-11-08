"""The dry run of a manifest over HTTP, ``POST /api/v1/models/{model_id}/manifest/check``,
against a real Postgres (conftest.py): the problems in a form to fix them from, and nothing
kept."""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest

from candlestack.core import Settings

pytestmark = pytest.mark.integration

PROBLEMS = "https://candlestack.tech/problems/"
MB = 1024 * 1024
FIXTURES = Path(__file__).parents[2] / "fixtures" / "keras"


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Ada is on the beta list, so her API keys sign in."""
    return settings.model_copy(update={"beta_allowlist": frozenset({"usr_adaaaaaaaa@example.com"})})


def keras_file(name: str) -> bytes:
    return (FIXTURES / f"{name}.keras").read_bytes()


def raw_manifest() -> dict[str, Any]:
    """The manifest of ``raw_candles.keras``: 25 candles of 5 columns, no preprocessing."""
    return json.loads((FIXTURES / "raw_candles.manifest.json").read_text(encoding="utf-8"))


def test_a_manifest_is_checked_without_being_kept(ada, sql) -> None:
    model = ada.upload(keras_file("functional")).json()  # takes [25, 23]
    manifest = raw_manifest()
    manifest["input"]["session"] = "us"

    refused = ada.check_manifest(model["id"], json.dumps(manifest))
    fitting = ada.check_manifest(model["id"], json.dumps(raw_manifest()))

    assert refused.status_code == 200
    assert refused.json() == {
        "valid": False,
        "problems": [
            {
                "check": "schema",
                "field": "input.session",
                "detail": "input.session: Input should be 'regular' or '24/7'.",
                "allowed": ["regular", "24/7"],
            }
        ],
        "more_problems": 0,
        "unknown_blocks": [],
    }
    assert fitting.json()["problems"] == [
        {
            "check": "model",
            "field": "signature.input.shape",
            "detail": "The manifest gives [25, 5], the model takes [25, 23].",
            "allowed": [[25, 23]],
        }
    ]
    assert sql("SELECT manifest FROM models") == [(None,)]


def test_a_manifest_that_passes_the_check_is_valid_and_still_not_kept(ada, sql) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()

    response = ada.check_manifest(model["id"], json.dumps(raw_manifest()))

    assert response.status_code == 200
    assert response.json() == {
        "valid": True,
        "problems": [],
        "more_problems": 0,
        "unknown_blocks": [],
    }
    assert ada.models().json()["models"][0]["state"] == "needs_setup"
    assert sql("SELECT manifest FROM models") == [(None,)]


def test_checking_a_manifest_needs_only_the_scope_read(ada, client) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    secrets = {
        scope: ada.request(
            "POST", "/api/v1/me/api-keys", json={"name": scope, "scopes": [scope]}
        ).json()["secret"]
        for scope in ("read", "backtests")
    }

    client.cookies = httpx2.Cookies()

    def check(secret: str) -> Any:
        return client.post(
            f"/api/v1/models/{model['id']}/manifest/check",
            content=json.dumps(raw_manifest()),
            headers={"Authorization": f"Bearer {secret}"},
        )

    assert check(secrets["read"]).json()["valid"] is True
    refused = check(secrets["backtests"])
    assert refused.status_code == 403
    assert refused.json()["scope"] == "read"


def test_checking_the_manifest_of_another_users_model_is_not_found(ada, bob) -> None:
    model = bob.upload(keras_file("raw_candles"), name="raw.keras").json()

    response = ada.check_manifest(model["id"], json.dumps(raw_manifest()))

    assert response.status_code == 404
    assert response.json()["type"] == PROBLEMS + "not-found"


def test_checking_a_manifest_larger_than_1_mb_is_refused(ada) -> None:
    model = ada.upload(keras_file("raw_candles"), name="raw.keras").json()
    manifest = raw_manifest()
    manifest["about"]["note"] = "x" * MB

    response = ada.check_manifest(model["id"], json.dumps(manifest))

    assert response.status_code == 413
    assert response.json()["type"] == PROBLEMS + "manifest-too-large"
