"""Fills one account with the 15 backtests of the app's mockup, so stage and local have data to
look at before backtests can be made: ``python -m candlestack.experiments.seed --email <email>``
(the image has no console scripts; ``DATABASE_URL`` names the database).

New accounts start empty: only the tech lead's account gets this test data, on stage and
local. The command refuses to run with ``APP_ENV`` ``prod``. It marks what it adds
(``backtests.seeded``) and a second run replaces only that, never the user's other
backtests, whose numbers it continues after. A backtest uses the user's model of its model's
name if there is one, else it keeps only the name, as a backtest of a deleted model does.
"""

import argparse
import asyncio
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from candlestack.core import Database, get_settings
from candlestack.experiments.errors import UnknownUser
from candlestack.experiments.models import Backtest, new_backtest_id
from candlestack.experiments.schemas import (
    EQUITY_POINTS,
    BacktestSetupIn,
    BacktestStatus,
    InstrumentMarket,
    Representation,
    SetupBenchmark,
    SetupDirection,
    SetupTimeframe,
)
from candlestack.experiments.store import BacktestStore, UserModel

PROG = "python -m candlestack.experiments.seed"
# Where test data must never go.
REFUSED_ENV = "prod"

_FIVE = ("AAPL", "MSFT", "NVDA", "SPY", "TSLA")
_KAGI_ERROR = (
    "blesk-novol-8.keras reads Renko bricks, not a Kagi line. Pick a model that reads Kagi or "
    "switch back to Renko."
)
_WINDOW_ERROR = (
    "stockmixer.onnx expects 30 candles per sample. The windows give 25. Set the window to 30 or "
    "pick another model."
)


@dataclass(frozen=True)
class SeedRun:
    """A backtest of the mockup, its results in percent as the mockup shows them."""

    number: int
    symbols: tuple[str, ...]
    representation: Representation
    model: str
    days_ago: int
    at: time
    duration_s: float
    # Total return, buy and hold, Sharpe, max drawdown, trades, win rate; None: it failed.
    results: tuple[float, float, float, float, int, float] | None
    starred: bool = False
    direction: SetupDirection = SetupDirection.long
    threshold: float = 0.05
    error: str | None = None


B8, B9, TSMIXER = "blesk-novol-8.keras", "blesk-novol-9.keras", "my-tsmixer.keras"
RENKO, KAGI, WINDOWS = Representation.renko, Representation.kagi, Representation.windows

RUNS: tuple[SeedRun, ...] = (
    SeedRun(1, ("AAPL",), RENKO, B8, 7, time(20, 40), 21, (38.4, 61.2, 1.12, -14.8, 214, 54.2)),
    SeedRun(2, ("AAPL",), RENKO, B9, 7, time(21, 2), 20, (29.6, 61.2, 0.93, -17.0, 176, 52.3)),
    SeedRun(
        3,
        ("META",),
        RENKO,
        B8,
        6,
        time(19, 30),
        21,
        (64.2, 127.8, 1.08, -28.7, 219, 53.4),
        starred=True,
    ),
    SeedRun(
        4,
        ("AAPL",),
        RENKO,
        B8,
        6,
        time(20, 12),
        20,
        (18.3, 61.2, 0.88, -9.7, 72, 58.3),
        threshold=0.10,
    ),
    SeedRun(5, ("TSLA",), RENKO, B8, 5, time(17, 5), 22, (21.4, 15.7, 0.64, -31.5, 227, 48.9)),
    SeedRun(
        6, ("AAPL",), WINDOWS, "stockmixer.onnx", 5, time(18, 20), 2, None, error=_WINDOW_ERROR
    ),
    SeedRun(
        7,
        ("MSFT", "GOOGL"),
        RENKO,
        B8,
        5,
        time(19, 44),
        41,
        (25.0, 71.0, 0.84, -18.0, 399, 51.6),
        starred=True,
    ),
    SeedRun(
        8,
        ("SPY",),
        RENKO,
        B8,
        1,
        time(16, 2),
        19,
        (-4.2, 38.0, -0.18, -22.4, 341, 47.8),
        direction=SetupDirection.both,
    ),
    SeedRun(
        9, ("AAPL",), WINDOWS, TSMIXER, 1, time(17, 31), 34, (12.6, 61.2, 0.48, -19.9, 302, 50.3)
    ),
    SeedRun(10, ("AAPL",), RENKO, B9, 1, time(18, 10), 20, (31.0, 61.2, 0.97, -16.2, 188, 52.7)),
    SeedRun(
        11,
        ("AAPL",),
        RENKO,
        B8,
        0,
        time(13, 58),
        21,
        (38.4, 61.2, 1.12, -14.8, 214, 54.2),
        starred=True,
    ),
    SeedRun(
        12,
        _FIVE,
        RENKO,
        B8,
        0,
        time(14, 22),
        90,
        (37.8, 61.9, 0.99, -15.8, 1056, 52.4),
        starred=True,
    ),
    SeedRun(13, ("TSLA",), KAGI, B8, 0, time(15, 40), 3, None, error=_KAGI_ERROR),
    SeedRun(
        14,
        ("NVDA",),
        RENKO,
        B8,
        0,
        time(15, 51),
        22,
        (96.5, 142.3, 1.34, -24.1, 241, 55.6),
        starred=True,
    ),
    SeedRun(
        15,
        (*_FIVE, "AMZN", "META", "GOOGL"),
        WINDOWS,
        TSMIXER,
        0,
        time(16, 5),
        162,
        (24.6, 68.2, 0.79, -19.7, 1712, 51.2),
    ),
)


