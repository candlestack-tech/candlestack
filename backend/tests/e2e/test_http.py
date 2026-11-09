import httpx
import pytest

pytestmark = pytest.mark.e2e


def test_health(http: httpx.Client) -> None:
    response = http.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-request-id"]
    # The substrings the deploy checks look for.
    assert b'"status":"ok"' in response.content
    assert b'"version":"e2e"' in response.content
    assert response.json() == {
        "status": "ok",
        "env": "test",
        "version": "e2e",
        "commit": response.json()["commit"],
        "redis": "ok",
        "postgres": "ok",
        "runner": "ok",
    }
    assert response.json()["commit"] not in ("", "unknown")


def test_docs(http: httpx.Client) -> None:
    response = http.get("/api/v1/docs")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "CandleStack API" in response.text


def test_openapi(http: httpx.Client) -> None:
    response = http.get("/api/v1/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["title"] == "CandleStack API"
    assert schema["info"]["version"] == "e2e"
    assert "/api/health" in schema["paths"]


def test_errors_are_problem_details(http: httpx.Client) -> None:
    response = http.get("/api/nothing-here")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "https://candlestack.tech/problems/not-found"
