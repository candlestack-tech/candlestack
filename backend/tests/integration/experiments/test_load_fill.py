"""The fill script of the load test (tests/load/fill.py) against a real Postgres: what the pages
under load then read, and that a second run replaces only its own rows."""

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from candlestack.core import Database

pytestmark = pytest.mark.integration

# fill.py is a script, not a package module: import it by path.
_spec = importlib.util.spec_from_file_location(
    "fill", Path(__file__).parents[2] / "load" / "fill.py"
)
assert _spec is not None
assert _spec.loader is not None
fill_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fill_module)

NOW = datetime(2026, 11, 2, 20, 0, tzinfo=UTC)


@pytest.fixture
def run_fill(database_url):
    async def run(email: str, backtests: int, models: int) -> tuple[str, int, int]:
        db = Database(database_url)
        try:
            async with db.sessions() as session:
                return await fill_module.fill(session, email, backtests, models, NOW)
        finally:
            await db.dispose()

    return lambda email, backtests, models: asyncio.run(run(email, backtests, models))


def test_fill_gives_the_pages_many_backtests_and_models(ada, bob, run_fill) -> None:
    own = ada.model("my-own.keras")

    assert run_fill(ada.email, 30, 4) == (ada.id, 30, 4)

    page = ada.backtests(sort="number", order="asc").json()
    assert page["total"] == 30
    assert page["counts"]["all"] == 30
    names = [
        item["generated_name"]
        for number in (1, 2, 3)
        for item in ada.backtests(sort="number", order="asc", page=number).json()["backtests"]
    ]
    assert len({name.lower() for name in names}) == 30
    assert {model["name"] for model in page["models"]} <= {
        "load-0.keras",
        "load-1.onnx",
        "load-2.keras",
        "load-3.onnx",
    }
    assert ada.dashboard().status_code == 200
    models = ada.request("GET", "/api/v1/models").json()["models"]
    assert {model["name"] for model in models} == {
        "my-own.keras",
        "load-0.keras",
        "load-1.onnx",
        "load-2.keras",
        "load-3.onnx",
    }
    assert own in {model["id"] for model in models}
    assert bob.backtests().json()["total"] == 0


def test_a_second_fill_replaces_the_first(ada, run_fill) -> None:
    ada.backtest(1)
    run_fill(ada.email, 30, 4)

    run_fill(ada.email, 20, 2)

    page = ada.backtests().json()
    assert page["total"] == 21
    models = ada.request("GET", "/api/v1/models").json()["models"]
    assert sorted(model["name"] for model in models) == ["load-0.keras", "load-1.onnx"]


def test_fill_refuses_an_unknown_email(run_fill) -> None:
    with pytest.raises(SystemExit, match=r"no user has the email nobody@example\.com"):
        run_fill("nobody@example.com", 1, 1)
