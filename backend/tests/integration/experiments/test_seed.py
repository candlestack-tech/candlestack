"""The seed command against a real Postgres: what it adds, that a second run replaces only
that, and that it refuses an unknown email."""

import asyncio
from datetime import UTC, datetime

import pytest

from candlestack.core import Database, Settings
from candlestack.experiments import seed as seed_module
from candlestack.experiments.errors import UnknownUser
from candlestack.experiments.seed import main, seed

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 17, 18, 30, tzinfo=UTC)


@pytest.fixture
def run_seed(database_url):
    async def run(email: str) -> list[int]:
        db = Database(database_url)
        try:
            async with db.sessions() as session:
                return [backtest.number for backtest in await seed(session, email, NOW)]
        finally:
            await db.dispose()

    return lambda email: asyncio.run(run(email))


def test_the_seed_adds_the_mockups_backtests(ada, bob, run_seed) -> None:
    model_id = ada.model("Blesk-Novol-8.keras")

    assert run_seed(ada.email.upper()) == list(range(1, 16))

    page = ada.backtests(sort="number", order="asc").json()
    assert page["total"] == 15
    assert page["counts"] == {"all": 15, "starred": 5, "failed": 2, "public": 0}
    assert page["models"] == [
        {"name": "Blesk-Novol-8.keras", "framework": "keras", "count": 10},
        {"name": "blesk-novol-9.keras", "framework": "keras", "count": 2},
        {"name": "my-tsmixer.keras", "framework": "keras", "count": 2},
        {"name": "stockmixer.onnx", "framework": "onnx", "count": 1},
    ]
    first = page["backtests"][0]
    assert first["model"] == {"id": model_id, "name": "Blesk-Novol-8.keras", "framework": "keras"}
    assert first["created_at"] == "2026-10-10T20:40:00Z"
    assert first["total_return"] == 0.384
    newest = ada.backtests().json()["backtests"][0]
    assert newest["number"] == 15
    assert newest["model"] == {"id": None, "name": "my-tsmixer.keras", "framework": "keras"}
    assert len(newest["instruments"]) == 8
    equity = newest["equity"]
    assert [len(equity["strategy"]), len(equity["benchmark"])] == [60, 60]
    assert (equity["strategy"][-1], equity["benchmark"][-1]) == (0.246, 0.682)
    failed = ada.backtests(show="failed").json()["backtests"]
    assert [backtest["equity"] for backtest in failed] == [None, None]
    assert bob.backtests().json()["total"] == 0


def test_a_second_run_replaces_only_what_the_seed_added(ada, sql, run_seed) -> None:
    ada.backtest(1)
    run_seed(ada.email)
    ada.backtest(17)
    first_ids = {row[0] for row in sql("SELECT id FROM backtests WHERE seeded")}

    first_equity = sql("SELECT equity FROM backtests WHERE seeded ORDER BY number")

    numbers = run_seed(ada.email)

    assert numbers == list(range(18, 33))
    # Drawn from the mockup's numbers, the lines stay the same.
    assert sql("SELECT equity FROM backtests WHERE seeded ORDER BY number") == first_equity
    rows = sql("SELECT id, number, seeded FROM backtests ORDER BY number")
    assert len(rows) == 17
    assert [(number, seeded) for _, number, seeded in rows[:1] + rows[-1:]] == [
        (1, False),
        (32, True),
    ]
    assert (17, False) in [(number, seeded) for _, number, seeded in rows]
    assert first_ids.isdisjoint(row[0] for row in rows)


def test_an_unknown_email_is_refused(ada, sql, run_seed) -> None:
    with pytest.raises(UnknownUser):
        run_seed("nobody@example.com")

    assert sql("SELECT id FROM backtests") == []


def test_the_command(ada, database_url, monkeypatch, capsys) -> None:
    settings = Settings(app_env="stage", database_url=database_url)
    monkeypatch.setattr(seed_module, "get_settings", lambda: settings)

    assert main(["--email", ada.email]) == 0
    assert capsys.readouterr().out == f"Added 15 test backtests (#1 to #15) to user {ada.id}\n"

    assert main(["--email", "nobody@example.com"]) == 1
    assert capsys.readouterr().err == (
        "python -m candlestack.experiments.seed: error: no user has the email nobody@example.com\n"
    )
