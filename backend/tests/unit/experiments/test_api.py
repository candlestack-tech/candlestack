"""The backtests and dashboard routes up to where they would need Postgres; the flows with it
are integration tests (tests/integration/experiments)."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.requests import Request

from candlestack.accounts import HEAVY, Scope, User
from candlestack.accounts.dependencies import Caller, authenticate, current_user
from candlestack.accounts.limits import request_cost
from candlestack.experiments import backtests_router, model_usage

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
    app.dependency_overrides[authenticate] = lambda: Caller(USER)  # the routes that take CallerDep
    return app


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/backtests"),
        ("PATCH", "/api/v1/backtests/bt_7f3k2m9qx4"),
        ("GET", "/api/v1/dashboard"),
        ("GET", "/api/v1/experiments"),
        ("POST", "/api/v1/backtests"),
        ("POST", "/api/v1/backtests/check"),
        ("GET", "/api/v1/backtests/bt_7f3k2m9qx4"),
        ("GET", "/api/v1/backtests/bt_7f3k2m9qx4/download"),
        ("POST", "/api/v1/backtests/bt_7f3k2m9qx4/cancel"),
        ("GET", "/api/v1/compare?ids=bt_7f3k2m9qx4,bt_t9n8v7d6a5"),
        ("POST", "/api/v1/sweeps"),
        ("GET", "/api/v1/sweeps/sw_7f3k2m9qx4"),
    ],
)
def test_the_backtests_need_a_session(client: TestClient, method: str, path: str) -> None:
    json = {"starred": True} if method == "PATCH" else None
    new = {
        "model": {"id": "mdl_7f3k2m9qx4"},
        "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 0, "end": 86400}},
    }
    if path in {"/api/v1/backtests", "/api/v1/backtests/check"}:
        json = new
    if path == "/api/v1/sweeps":
        json = {"base": new, "vary": {"models": ["mdl_7f3k2m9qx4", "mdl_t9n8v7d6a5"]}}

    response = client.request(method, path, headers=ORIGIN, json=json)

    assert response.status_code == 401
    assert response.json()["type"] == PROBLEMS + "not-signed-in"


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/backtests",
        "/api/v1/backtests/check",
        "/api/v1/backtests/bt_7f3k2m9qx4/cancel",
        "/api/v1/sweeps",
    ],
)
def test_starting_or_cancelling_a_backtest_needs_the_apps_own_origin(
    client: TestClient, path: str
) -> None:
    response = client.post(path, json={}, headers={"Origin": "https://evil.example"})

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
@pytest.mark.parametrize("path", ["/api/v1/backtests", "/api/v1/backtests/check"])
def test_a_new_backtest_without_its_groups_is_a_422(
    client: TestClient, body: dict[str, object], path: str
) -> None:
    response = client.post(path, json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.usefixtures("signed_in")
@pytest.mark.parametrize(
    "body",
    [
        {},
        {"vary": {"rules": [{"name": "threshold", "limit": 0.1}]}},
        {"base": {"model": {"id": "mdl_x"}}, "vary": {}},
        {
            "base": {
                "model": {"id": "mdl_x"},
                "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 0, "end": 9}},
            },
            "metric": "num_trades",
        },
    ],
    ids=["empty", "no-base", "base-without-data", "metric-without-direction"],
)
def test_a_new_sweep_without_a_base_or_with_a_wrong_metric_is_a_422(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post("/api/v1/sweeps", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.usefixtures("signed_in")
def test_a_mistyped_dry_run_flag_is_a_422_not_a_sweep(client: TestClient) -> None:
    body = {
        "base": {
            "model": {"id": "mdl_x"},
            "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 0, "end": 9}},
        },
        "vary": {"rules": [{"name": "threshold", "limit": 0.2}]},
        "validateOnly": True,
    }

    response = client.post("/api/v1/sweeps", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.fixture
def read_key(app: FastAPI) -> FastAPI:
    app.dependency_overrides[authenticate] = lambda: Caller(USER, frozenset({Scope.read}))
    return app


@pytest.mark.usefixtures("read_key")
def test_a_read_key_cannot_start_a_sweep(client: TestClient) -> None:
    body = {
        "base": {
            "model": {"id": "mdl_x"},
            "data": {"instrument": {"id": "stock:AAPL"}, "period": {"start": 0, "end": 9}},
        },
        "vary": {"rules": [{"name": "threshold", "limit": 0.2}]},
        "validate_only": True,
    }

    response = client.post("/api/v1/sweeps", json=body, headers=ORIGIN)

    assert response.status_code == 403
    assert response.json()["type"] == PROBLEMS + "missing-scope"
    assert response.json()["scope"] == "backtests"


@pytest.mark.usefixtures("read_key")
def test_a_read_key_reads_a_sweep(client: TestClient) -> None:
    # Past the scope check: the query is checked next.
    response = client.get("/api/v1/sweeps/sw_7f3k2m9qx4", params={"metric": "exposure"})

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.usefixtures("signed_in")
def test_a_sweep_is_ranked_only_by_a_statistic_with_a_direction(client: TestClient) -> None:
    response = client.get("/api/v1/sweeps/sw_7f3k2m9qx4", params={"metric": "exposure"})

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
        {"sort": "title"},
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
    "params",
    [
        {"q": "a" * 101},
        {"mine": "maybe"},
        {"author": "u" * 33},
        {"market": "forex"},
        {"symbol": "s" * 33},
        {"representation": "ticks"},
        {"timeframe": "2h"},
        {"sort": "number"},
        {"order": "up"},
        {"page": "0"},
        {"page": "100001"},
    ],
    ids=[
        "long-q",
        "mine",
        "long-author",
        "market",
        "long-symbol",
        "representation",
        "timeframe",
        "sort",
        "order",
        "page-0",
        "page-huge",
    ],
)
@pytest.mark.usefixtures("signed_in")
def test_the_experiments_query_is_validated(client: TestClient, params: dict[str, str]) -> None:
    response = client.get("/api/v1/experiments", params=params)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


@pytest.mark.parametrize(
    "body",
    [{}, {"starred": "maybe"}, {"starred": True, "number": 3}, {"public": "yes please"}],
    ids=["empty", "not-bool", "extra", "public-not-bool"],
)
@pytest.mark.usefixtures("signed_in")
def test_a_star_is_validated(client: TestClient, body: dict[str, object]) -> None:
    response = client.patch("/api/v1/backtests/bt_7f3k2m9qx4", json=body, headers=ORIGIN)

    assert response.status_code == 422
    assert response.json()["type"] == PROBLEMS + "validation"


def test_the_app_counts_the_models_backtests_through_experiments(app: FastAPI) -> None:
    assert app.state.model_usage is model_usage


@pytest.mark.parametrize(
    ("path", "query", "cost"),
    [
        ("/api/v1/backtests/{backtest_id}", b"view=summary", 1),
        ("/api/v1/backtests/{backtest_id}", b"", HEAVY),
        ("/api/v1/backtests/{backtest_id}", b"view=light", HEAVY),
        ("/api/v1/backtests/{backtest_id}/download", b"", HEAVY),
        ("/api/v1/compare", b"ids=bt_7f3k2m9qx4", HEAVY),
    ],
)
def test_what_a_key_pays_for_a_result(path: str, query: bytes, cost: int) -> None:
    route = next(
        route
        for route in backtests_router.routes
        if isinstance(route, APIRoute) and route.path == path and "GET" in (route.methods or ())
    )
    request = Request({"type": "http", "route": route, "query_string": query, "headers": []})

    assert request_cost(request) == cost
