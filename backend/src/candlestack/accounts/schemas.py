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


# --- API keys ------------------------------------------------------------------------------------

# The most keys a user has that are neither revoked nor expired.
API_KEYS_MAX = 10
API_KEY_NAME_MAX = 80
# The longest expiry a new key can get.
API_KEY_DAYS_MAX = 365


class Scope(StrEnum):
    """What an API key may do. `read`: every GET; `backtests`: start, rename and star
    backtests; `models`: upload models, add their manifests, rename, star and delete them;
    `publish`: publish and unpublish backtests. Managing keys, deleting the account, sign-in
    methods and signing out need the browser session whatever the scopes."""

    read = "read"
    backtests = "backtests"
    models = "models"
    publish = "publish"


# The order scopes are listed in, whatever order they were given in.
SCOPE_ORDER = tuple(Scope)


class ApiKeyIn(BaseModel):
    """A new API key."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=API_KEY_NAME_MAX,
        pattern=r"^[^\x00-\x1f\x7f]+$",
        description="What the key is for, without control characters; surrounding whitespace "
        "is dropped",
        examples=["Claude Code on my laptop"],
    )
    scopes: list[Scope] = Field(
        min_length=1,
        max_length=len(Scope),
        description="What the key may do, at least one; a scope given twice counts once",
        examples=[["read", "backtests"]],
    )
    expires_in_days: int | None = Field(
        default=None,
        ge=1,
        le=API_KEY_DAYS_MAX,
        description=f"Days until the key stops working, 1 to {API_KEY_DAYS_MAX}; null or left "
        "out: it works until revoked",
        examples=[90],
    )


class ApiKeyOut(BaseModel):
    """An API key, without its secret: that is shown once, when the key is created."""

    id: str = Field(
        pattern=r"^key_[a-z2-7]{10}$",
        description="Public ID of the key, for revoking it",
        examples=["key_7f3k2m4qx4"],
    )
    name: str = Field(description="What the key is for", examples=["Claude Code on my laptop"])
    scopes: list[Scope] = Field(
        description="What the key may do, in the order read, backtests, models, publish",
        examples=[["read", "backtests"]],
    )
    prefix: str = Field(
        pattern=r"^cs_[a-z2-7]{8}$",
        description="The start of the key, to tell keys apart",
        examples=["cs_7f3k2m4q"],
    )
    created_at: datetime = Field(description="When the key was created (UTC)")
    expires_at: datetime | None = Field(
        description="When the key stops working (UTC); null: it works until revoked"
    )
    last_used_at: datetime | None = Field(
        description="When a request last signed in with the key (UTC); written at most once a "
        "minute, so it may lag by up to a minute; null: never used"
    )


class ApiKeyCreated(ApiKeyOut):
    """A new API key with its secret, which only this response shows."""

    # Kept out of the repr, so an error report holding this object never shows the secret.
    secret: str = Field(
        repr=False,
        pattern=r"^cs_[a-z2-7]{40}$",
        description="The key itself, for `Authorization: Bearer <secret>`. Shown only in this "
        "response: CandleStack keeps just its SHA-256, so copy it now",
        examples=["cs_7f3k2m4q" + "x" * 32],
    )


class ApiKeysOut(BaseModel):
    """The user's API keys that are not revoked, oldest first; an expired one stays listed
    until it is revoked."""

    items: list[ApiKeyOut]
    limit: int = Field(
        description="The most keys a user can have that are neither revoked nor expired",
        examples=[API_KEYS_MAX],
    )


# --- OAuth for MCP clients -----------------------------------------------------------------------

# What each scope lets an app do, in the words of the consent screen.
SCOPE_WORDS = {
    Scope.read: "See your backtests, models and market data",
    Scope.backtests: "Start, rename and star backtests",
    Scope.models: "Upload, rename and delete your models",
    Scope.publish: "Publish and unpublish backtests in Experiments",
}


class Decision(StrEnum):
    allow = "allow"
    deny = "deny"


class ScopeOut(BaseModel):
    """A scope an app asks for, in plain words."""

    scope: Scope
    description: str = Field(examples=[SCOPE_WORDS[Scope.read]])


class ClientOut(BaseModel):
    """The app as it describes itself: anyone can register an app named Claude, so the host it
    returns to is what tells apps apart."""

    name: str = Field(
        description="The app's name; the host it returns to when it gave none",
        examples=["Claude"],
    )
    logo_uri: str | None = Field(description="The app's logo, if it gave one")
    client_uri: str | None = Field(description="The app's home page, if it gave one")
    redirect_host: str = Field(
        description="The host the browser returns to after the decision", examples=["claude.ai"]
    )


class AuthorizationRequestOut(BaseModel):
    """An app's request for access to the user's account, for the consent screen."""

    client: ClientOut
    scopes: list[ScopeOut] = Field(
        description="What the app asks for, in the order read, backtests, models, publish; read "
        "is always granted"
    )
    expires_at: datetime = Field(description="Until when the request can be decided (UTC)")


class ConsentIn(BaseModel):
    """The user's decision on an app's request."""

    model_config = ConfigDict(extra="forbid")

    decision: Decision
    scopes: list[Scope] = Field(
        default_factory=list,
        max_length=len(Scope),
        description="With `allow`: the scopes to grant, each one the app asked for; read is "
        "always granted. Ignored with `deny`",
        examples=[["read", "backtests"]],
    )


class ConsentOut(BaseModel):
    """Where the browser goes next: back to the app, with a code or `error=access_denied`."""

    redirect_to: str = Field(
        description="The app's redirect URI with `code` (or `error`), `state` and `iss`",
        examples=["https://claude.ai/api/mcp/auth_callback?code=...&state=...&iss=..."],
    )


class ConnectedAppOut(BaseModel):
    """An app the user allowed to act for them."""

    id: str = Field(
        pattern=r"^app_[a-z2-7]{10}$",
        description="Public ID of the connected app, for disconnecting it",
        examples=["app_7f3k2m4qx4"],
    )
    client: ClientOut
    scopes: list[Scope] = Field(
        description="What the app may do, as of the user's last consent",
        examples=[["read", "backtests"]],
    )
    connected_at: datetime = Field(description="When the user first allowed the app (UTC)")
    last_used_at: datetime | None = Field(
        description="When the app last called the MCP server (UTC); written at most once a "
        "minute; null: never"
    )


class ConnectedAppsOut(BaseModel):
    """The apps the user allowed, the most recently connected first."""

    items: list[ConnectedAppOut]


class TokenEndpointAuthMethod(StrEnum):
    """How a client proves itself at the token endpoint: `none` for a public client (PKCE
    alone), or with the secret it got at registration."""

    none = "none"
    client_secret_post = "client_secret_post"  # noqa: S105 (a method's name)
    client_secret_basic = "client_secret_basic"  # noqa: S105 (a method's name)


class ClientMetadataIn(BaseModel):
    """What a client says about itself (RFC 7591), in a registration request or its client ID
    metadata document. Members the server does not use are ignored."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    redirect_uris: list[str] = Field(
        min_length=1,
        max_length=10,
        description="Where the browser returns with the code: https URLs, or http on a "
        "loopback address (127.0.0.1, [::1], localhost) for a native app, whose port may "
        "change from one sign-in to the next",
        examples=[["https://claude.ai/api/mcp/auth_callback"]],
    )
    client_name: str | None = Field(
        default=None, max_length=100, description="The app's name", examples=["Claude"]
    )
    client_uri: str | None = Field(default=None, max_length=512)
    logo_uri: str | None = Field(default=None, max_length=512)
    token_endpoint_auth_method: TokenEndpointAuthMethod = Field(
        default=TokenEndpointAuthMethod.client_secret_basic,
        description="`none`, `client_secret_post` or `client_secret_basic` (the default of "
        "RFC 7591); a metadata document may only say `none`",
    )
    grant_types: list[str] = Field(
        default=["authorization_code", "refresh_token"],
        description="`authorization_code` and `refresh_token` only",
    )
    response_types: list[str] = Field(default=["code"], description="`code` only")


