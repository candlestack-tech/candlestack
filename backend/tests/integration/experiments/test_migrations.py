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
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from candlestack.experiments.names import ADJECTIVES, NOUNS

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).parents[3]
# The revision before the publication of a backtest.
BEFORE_PUBLICATION = "9c2e4f7a1b36"
# The revision before the generated names.
BEFORE_NAMES = "e4b7a2c9d013"
# The revision before the queue of backtests.
BEFORE_QUEUE = "d990b92fe1cd"


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


# --- The generated names ------------------------------------------------------------------------


@pytest.fixture
def before_names(database_url: str) -> Iterator[None]:
    """The database at the revision before the generated names; at the head again after."""
    _migrate(database_url, BEFORE_NAMES, down=True)
    try:
        yield
    finally:
        _migrate(database_url, "head")


def _add_backtests(sql, user_id: str, names: dict[int, str | None]) -> None:
    """Backtests of the user by number, each with its custom name or none, stored before the
    generated names (the newest first, so only the migration's order puts them oldest first)."""
    for number, name in sorted(names.items(), reverse=True):
        sql(
            "INSERT INTO backtests (id, user_id, number, status, instruments, representation, "
            "model_name, model_framework, setup, name, created_at) VALUES (:id, :user_id, "
            ":number, 'finished', '[]', 'renko', 'm.keras', 'keras', '{}', :name, now())",
            id=f"bt_{user_id[4:8]}{number:06d}",
            user_id=user_id,
            number=number,
            name=name,
        )


def _generated(sql, user_id: str) -> list[str]:
    rows = sql(
        "SELECT generated_name FROM backtests WHERE user_id = :user_id ORDER BY number",
        user_id=user_id,
    )
    return [row[0] for row in rows]


@pytest.mark.usefixtures("before_names")
def test_every_backtest_gets_a_name_of_its_own_in_its_account(
    ada, bob, sql, database_url: str
) -> None:
    _add_backtests(sql, ada.id, {1: None, 2: "my run", 3: None, 4: None})
    _add_backtests(sql, bob.id, {1: None, 2: None})

    _migrate(database_url, "head")

    for user in (ada, bob):
        names = _generated(sql, user.id)
        assert len({name.lower() for name in names}) == len(names)
        for name in names:
            adjective, noun = name.split(" ")
            assert (adjective in ADJECTIVES, noun in NOUNS) == (True, True)
    # The custom names stay.
    assert sql("SELECT number, name FROM backtests WHERE name IS NOT NULL") == [(2, "my run")]


@pytest.mark.usefixtures("before_names")
def test_the_names_go_oldest_first_and_skip_the_custom_names(ada, sql, database_url: str) -> None:
    _add_backtests(sql, ada.id, {1: None, 2: None, 3: None, 4: None})
    _migrate(database_url, "head")
    first = _generated(sql, ada.id)
    _migrate(database_url, BEFORE_NAMES, down=True)
    # The name the oldest got, as another backtest's own name in another case.
    sql("UPDATE backtests SET name = :name WHERE number = 4", name=first[0].upper())

    _migrate(database_url, "head")

    again = _generated(sql, ada.id)
    # The same order every run: each takes the next free name.
    assert again[:3] == first[1:]
    assert again[3].lower() not in {name.lower() for name in first}


@pytest.mark.usefixtures("before_names")
def test_the_names_are_the_same_every_run(ada, bob, sql, database_url: str) -> None:
    _add_backtests(sql, ada.id, {1: None, 2: "my run"})
    _add_backtests(sql, bob.id, {1: None})
    _migrate(database_url, "head")
    first = sql("SELECT id, generated_name FROM backtests ORDER BY id")
    _migrate(database_url, BEFORE_NAMES, down=True)

    _migrate(database_url, "head")

    assert sql("SELECT id, generated_name FROM backtests ORDER BY id") == first


@pytest.mark.usefixtures("before_names")
def test_the_index_refuses_a_generated_name_twice_in_an_account(
    ada, bob, sql, database_url: str
) -> None:
    _add_backtests(sql, ada.id, {1: None})
    _migrate(database_url, "head")
    taken = _generated(sql, ada.id)[0]
    insert = (
        "INSERT INTO backtests (id, user_id, number, status, instruments, representation, "
        "model_name, model_framework, setup, generated_name, created_at) VALUES (:id, :user_id, "
        "2, 'finished', '[]', 'renko', 'm.keras', 'keras', '{}', :name, now())"
    )

    sql(insert, id="bt_bob0000002", user_id=bob.id, name=taken)
    with pytest.raises(IntegrityError, match="uq_backtests_user_id_generated_name"):
        sql(insert, id="bt_ada0000002", user_id=ada.id, name=taken.lower())


@pytest.mark.usefixtures("before_names")
def test_the_generated_names_go_back(ada, sql, database_url: str) -> None:
    _add_backtests(sql, ada.id, {1: "my run"})
    _migrate(database_url, "head")

    _migrate(database_url, BEFORE_NAMES, down=True)

    columns = sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'backtests'"
    )
    indexes = sql("SELECT indexname FROM pg_indexes WHERE tablename = 'backtests'")
    assert "generated_name" not in {row[0] for row in columns}
    assert "uq_backtests_user_id_generated_name" not in {row[0] for row in indexes}
    assert sql("SELECT name FROM backtests") == [("my run",)]


# --- The queue ----------------------------------------------------------------------------------


@pytest.fixture
def before_queue(database_url: str) -> Iterator[None]:
    """The database at the revision before the queue; at the head again after the test."""
    _migrate(database_url, BEFORE_QUEUE, down=True)
    try:
        yield
    finally:
        _migrate(database_url, "head")


def _add_backtest(sql, user_id: str, number: int, status: str) -> None:
    sql(
        "INSERT INTO backtests (id, user_id, number, status, instruments, representation, "
        "model_name, model_framework, setup, generated_name, created_at) VALUES (:id, :user_id, "
        ":number, :status, '[]', 'renko', 'm.keras', 'keras', '{}', :name, "
        "'2026-10-01T12:00:00Z')",
        id=f"bt_{user_id[4:8]}{number:06d}",
        user_id=user_id,
        number=number,
        status=status,
        name=f"Backtest {number}",
    )


@pytest.mark.usefixtures("before_queue")
def test_the_backtests_stored_before_the_queue_began_to_run_when_they_were_made(
    ada, sql, database_url: str
) -> None:
    _add_backtest(sql, ada.id, 1, "finished")

    _migrate(database_url, "head")

    assert sql("SELECT started_at = created_at FROM backtests") == [(True,)]


@pytest.mark.usefixtures("before_queue")
def test_the_queue_goes_back_failing_the_queued_backtests(ada, sql, database_url: str) -> None:
    _migrate(database_url, "head")
    _add_backtest(sql, ada.id, 1, "queued")

    _migrate(database_url, BEFORE_QUEUE, down=True)

    columns = sql(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'backtests'"
    )
    assert "started_at" not in {row[0] for row in columns}
    assert sql("SELECT status, error IS NOT NULL FROM backtests") == [("failed", True)]
    with pytest.raises(IntegrityError, match="ck_backtests_status"):
        _add_backtest(sql, ada.id, 2, "queued")
