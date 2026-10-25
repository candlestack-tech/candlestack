"""Request and response bodies of the backtests and dashboard API, and the documents a backtest
stores (its instruments and setup)."""

from datetime import date, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Backtests per page of the list.
PAGE_SIZE = 10
# The longest search the list takes.
QUERY_MAX = 100
# The longest name of a model (models.name, backtests.model_name).
MODEL_NAME_MAX = 255
# The most points of a backtest's equity series: enough for a small chart.
EQUITY_POINTS = 60


class BacktestStatus(StrEnum):
    """`finished` (it has results) or `failed` (it has an error); `running` comes with the
    backtest builder."""

    running = "running"
    finished = "finished"
    failed = "failed"


class Representation(StrEnum):
    """What the model reads: Renko bricks, a Kagi line or time windows of candles."""

    renko = "renko"
    kagi = "kagi"
    windows = "windows"


class InstrumentMarket(StrEnum):
    """The market of an instrument, as the market data API names it."""

    crypto = "crypto"
    stock = "stock"


class SetupTimeframe(StrEnum):
    """Timeframes a backtest runs on: those of the data."""

    m1 = "1m"
    m5 = "5m"
    m15 = "15m"
    m30 = "30m"
    h1 = "1h"
    h4 = "4h"
    d1 = "1d"


class SetupDirection(StrEnum):
    long = "long"
    both = "both"


class SetupBenchmark(StrEnum):
    """What a backtest is measured against: buy and hold of its own instruments, or an ETF."""

    buy_and_hold = "bh"
    spy = "SPY"
    qqq = "QQQ"
    dia = "DIA"


class BacktestInstrument(BaseModel):
    market: InstrumentMarket
    symbol: str = Field(description="Symbol as the market data API names it", examples=["AAPL"])


# Reads what is stored without the limits of a new backtest (BacktestSetupIn), so a limit made
# tighter later never turns away the backtests that ran before it.
class BacktestSetup(BaseModel):
    """The settings a backtest ran with: the fields of Settings, Backtest defaults."""

    timeframe: SetupTimeframe
    period_start: date = Field(description="First day of the test")
    period_end: date = Field(description="Last day of the test")
    direction: SetupDirection
    threshold: float = Field(description="Signal strength to trade")
    stop_loss_pct: int = Field(description="Exit when a trade loses this many percent")
    fees_pct: float = Field(description="Fees in percent per side")
    slippage_pct: float = Field(description="Slippage in percent")
    benchmark: SetupBenchmark


class BacktestSetupIn(BacktestSetup):
    """The setup of a new backtest, checked against the limits of Settings, Backtest defaults:
    the seed command stores it through this model. A backtest run in the app stores the summary
    of its own setup (runs.py), which may lie outside these limits."""

    threshold: float = Field(ge=0, le=0.5, description="Signal strength to trade")
    stop_loss_pct: int = Field(ge=1, le=20, description="Exit when a trade loses this many percent")
    fees_pct: float = Field(ge=0, le=5, description="Fees in percent per side")
    slippage_pct: float = Field(ge=0, le=5, description="Slippage in percent")

    @model_validator(mode="after")
    def _period_moves_forward(self) -> Self:
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


# Reads what is stored without the checks of a new series (EquitySeriesIn).
class EquitySeries(BaseModel):
    """The equity of the strategy and of buy and hold over the test period, for a small chart:
    cumulative returns as fractions (0 at the start; 0.246 is 24.6 %), sampled at evenly spaced
    times from the first day to the last, the same times in both."""

    strategy: list[float] = Field(
        description=f"Cumulative return of the strategy, 2 to {EQUITY_POINTS} points",
        examples=[[0.0, 0.031, 0.118, 0.246]],
    )
    benchmark: list[float] = Field(
        description="Cumulative return of buying and holding the instruments, as many points",
        examples=[[0.0, 0.12, 0.35, 0.682]],
    )


class EquitySeriesIn(EquitySeries):
    """A new equity series, checked: as many points in both, 2 to ``EQUITY_POINTS`` each,
    finite numbers."""

    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")

    strategy: list[float] = Field(min_length=2, max_length=EQUITY_POINTS)
    benchmark: list[float] = Field(min_length=2, max_length=EQUITY_POINTS)

    @model_validator(mode="after")
    def _same_times(self) -> Self:
        if len(self.strategy) != len(self.benchmark):
            raise ValueError("strategy and benchmark must have as many points")
        return self


class UsedModel(BaseModel):
    """The model a backtest used."""

    id: str | None = Field(
        description="Public ID of the model; null once the model was deleted",
        examples=["mdl_7f3k2m9qx4"],
    )
    name: str = Field(
        description=(
            "The model's current name, or the name it had when the backtest ran once it was deleted"
        ),
        examples=["blesk-novol-8.keras"],
    )
    framework: str = Field(
        description="Framework of the model file when the backtest ran", examples=["keras"]
    )


