"""Request and response bodies of the accounts API (the contract the web app is built
against)."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The longest display name: what a user may save, and where a provider's name is cut.
DISPLAY_NAME_MAX = 80


class Provider(StrEnum):
    """An identity provider a user signs in with."""

    github = "github"
    google = "google"


class SignInIntent(StrEnum):
    sign_in = "sign-in"
    connect = "connect"


class SignInMethodOut(BaseModel):
    """A sign-in method: the provider's account the user signs in with."""

    provider: Provider
    email: str = Field(
        description="The account's email at the provider, as of its last sign-in or connect",
        examples=["arsen@example.com"],
    )
    login: str | None = Field(
        description="The account's GitHub username; null for Google", examples=["arsen"]
    )


class MeOut(BaseModel):
    """The signed-in user."""

    id: str = Field(
        pattern=r"^usr_[a-z0-9]{10}$",
        description="Public user ID, shown in Settings and given to support",
        examples=["usr_7f3k2m9qx4"],
    )
    display_name: str = Field(description="Name shown in the app", examples=["Arsen Labovich"])
    email: str = Field(
        description="From the identity provider, read-only", examples=["arsen@example.com"]
    )
    providers: list[Provider] = Field(
        description="Connected sign-in methods (the providers of `sign_in_methods`)",
        examples=[["github"]],
    )
    sign_in_methods: list[SignInMethodOut] = Field(
        description="The account of each sign-in method, by provider"
    )
    created_at: datetime = Field(description="When the account was created (UTC)")


class DeniedSignInOut(BaseModel):
    """The last sign-in of this browser that the beta list turned away."""

    email: str = Field(description="The account's email", examples=["arsen@example.com"])
    provider: Provider


class MeUpdate(BaseModel):
    """What the user may change about themselves."""

    # The length limits apply to the stripped name, so a blank one is refused.
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    display_name: str = Field(
        min_length=1,
        max_length=DISPLAY_NAME_MAX,
        description="Name shown in the app; surrounding whitespace is dropped",
        examples=["Arsen Labovich"],
    )


class BacktestTimeframe(StrEnum):
    """Timeframes a new backtest can start with (Settings, Backtest defaults): those of the data."""

    m1 = "1m"
    m5 = "5m"
    m15 = "15m"
    m30 = "30m"
    h1 = "1h"
    h4 = "4h"
    d1 = "1d"


class Direction(StrEnum):
    long = "long"
    both = "both"


class Benchmark(StrEnum):
    """What each backtest is measured against: buy and hold of its own instruments, or an ETF."""

    buy_and_hold = "bh"
    spy = "SPY"
    qqq = "QQQ"
    dia = "DIA"


# The backtest defaults' fields, shared by the read model (with defaults) and the body that
# saves them (without).
_PeriodStart = Annotated[date, Field(description="First day of the test")]
_PeriodEnd = Annotated[date, Field(description="Last day of the test")]
_Threshold = Annotated[
    float, Field(ge=0, le=0.5, multiple_of=0.01, description="Signal strength to trade")
]
_StopLossPct = Annotated[
    int, Field(ge=1, le=20, description="Exit when a trade loses this many percent")
]
_FeesPct = Annotated[float, Field(ge=0, le=5, description="Fees in percent per side")]
_SlippagePct = Annotated[float, Field(ge=0, le=5, description="Slippage in percent")]


class BacktestDefaults(BaseModel):
    """How new backtests start (Settings, Backtest defaults)."""

    timeframe: BacktestTimeframe = BacktestTimeframe.m30
    period_start: _PeriodStart = date(2021, 1, 4)
    period_end: _PeriodEnd = date(2024, 12, 31)
    direction: Direction = Direction.long
    threshold: _Threshold = 0.05
    stop_loss_pct: _StopLossPct = 7
    fees_pct: _FeesPct = 0.1
    slippage_pct: _SlippagePct = 0.02
    benchmark: Benchmark = Benchmark.buy_and_hold

    @model_validator(mode="after")
    def _period_moves_forward(self) -> Self:
        if self.period_start >= self.period_end:
            raise ValueError("period_start must be before period_end")
        return self


class BacktestDefaultsIn(BacktestDefaults):
    """The backtest defaults to save (Save defaults): every field, and no other. The same
    checks as `BacktestDefaults`, but no defaults, so a body that leaves a field out is
    refused instead of filled in."""

    model_config = ConfigDict(extra="forbid")

    timeframe: BacktestTimeframe
    period_start: _PeriodStart
    period_end: _PeriodEnd
    direction: Direction
    threshold: _Threshold
    stop_loss_pct: _StopLossPct
    fees_pct: _FeesPct
    slippage_pct: _SlippagePct
    benchmark: Benchmark


class Theme(StrEnum):
    light = "light"
    dark = "dark"


class TimeZone(StrEnum):
    """`ny`: exchange time (New York); `local`: the browser's time zone."""

    ny = "ny"
    local = "local"


class DateFormat(StrEnum):
    """`dmy` 12 Oct 2024, `iso` 2024-10-12, `dot` 12.10.2024."""

    dmy = "dmy"
    iso = "iso"
    dot = "dot"


class TimeFormat(StrEnum):
    """`24` 16:05, `12` 4:05 PM."""

    h24 = "24"
    h12 = "12"


class Appearance(BaseModel):
    """How the app looks for this user (Settings, Appearance)."""

    theme: Theme = Theme.dark
    time_zone: TimeZone = TimeZone.ny
    date_format: DateFormat = DateFormat.dmy
    time_format: TimeFormat = TimeFormat.h24
    reduce_motion: bool = Field(default=False, description="Turns off the small animations")


class AppearanceUpdate(BaseModel):
    """A change of some of the appearance settings; the fields left out stay."""

    model_config = ConfigDict(extra="forbid")

    theme: Theme | None = None
    time_zone: TimeZone | None = None
    date_format: DateFormat | None = None
    time_format: TimeFormat | None = None
    reduce_motion: bool | None = None


class PreferencesOut(BaseModel):
    """The user's settings; a user who never saved any gets the defaults."""

    backtest_defaults: BacktestDefaults
    appearance: Appearance
