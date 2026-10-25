"""Application settings, read from environment variables (no prefix)."""

from functools import cache
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# Our Alpaca request budget per minute. Alpaca allows 200 per key: prod and stage have one
# each.
_ALPACA_RATE_LIMITS = {"prod": 150, "stage": 100}
_ALPACA_RATE_LIMIT_DEFAULT = 60


def default_alpaca_rate_limit(app_env: str) -> int:
    return _ALPACA_RATE_LIMITS.get(app_env, _ALPACA_RATE_LIMIT_DEFAULT)


# Our logo.dev request budget per minute. logo.dev allows 100 per minute for the whole account,
# which every environment shares: prod and stage stay within it together.
_LOGO_DEV_RATE_LIMITS = {"prod": 50, "stage": 20}
_LOGO_DEV_RATE_LIMIT_DEFAULT = 20


def default_logo_dev_rate_limit(app_env: str) -> int:
    return _LOGO_DEV_RATE_LIMITS.get(app_env, _LOGO_DEV_RATE_LIMIT_DEFAULT)


# The environments that refresh the instrument stats (24 h volume and change) in the
# background: the others spend no source budget on them and list instruments without stats.
_STATS_REFRESH_ENVS = frozenset({"prod", "stage"})


def default_stats_refresh(app_env: str) -> bool:
    return app_env in _STATS_REFRESH_ENVS


# Share of requests traced in Sentry: prod has real traffic, stage mostly the smoke tests.
_TRACES_SAMPLE_RATE_PROD = 0.2
_TRACES_SAMPLE_RATE_DEFAULT = 1.0


def default_traces_sample_rate(app_env: str) -> float:
    return _TRACES_SAMPLE_RATE_PROD if app_env == "prod" else _TRACES_SAMPLE_RATE_DEFAULT


# Where each environment is reached; the OAuth callbacks, the cookies and the same-origin check
# of cookie-authenticated requests depend on it.
_PUBLIC_URLS = {
    "prod": "https://app.candlestack.tech",
    "stage": "https://stage.candlestack.tech",
    "local": "http://localhost:5173",
    "test": "http://testserver",
}


def default_public_url(app_env: str) -> str:
    return _PUBLIC_URLS[app_env]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore", frozen=True)

    app_env: str = Field(default="local", pattern=r"^(local|test|stage|prod)$")
    app_version: str = "dev"
    app_commit: str = "unknown"
    log_level: LogLevel = "INFO"

    public_url: str = Field(
        default_factory=lambda data: default_public_url(data.get("app_env", "local")),
        pattern=r"^https?://[^/]+$",
    )

    redis_url: str = "redis://127.0.0.1:6379/0"
    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://candlestack:candlestack@127.0.0.1:5432/candlestack"
    )

    oauth_github_client_id: str = ""
    oauth_github_client_secret: SecretStr = SecretStr("")
    oauth_google_client_id: str = ""
    oauth_google_client_secret: SecretStr = SecretStr("")
    # Emails that may sign in with GitHub or Google, lowercased; empty lets nobody in.
    beta_allowlist: Annotated[frozenset[str], NoDecode] = frozenset()

    alpaca_key_id: SecretStr = SecretStr("")
    alpaca_secret_key: SecretStr = SecretStr("")
    alpaca_api_url: str = "https://paper-api.alpaca.markets"
    alpaca_data_url: str = "https://data.alpaca.markets"
    binance_api_url: str = "https://api.binance.com"
    binance_data_url: str = "https://data.binance.vision"
    logo_dev_key: SecretStr = SecretStr("")
    logo_dev_url: str = "https://img.logo.dev"

    # The files of the uploaded models; relative to the working directory.
    model_storage_dir: Path = Path("var/models")

    # The jobs of the worker that runs the models (candlestack.jobs): a Redis of their own, so
    # the worker reaches none of the cache's keys, and a directory the backend and the worker
    # share for the candles and the predictions; relative to the working directory.
    jobs_redis_url: str = "redis://127.0.0.1:6379/1"
    job_files_dir: Path = Path("var/jobs")
    # How long the worker lets one job run before it stops it, in seconds.
    job_time_limit: float = Field(default=120, gt=0, le=3600)

    candles_max: int = Field(default=50_000, gt=0)
    client_rate_limit: int = Field(default=60, ge=0)
    alpaca_rate_limit: int = Field(
        default_factory=lambda data: default_alpaca_rate_limit(data.get("app_env", "local")),
        gt=0,
    )
    binance_weight_limit: int = Field(default=1000, gt=0)
    logo_dev_rate_limit: int = Field(
        default_factory=lambda data: default_logo_dev_rate_limit(data.get("app_env", "local")),
        gt=0,
    )

    stats_refresh: bool = Field(
        default_factory=lambda data: default_stats_refresh(data.get("app_env", "local"))
    )

    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = Field(
        default_factory=lambda data: default_traces_sample_rate(data.get("app_env", "local")),
        ge=0,
        le=1,
    )

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "alpaca_key_id",
        "alpaca_secret_key",
        "logo_dev_key",
        "database_url",
        "oauth_github_client_id",
        "oauth_github_client_secret",
        "oauth_google_client_id",
        "oauth_google_client_secret",
        mode="before",
    )
    @classmethod
    def _strip_key(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("public_url", mode="before")
    @classmethod
    def _strip_trailing_slash(cls, value: Any) -> Any:
        return value.strip().rstrip("/") if isinstance(value, str) else value

    @field_validator("beta_allowlist", mode="before")
    @classmethod
    def _split_emails(cls, value: Any) -> Any:
        """``a@x.com, B@y.com`` from the environment: comma-separated, any case."""
        if isinstance(value, str):
            value = value.split(",")
        if isinstance(value, list | tuple | set | frozenset):
            return frozenset(email.strip().lower() for email in value if email.strip())
        return value


@cache
def get_settings() -> Settings:
    """Settings of this process, read once from the environment."""
    return Settings()


def request_settings(request: Request) -> Settings:
    """Settings the running app was created with (see ``create_app``)."""
    return request.app.state.settings


SettingsDep = Annotated[Settings, Depends(request_settings)]