class BacktestOut(BaseModel):
    """A backtest of the user. Returns and rates are fractions: 0.246 is 24.6 %. The results are
    null for a backtest that did not finish."""

    id: str = Field(description="Public ID of the backtest", examples=["bt_7f3k2m9qx4"])
    number: int = Field(description="The user's own number of the backtest, shown as #N", ge=1)
    status: BacktestStatus
    instruments: list[BacktestInstrument] = Field(
        description="The instruments, in the order chosen"
    )
    representation: Representation
    model: UsedModel
    setup: BacktestSetup
    total_return: float | None = Field(
        description="Return of the strategy over the period, a fraction", examples=[0.246]
    )
    buy_and_hold_return: float | None = Field(
        description="Return of buying and holding the instruments over the period, a fraction",
        examples=[0.682],
    )
    sharpe: float | None = Field(description="Sharpe ratio, per year", examples=[0.79])
    max_drawdown: float | None = Field(
        description="Largest fall from a peak of the equity, a negative fraction (or 0)",
        examples=[-0.197],
    )
    win_rate: float | None = Field(
        description="Share of the trades that made money, a fraction", examples=[0.512]
    )
    num_trades: int | None = Field(description="Number of trades", examples=[1712])
    equity: EquitySeries | None = Field(
        description="A short equity series of the strategy and of buy and hold, for a small "
        "chart; null for a backtest that did not finish or has none"
    )
    duration_s: float | None = Field(
        description="How long the run took, in seconds", examples=[162.0]
    )
    error: str | None = Field(
        description="What went wrong and what to do, for a failed backtest; else null",
        examples=[None],
    )
    starred: bool = Field(description="The user starred the backtest")
    created_at: datetime = Field(description="When the backtest started (UTC)")
    finished_at: datetime | None = Field(
        description="When it finished or failed (UTC); null while it runs"
    )


class BacktestShow(StrEnum):
    """Which backtests the list shows: all, the starred or the failed ones."""

    all = "all"
    starred = "starred"
    failed = "failed"


class BacktestSort(StrEnum):
    """What the list is sorted by. `default` is newest first (`order` does not apply); `when`
    sorts like `number`. Backtests without the value (failed ones) come last either way, and
    ties go by number, newest first."""

    default = "default"
    number = "number"
    model = "model"
    total_return = "return"
    sharpe = "sharpe"
    max_drawdown = "max_drawdown"
    trades = "trades"
    when = "when"


class SortOrder(StrEnum):
    asc = "asc"
    desc = "desc"


class BacktestCounts(BaseModel):
    """The user's backtests in all, starred and failed."""

    all: int = Field(examples=[15])
    starred: int = Field(examples=[5])
    failed: int = Field(examples=[2])


class ModelCount(BaseModel):
    """A model the user's backtests used, by the name they show for it."""

    name: str = Field(examples=["blesk-novol-8.keras"])
    framework: str = Field(examples=["keras"])
    count: int = Field(description="Backtests that used it", examples=[9])


class BacktestsPage(BaseModel):
    backtests: list[BacktestOut] = Field(description=f"The page, at most {PAGE_SIZE} backtests")
    total: int = Field(description="Backtests that match the filters", examples=[15])
    page: int = Field(description="The page, from 1", examples=[1])
    pages: int = Field(description="Pages of the backtests that match, at least 1", examples=[2])
    counts: BacktestCounts = Field(description="All the user's backtests, whatever the filters")
    models: list[ModelCount] = Field(
        description=(
            "Every model the user's backtests used, whatever the filters, the most used first, "
            "then by name"
        )
    )


class BacktestUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starred: bool = Field(description="Star or unstar the backtest")


class BestBacktests(BaseModel):
    """The 5 best finished backtests by each result, the best first (for `max_drawdown` the
    smallest fall)."""

    sharpe: list[BacktestOut]
    total_return: list[BacktestOut]
    max_drawdown: list[BacktestOut]
    win_rate: list[BacktestOut]


class StarredBacktests(BaseModel):
    backtests: list[BacktestOut] = Field(
        description="The 4 starred backtests with the best Sharpe ratio, those without one last"
    )
    total: int = Field(description="All the starred backtests", examples=[5])


class DashboardOut(BaseModel):
    """Everything the Dashboard shows, in one response."""

    counts: BacktestCounts
    last: list[BacktestOut] = Field(
        description="The 6 newest backtests that ended, failed ones included (not running ones)"
    )
    best: BestBacktests
    starred: StarredBacktests
