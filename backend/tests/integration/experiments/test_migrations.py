"""The migrations of the backtests table that move data, run on rows stored before them against
a real Postgres: the database is migrated back to the revision before, filled, and up again."""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).parents[3]
# The revision before the publication of a backtest.
BEFORE_PUBLICATION = "9c2e4f7a1b36"


def _migrate(database_url: str, revision: str, *, down: bool = False) -> None:
    def run(connection: Connection) -> None:
        config = Config(toml_file=BACKEND / "pyproject.toml")
        config.attributes["connection"] = connection
        (command.downgrade if down else command.upgrade)(config, revision)

    async def on_database() -> None:
        engine = create_async_engine(database_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(run)
        finally:
            await engine.dispose()

    asyncio.run(on_database())


@pytest.fixture
def before_publication(database_url: str) -> Iterator[None]:
    """The database at the revision before the publication; at the head again after the test."""
    _migrate(database_url, BEFORE_PUBLICATION, down=True)
    try:
        yield
    finally:
        _migrate(database_url, "head")


def _spec(shape: object) -> str:
    return json.dumps({"model": {"manifest": {"signature": {"input": {"shape": shape}}}}})


@pytest.mark.usefixtures("before_publication")
def test_the_publication_fills_the_benchmarks_return_and_the_models_input(
    ada, sql, database_url: str
) -> None:
    for number, benchmark, spec in (
        (1, "bh", _spec([25, 5])),
        (2, "SPY", _spec([30, 23])),
        (3, "bh", None),  # test data of the seed command
        (4, "bh", _spec(None)),
    ):
        sql(
            "INSERT INTO backtests (id, user_id, number, status, instruments, representation, "
            "model_name, model_framework, setup, buy_and_hold_return, spec, created_at) VALUES "
            "(:id, :user_id, :number, 'finished', '[]', 'renko', 'm.keras', 'keras', "
            "CAST(:setup AS jsonb), 0.25, CAST(:spec AS jsonb), now())",
            id=f"bt_{number:010d}",
            user_id=ada.id,
            number=number,
            setup=json.dumps({"benchmark": benchmark}),
            spec=spec,
        )

    _migrate(database_url, "head")

    assert sql(
        "SELECT number, benchmark_return, input_shape, published_at FROM backtests ORDER BY number"
    ) == [
        (1, 0.25, [25, 5], None),
        # The fund's return was never stored.
        (2, None, [30, 23], None),
        (3, 0.25, None, None),
        (4, 0.25, None, None),
    ]


@pytest.mark.usefixtures("before_publication")
def test_the_publication_goes_back(sql, database_url: str) -> None:
    _migrate(database_url, "head")
    _migrate(database_url, BEFORE_PUBLICATION, down=True)

    columns = sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'backtests'"
    )
    assert not {"published_at", "benchmark_return", "input_shape"} & {row[0] for row in columns}