class ClientRegistrationOut(BaseModel):
    """A registered client (RFC 7591): its ID, its secret when it asked for one, and its
    metadata as the server keeps it."""

    client_id: str = Field(examples=["ocl_7f3k2m4qx4abcdef"])
    client_secret: str | None = Field(
        default=None,
        repr=False,
        description="Only for `client_secret_post` and `client_secret_basic`; shown only here",
    )
    client_id_issued_at: int = Field(description="When it was registered (epoch seconds)")
    client_secret_expires_at: int | None = Field(
        default=None, description="0 with a secret: it does not expire"
    )
    redirect_uris: list[str]
    client_name: str | None = None
    client_uri: str | None = None
    logo_uri: str | None = None
    token_endpoint_auth_method: TokenEndpointAuthMethod
    grant_types: list[str]
    response_types: list[str]


class TokenOut(BaseModel):
    """The tokens of an authorization or a refresh (RFC 6749, section 5.1)."""

    access_token: str = Field(repr=False, description="`csa_...`, for `Authorization: Bearer`")
    token_type: str = Field(default="Bearer", examples=["Bearer"])
    expires_in: int = Field(description="Seconds the access token works", examples=[3600])
    refresh_token: str = Field(
        repr=False, description="`csr_...`, works once: the answer to it holds a new one"
    )
    scope: str = Field(description="The granted scopes, space-separated", examples=["read"])


class OAuthErrorOut(BaseModel):
    """An OAuth error (RFC 6749, section 5.2; RFC 7591, section 3.2.2)."""

    error: str = Field(examples=["invalid_grant"])
    error_description: str = Field(examples=["The code is unknown, used or expired."])
