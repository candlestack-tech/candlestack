import json
import logging
import sys

import pytest
from fastapi.testclient import TestClient

from candlestack.core import setup_logging
from candlestack.core.logging import JsonFormatter, request_id_var


def _record(msg: str = "hello %s", *args: object, **extra: object) -> logging.LogRecord:
    record = logging.makeLogRecord(
        {"name": "candlestack.test", "levelno": logging.INFO, "levelname": "INFO"}
    )
    record.msg, record.args = msg, args or ("world",)
    record.__dict__.update(extra)
    return record


def test_formats_one_json_object() -> None:
    line = JsonFormatter().format(_record(status=200))

    assert "\n" not in line
    entry = json.loads(line)
    assert entry.keys() == {"ts", "level", "logger", "msg", "status"}
    assert entry["level"] == "INFO"
    assert entry["logger"] == "candlestack.test"
    assert entry["msg"] == "hello world"
    assert entry["status"] == 200
    assert entry["ts"].endswith("+00:00")


def test_adds_the_current_request_id() -> None:
    token = request_id_var.set("8c1f0e2d")
    try:
        entry = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)

    assert entry["request_id"] == "8c1f0e2d"


def test_includes_the_traceback() -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        record = _record()
        record.exc_info = sys.exc_info()

    entry = json.loads(JsonFormatter().format(record))

    assert "ValueError: boom" in entry["exc"]


def test_setup_logging_replaces_its_handler() -> None:
    root = logging.getLogger()
    setup_logging("INFO")
    setup_logging("DEBUG")

    ours = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
    assert len(ours) == 1
    assert root.level == logging.DEBUG
    setup_logging("INFO")


def test_access_log_line(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="candlestack.access"):
        response = client.get("/api/health", headers={"CF-Ray": "8c1f0e2d3b4a5968-VIE"})

    assert response.headers["x-request-id"] == "8c1f0e2d3b4a5968-VIE"
    [record] = [r for r in caplog.records if r.name == "candlestack.access"]
    entry = json.loads(JsonFormatter().format(record))
    assert entry["msg"] == "GET /api/health 200"
    assert entry["method"] == "GET"
    assert entry["path"] == "/api/health"
    assert entry["status"] == 200
    assert entry["request_id"] == "8c1f0e2d3b4a5968-VIE"
    assert isinstance(entry["duration_ms"], float)


def test_the_access_log_leaves_out_an_upload_links_token(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # Not of a token's form, so the route answers without Postgres.
    token = "Secret-Token"

    with caplog.at_level(logging.INFO, logger="candlestack.access"):
        client.put(f"/api/v1/uploads/{token}", content=b"x")

    [record] = [r for r in caplog.records if r.name == "candlestack.access"]
    line = JsonFormatter().format(record)
    entry = json.loads(line)
    assert entry["msg"] == "PUT /api/v1/uploads/[Filtered] 404"
    assert entry["path"] == "/api/v1/uploads/[Filtered]"
    assert token not in line


def test_request_id_is_random_without_cf_ray(client: TestClient) -> None:
    first = client.get("/api/health").headers["x-request-id"]
    second = client.get("/api/health").headers["x-request-id"]

    assert len(first) == 16
    assert first != second


@pytest.mark.parametrize("path", ["/api/health", "/api/nothing-here"])
def test_server_timing(client: TestClient, path: str) -> None:
    timing = client.get(path).headers["server-timing"]

    name, duration = timing.split(";dur=")
    assert name == "app"
    assert float(duration) >= 0