def _since_midnight(at: time) -> timedelta:
    return timedelta(hours=at.hour, minutes=at.minute, seconds=at.second)


# When the last of today's runs of the mockup ends, after midnight (UTC); before then, today's
# runs move back.
_TODAY_LAST_END = max(
    _since_midnight(run.at) + timedelta(seconds=run.duration_s) for run in RUNS if run.days_ago == 0
)


def started_at(run: SeedRun, now: datetime) -> datetime:
    """When `run` started: its day before `now` at its time (UTC). Today's runs must have
    finished by `now`: earlier in the day they all move back by as much, keeping their gaps
    (very early in the day that reaches into yesterday)."""
    day = now.date() - timedelta(days=run.days_ago)
    start = datetime.combine(day, run.at, tzinfo=UTC)
    if run.days_ago == 0:
        last_end = datetime.combine(day, time(), tzinfo=UTC) + _TODAY_LAST_END
        start -= max(timedelta(0), last_end - now)
    return start


# The equity of a mockup run: a random walk of this many steps over the period, drawn as the
# mockup draws it (eqRun), then sampled down to what a backtest keeps.
_WALK_STEPS = 120
_U32 = 0xFFFFFFFF


def mulberry32(seed: int) -> Callable[[], float]:
    """The mockup's random numbers (mulberry32): the same seed draws the same numbers in
    [0, 1) as the mockup's JavaScript."""
    state = seed & _U32

    def draw() -> float:
        nonlocal state
        state = (state + 0x6D2B79F5) & _U32
        t = (state ^ (state >> 15)) * (1 | state) & _U32
        t = ((t + ((t ^ (t >> 7)) * (61 | t) & _U32)) & _U32) ^ t
        return (t ^ (t >> 14)) / 2**32

    return draw


def gauss(draw: Callable[[], float]) -> float:
    """A standard normal number from `draw` (Box-Muller), as the mockup makes it."""
    u = v = 0.0
    while not u:
        u = draw()
    while not v:
        v = draw()
    return math.sqrt(-2 * math.log(u)) * math.cos(2 * math.pi * v)


def equity_walk(draw: Callable[[], float], target: float, volatility: float) -> list[float]:
    """A cumulative return from 0 to `target` over ``_WALK_STEPS`` steps: a random walk with
    steps of `volatility`, bent so that it ends at `target`."""
    walk = [0.0]
    for _ in range(_WALK_STEPS):
        walk.append(walk[-1] + gauss(draw) * volatility)
    end = walk[-1]
    return [
        target * i / _WALK_STEPS + walk[i] - end * i / _WALK_STEPS for i in range(_WALK_STEPS + 1)
    ]


def downsample(points: Sequence[float], count: int = EQUITY_POINTS) -> list[float]:
    """`count` of `points` at evenly spaced positions, the first and the last included."""
    last = len(points) - 1
    return [points[round(k * last / (count - 1))] for k in range(count)]


def seed_equity(run: SeedRun) -> tuple[list[float], list[float]] | None:
    """The equity series of a finished run, the strategy's and buy and hold's, as the mockup
    draws them (seeded by the run's number of the mockup, so every run of the command draws
    the same lines), ending exactly at its returns; None for a failed run."""
    if run.results is None:
        return None
    total, buy_and_hold, _, drawdown, _, _ = run.results
    draw = mulberry32(run.number * 7919 + 13)
    volatility = min(1.4, 0.6 + abs(drawdown) / 25)
    series = []
    for percent, step in ((total, 0.018), (buy_and_hold, 0.03)):
        points = [
            round(value, 6)
            for value in downsample(equity_walk(draw, percent / 100, step * volatility))
        ]
        points[0], points[-1] = 0.0, _fraction(percent)
        series.append(points)
    return series[0], series[1]


