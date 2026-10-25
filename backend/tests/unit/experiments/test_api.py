"""The backtests and dashboard routes up to where they would need Postgres; the flows with it
are integration tests (tests/integration/experiments)."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from candlestack.accounts import User
from candlestack.accounts.dependencies import current_user
from candlestack.experiments import model_usage

PROBLEMS = "https://candlestack.tech/problems/"
ORIGIN = {"Origin": "http://testserver"}
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
        ("GET", "/api/v1/backtests"),
        ("PATCH", "/api/v1/backtests/bt_7f3k2m9qx4"),
        ("GET", "/api/v1/dashboard"),
        ("POST", "/api/v1/backtests"),
        ("GET", "/api/v1/backtests/bt_7f3k2m9qx4"),
        ("GET", "/api/v1/backtests/bt_7f3k2m9qx4/download"),
    ],
)
def test_the_backtests_need_a_session(client: TestClient, method: str, path: str) -> None:
    json = {"starred": True} if method == "PATCH" else None
    if method == "POST":
        json = {
            "model": {"id": "mdl_7f3k2m9qx4"},
            "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 0, "end": 86400}},
        }

    response = client.request(method, path, headers=ORIGIN, json=json)

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


@pytest.mark.usefixtures("signed_in")
def test_starting_a_backtest_needs_the_apps_own_origin(client: TestClient) -> None:
    response = client.post("/api/v1/backtests", json={}, headers={"Origin": "https://evil.example"})

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"model": {"id": "mdl_x"}, "data": {"instrument": {"id": "stock:AAPL"}}},
        {
            "model": {"id": "mdl_x"},
            "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 9, "end": 1}},
        },
    ],
)
def test_a_new_backtest_without_its_groups_is_a_422(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/api/v1/backtests", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.usefixtures("signed_in")
def test_a_star_needs_the_apps_own_origin(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/backtests/bt_7f3k2m9qx4",
        json={"starred": True},
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "cross-origin-request"


@pytest.mark.parametrize(
    "params",
    [
        {"q": "a" * 101},
        {"show": "running"},
        {"sort": "name"},
        {"order": "up"},
        {"page": "0"},
        {"page": "100001"},
        {"page": "two"},
        {"model": "m" * 256},
    ],
    ids=["long-q", "show", "sort", "order", "page-0", "page-huge", "page-text", "long-model"],
)
@pytest.mark.usefixtures("signed_in")
def test_the_list_query_is_validated(client: TestClient, params: dict[str, str]) -> None:
    response = client.get("/api/v1/backtests", params=params)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.parametrize(
    "body",
    [{}, {"starred": "maybe"}, {"starred": True, "number": 3}],
    ids=["empty", "not-bool", "extra"],
)
@pytest.mark.usefixtures("signed_in")
def test_a_star_is_validated(client: TestClient, body: dict[str, object]) -> None:
    response = client.patch("/api/v1/backtests/bt_7f3k2m9qx4", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


def test_the_app_counts_the_models_backtests_through_experiments(app: FastAPI) -> None:
    assert app.state.model_usage is model_usage
