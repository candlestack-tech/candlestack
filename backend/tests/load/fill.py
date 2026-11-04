"""Fills one local account with many backtests and models for the account scenario of load.py.

The models are rows only, without files: the pages under load read the rows, never the files.
The backtests are made like the seed command's (``candlestack.experiments.seed``), finished or
failed, over the last year, on the models added here. A second run replaces what the first one
added: the seeded backtests and the models named ``load-*``. Like the seed command, it refuses to
run with ``APP_ENV`` ``prod``; ``DATABASE_URL`` names the database.

    uv run python tests/load/fill.py --email dev@candlestack.tech --backtests 1000 --models 100
"""

import argparse
import asyncio
import hashlib
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, time, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.core import Database, get_settings
from candlestack.experiments.names import generated_name
from candlestack.experiments.schemas import Representation, SetupDirection
from candlestack.experiments.seed import REFUSED_ENV, SeedRun, seed_backtest
from candlestack.experiments.store import BacktestStore, UserModel
from candlestack.ml.models import Model, new_model_id

if TYPE_CHECKING:
    from candlestack.experiments.models import Backtest

PROG = "tests/load/fill.py"
PREFIX = "load-"
SYMBOLS = ("AAPL", "MSFT", "NVDA", "SPY", "TSLA", "META", "GOOGL", "AMZN", "AMD", "NFLX")
_FAILED = "The model expects 30 candles per sample. The windows give 25."


def load_models(user_id: str, count: int, now: datetime) -> list[Model]:
    """`count` models named ``load-0.keras`` and on, half Keras and half ONNX, a minute apart."""
    models = []
    for n in range(count):
        framework = "keras" if n % 2 == 0 else "onnx"
        name = f"{PREFIX}{n}.{framework}"
        models.append(
            Model(
                id=new_model_id(),
                user_id=user_id,
                name=name,
                framework=framework,
                size_bytes=200_000 + n * 1000,
                sha256=hashlib.sha256(name.encode()).digest(),
                parameters=10_000 + n,
                input_shape=[30, 5],
                output_shape=[1],
                keras_version="3.10.0" if framework == "keras" else None,
                starred=n % 10 == 0,
                created_at=now - timedelta(minutes=count - n),
            )
        )
    return models


def random_run(draw: random.Random, number: int, model: UserModel) -> SeedRun:
    """A backtest of the seed command's kind: one in ten failed, one in ten starred."""
    failed = draw.random() < 0.1
    results = None
    if not failed:
        total = draw.uniform(-30, 90)
        results = (
            round(total, 1),
            round(draw.uniform(-10, 140), 1),
            round(draw.uniform(-0.5, 1.6), 2),
            round(draw.uniform(-40, -3), 1),
            draw.randint(20, 1500),
            round(draw.uniform(40, 60), 1),
        )
    return SeedRun(
        number=number,
        symbols=tuple(draw.sample(SYMBOLS, draw.choice((1, 1, 1, 2, 5)))),
        representation=draw.choice(tuple(Representation)),
        model=model.name,
        days_ago=draw.randint(1, 365),
        at=time(draw.randint(0, 23), draw.randint(0, 59)),
        duration_s=round(draw.uniform(2, 160), 1),
        results=results,
        starred=draw.random() < 0.1,
        direction=draw.choice(tuple(SetupDirection)),
        threshold=draw.choice((0.05, 0.1)),
        error=_FAILED if failed else None,
    )


async def fill(
    db: AsyncSession, email: str, backtests: int, models: int, now: datetime
) -> tuple[str, int, int]:
    """Replaces what an earlier run added to the account with `backtests` backtests on
    `models` new models; the user's ID and how many of each it added.

    Raises:
        SystemExit: No user has the email.
    """
    store = BacktestStore(db)
    user_id = await store.user_id(email)
    if user_id is None:
        raise SystemExit(f"{PROG}: error: no user has the email {email}")
    await store.lock_user(user_id)
    await store.delete_seeded(user_id)
    await db.execute(delete(Model).where(Model.user_id == user_id, Model.name.startswith(PREFIX)))
    added = load_models(user_id, models, now)
    db.add_all(added)
    await db.flush()
    owned = [UserModel(model.id, model.name, model.framework) for model in added]
    first = await store.last_number(user_id) + 1
    draw = random.Random(251)
    rows: list[Backtest] = []
    for n in range(backtests):
        model = draw.choice(owned)
        run = random_run(draw, n + 1, model)
        rows.append(seed_backtest(run, user_id, number=first + n, model=model, now=now))
    # Under the lock and after the delete, like the seed command: the names left are all taken.
    taken = await store.names(user_id)
    for row in rows:
        row.generated_name = generated_name(taken)
        taken.add(row.generated_name.lower())
    db.add_all(rows)
    await store.commit()
    return user_id, len(rows), len(added)


async def _fill_database(url: str, args: argparse.Namespace) -> tuple[str, int, int]:
    db = Database(url)
    try:
        async with db.sessions() as session:
            return await fill(session, args.email, args.backtests, args.models, datetime.now(UTC))
    finally:
        await db.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG, description="Fill a local account for the load test of its pages."
    )
    parser.add_argument("--email", default="dev@candlestack.tech", help="the account's email")
    parser.add_argument("--backtests", type=int, default=1000)
    parser.add_argument("--models", type=int, default=100, help="at most 100, the app's limit")
    args = parser.parse_args(argv)
    if not 1 <= args.models <= 100:
        parser.error("--models must be from 1 to 100")
    settings = get_settings()
    if settings.app_env == REFUSED_ENV:
        sys.stderr.write(f"{PROG}: error: APP_ENV is {REFUSED_ENV}: local accounts only\n")
        return 1
    user_id, backtests, models = asyncio.run(
        _fill_database(settings.database_url.get_secret_value(), args)
    )
    sys.stdout.write(f"Added {backtests} backtests and {models} models to user {user_id}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