def _framework(model_name: str) -> str:
    return model_name.rsplit(".", 1)[-1]


def _setup(run: SeedRun) -> BacktestSetupIn:
    return BacktestSetupIn(
        timeframe=SetupTimeframe.m30,
        period_start=date(2021, 1, 4),
        period_end=date(2024, 12, 31),
        direction=run.direction,
        threshold=run.threshold,
        stop_loss_pct=7,
        fees_pct=0.1,
        slippage_pct=0.02,
        benchmark=SetupBenchmark.buy_and_hold,
    )


def _fraction(percent: float) -> float:
    return round(percent / 100, 6)


def seed_backtest(
    run: SeedRun, user_id: str, *, number: int, model: UserModel | None, now: datetime
) -> Backtest:
    """The backtest of `run` for the user, with the user's model of its name if there is one."""
    start = started_at(run, now)
    backtest = Backtest(
        id=new_backtest_id(),
        user_id=user_id,
        number=number,
        status=(BacktestStatus.failed if run.results is None else BacktestStatus.finished).value,
        instruments=[
            {"market": InstrumentMarket.stock.value, "symbol": symbol} for symbol in run.symbols
        ],
        representation=run.representation.value,
        model_id=model.id if model else None,
        model_name=model.name if model else run.model,
        model_framework=model.framework if model else _framework(run.model),
        setup=_setup(run).model_dump(mode="json"),
        duration_s=float(run.duration_s),
        error=run.error,
        starred=run.starred,
        seeded=True,
        created_at=start,
        finished_at=start + timedelta(seconds=run.duration_s),
    )
    if run.results is not None:
        total, buy_and_hold, sharpe, drawdown, trades, win_rate = run.results
        backtest.total_return = _fraction(total)
        backtest.buy_and_hold_return = _fraction(buy_and_hold)
        # Their benchmark is buy and hold.
        backtest.benchmark_return = backtest.buy_and_hold_return
        backtest.sharpe = sharpe
        backtest.max_drawdown = _fraction(drawdown)
        backtest.num_trades = trades
        backtest.win_rate = _fraction(win_rate)
    if (equity := seed_equity(run)) is not None:
        backtest.set_equity(*equity)
    return backtest


def seed_backtests(
    user_id: str, *, first_number: int, models: Sequence[UserModel], now: datetime
) -> list[Backtest]:
    """The mockup's backtests for the user, numbered from `first_number` in the mockup's
    order."""
    by_name = {model.name.lower(): model for model in models}
    return [
        seed_backtest(
            run,
            user_id,
            number=first_number + run.number - 1,
            model=by_name.get(run.model.lower()),
            now=now,
        )
        for run in RUNS
    ]


async def seed(db: AsyncSession, email: str, now: datetime) -> list[Backtest]:
    """Replaces the seeded backtests of the user with the email; the new ones.

    Raises:
        UnknownUser: No user has the email.
    """
    store = BacktestStore(db)
    user_id = await store.user_id(email)
    if user_id is None:
        raise UnknownUser
    await store.lock_user(user_id)
    await store.delete_seeded(user_id)
    backtests = seed_backtests(
        user_id,
        first_number=await store.last_number(user_id) + 1,
        models=await store.models(user_id),
        now=now,
    )
    store.add_all(backtests)
    await store.commit()
    return backtests


async def _seed_database(url: str, email: str) -> list[Backtest]:
    db = Database(url)
    try:
        async with db.sessions() as session:
            return await seed(session, email, datetime.now(UTC))
    finally:
        await db.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    """Runs the command with ``argv`` (``sys.argv[1:]`` when None); its exit code."""
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Replace the test backtests of one account with the mockup's 15 "
        "(stage and local only).",
    )
    parser.add_argument("--email", required=True, help="email of the account, in any case")
    args = parser.parse_args(argv)
    settings = get_settings()
    if settings.app_env == REFUSED_ENV:
        sys.stderr.write(
            f"{PROG}: error: APP_ENV is {REFUSED_ENV}: test backtests go to stage and local "
            "accounts only\n"
        )
        return 1
    try:
        backtests = asyncio.run(
            _seed_database(settings.database_url.get_secret_value(), args.email)
        )
    except UnknownUser:
        sys.stderr.write(f"{PROG}: error: no user has the email {args.email}\n")
        return 1
    numbers = [backtest.number for backtest in backtests]
    sys.stdout.write(
        f"Added {len(backtests)} test backtests (#{min(numbers)} to #{max(numbers)}) to user "
        f"{backtests[0].user_id}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
