"""The Backtests page, the Dashboard and starting a backtest over HTTP with the built image and
its migrated table; the dev user has no backtests."""

from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e

ORIGIN = {"Origin": "http://testserver"}
NO_BACKTESTS = {"all": 0, "starred": 0, "failed": 0}
FIXTURES = Path(__file__).parents[1] / "fixtures" / "keras"


def test_the_backtests_list(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)

    response = http.get("/api/v1/backtests", params={"q": "aapl", "sort": "sharpe", "page": 2})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "backtests": [],
        "total": 0,
        "page": 2,
        "pages": 1,
        "counts": NO_BACKTESTS,
        "models": [],
    }


def test_the_dashboard(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)

    response = http.get("/api/v1/dashboard")

    assert response.status_code == 200
    assert response.json()["counts"] == NO_BACKTESTS
    assert response.json()["last"] == []


def test_starring_an_unknown_backtest(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)

    response = http.patch("/api/v1/backtests/bt_zzzzzzzzzz", json={"starred": True}, headers=ORIGIN)

    assert response.status_code == 404
    assert response.json()["type"] == "https://candlestack.tech/problems/not-found"


def test_a_backtest_needs_the_worker(http: httpx.Client) -> None:
    """The stack has no worker or job queue, as prod has none yet: a backtest is refused at
    once, never left waiting."""
    http.post("/api/v1/auth/dev", headers=ORIGIN)
    uploaded = http.post(
        "/api/v1/models",
        params={"name": "raw_candles.keras"},
        content=(FIXTURES / "raw_candles.keras").read_bytes(),
        headers={**ORIGIN, "Content-Type": "application/octet-stream"},
    )
    model_id = uploaded.json()["id"]
    http.put(
        f"/api/v1/models/{model_id}/manifest",
        content=(FIXTURES / "raw_candles.manifest.json").read_bytes(),
        headers={**ORIGIN, "Content-Type": "application/json"},
    )
    body = {
        "model": {"id": model_id},
        "data": {
            "instrument": {"id": "stock:AAPL"},
            "period": {"start": 1717372800, "end": 1717459200},
        },
    }

    try:
        response = http.post("/api/v1/backtests", json=body, headers=ORIGIN)
    finally:
        http.delete(f"/api/v1/models/{model_id}", headers=ORIGIN)

    assert response.status_code == 503
    assert response.json()["type"] == "https://candlestack.tech/problems/backtests-unavailable"
    assert http.get("/api/v1/backtests").json()["total"] == 0


def test_the_document_of_an_unknown_backtest(http: httpx.Client) -> None:
    http.post("/api/v1/auth/dev", headers=ORIGIN)

    document = http.get("/api/v1/backtests/bt_zzzzzzzzzz")
    download = http.get("/api/v1/backtests/bt_zzzzzzzzzz/download")

    assert (document.status_code, download.status_code) == (404, 404)
    assert document.json()["type"] == "https://candlestack.tech/problems/not-found"
