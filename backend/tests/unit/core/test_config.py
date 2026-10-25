from pathlib import Path

import pytest
from pydantic import ValidationError

from candlestack.core import Settings, get_settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults() -> None:
    settings = Settings()

    assert settings.app_env == "local"
    assert settings.app_version == "dev"
    assert settings.app_commit == "unknown"
    assert settings.log_level == "INFO"
    assert settings.redis_url == "redis://127.0.0.1:6379/0"
    assert settings.alpaca_key_id.get_secret_value() == ""
    assert settings.alpaca_secret_key.get_secret_value() == ""
    assert settings.alpaca_api_url == "https://paper-api.alpaca.markets"
    assert settings.alpaca_data_url == "https://data.alpaca.markets"
    assert settings.binance_api_url == "https://api.binance.com"
    assert settings.binance_data_url == "https://data.binance.vision"
    assert settings.logo_dev_key.get_secret_value() == ""
    assert settings.logo_dev_url == "https://img.logo.dev"
    assert settings.candles_max == 50_000
    assert settings.client_rate_limit == 60
    assert settings.alpaca_rate_limit == 60
    assert settings.binance_weight_limit == 1000
    assert settings.logo_dev_rate_limit == 20
    assert settings.public_url == "http://localhost:5173"
    assert settings.database_url.get_secret_value() == (
        "postgresql+asyncpg://candlestack:candlestack@127.0.0.1:5432/candlestack"
    )
    assert settings.oauth_github_client_id == ""
    assert settings.oauth_github_client_secret.get_secret_value() == ""
    assert settings.oauth_google_client_id == ""
    assert settings.oauth_google_client_secret.get_secret_value() == ""
    assert settings.beta_allowlist == frozenset()


@pytest.mark.parametrize(
    ("app_env", "url"),
    [
        ("prod", "https://app.candlestack.tech"),
        ("stage", "https://stage.candlestack.tech"),
        ("local", "http://localhost:5173"),
        ("test", "http://testserver"),
    ],
)
def test_public_url_default_depends_on_env(
    monkeypatch: pytest.MonkeyPatch, app_env: str, url: str
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    assert Settings().public_url == url


def test_public_url_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_URL", " http://127.0.0.1:8000/ ")

    assert Settings().public_url == "http://127.0.0.1:8000"


def test_beta_allowlist_is_comma_separated_and_any_case(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BETA_ALLOWLIST", " Ada@Example.com,bob@example.com ,, ")

    assert Settings().beta_allowlist == frozenset({"ada@example.com", "bob@example.com"})


def test_oauth_secrets_and_database_url_are_stripped_and_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_ID", " Iv1.gh \n")
    monkeypatch.setenv("OAUTH_GITHUB_CLIENT_SECRET", " gh-secret-1\n")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_ID", "g.apps.googleusercontent.com ")
    monkeypatch.setenv("OAUTH_GOOGLE_CLIENT_SECRET", "\tg-secret-2")
    monkeypatch.setenv("DATABASE_URL", " postgresql+asyncpg://app:db-secret-3@db:5432/app ")

    settings = Settings()

    assert settings.oauth_github_client_id == "Iv1.gh"
    assert settings.oauth_github_client_secret.get_secret_value() == "gh-secret-1"
    assert settings.oauth_google_client_id == "g.apps.googleusercontent.com"
    assert settings.oauth_google_client_secret.get_secret_value() == "g-secret-2"
    assert settings.database_url.get_secret_value() == (
        "postgresql+asyncpg://app:db-secret-3@db:5432/app"
    )
    for secret in ("gh-secret-1", "g-secret-2", "db-secret-3"):
        assert secret not in repr(settings)


@pytest.mark.parametrize(
    ("app_env", "limit"),
    [("prod", 150), ("stage", 100), ("local", 60), ("test", 60)],
)
def test_alpaca_rate_limit_default_depends_on_env(
    monkeypatch: pytest.MonkeyPatch, app_env: str, limit: int
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    assert Settings().alpaca_rate_limit == limit


def test_alpaca_rate_limit_default_follows_init_arguments() -> None:
    assert Settings(app_env="stage").alpaca_rate_limit == 100


def test_alpaca_rate_limit_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("ALPACA_RATE_LIMIT", "100")

    assert Settings().alpaca_rate_limit == 100


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "stage")
    monkeypatch.setenv("APP_VERSION", "main-abc")
    monkeypatch.setenv("CANDLES_MAX", "1000")
    monkeypatch.setenv("CLIENT_RATE_LIMIT", "0")
    monkeypatch.setenv("LOG_LEVEL", "debug")

    settings = Settings()

    assert settings.app_env == "stage"
    assert settings.app_version == "main-abc"
    assert settings.candles_max == 1000
    assert settings.client_rate_limit == 0
    assert settings.log_level == "DEBUG"


def test_empty_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CANDLES_MAX", "")
    monkeypatch.setenv("ALPACA_RATE_LIMIT", "")

    settings = Settings()

    assert settings.candles_max == 50_000
    assert settings.alpaca_rate_limit == 60


def test_alpaca_keys_are_stripped_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALPACA_KEY_ID", "  PKTEST123 \n")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "\tsecret456  ")

    settings = Settings()

    assert settings.alpaca_key_id.get_secret_value() == "PKTEST123"
    assert settings.alpaca_secret_key.get_secret_value() == "secret456"
    assert "PKTEST123" not in repr(settings)
    assert "secret456" not in repr(settings)


@pytest.mark.parametrize(
    ("app_env", "limit"),
    [("prod", 50), ("stage", 20), ("local", 20), ("test", 20)],
)
def test_logo_dev_rate_limit_default_depends_on_env(
    monkeypatch: pytest.MonkeyPatch, app_env: str, limit: int
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    assert Settings().logo_dev_rate_limit == limit


@pytest.mark.parametrize(
    ("app_env", "refresh"),
    [("prod", True), ("stage", True), ("local", False), ("test", False)],
)
def test_stats_refresh_default_depends_on_env(
    monkeypatch: pytest.MonkeyPatch, app_env: str, refresh: bool
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)

    assert Settings().stats_refresh is refresh


def test_stats_refresh_can_be_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("STATS_REFRESH", "true")

    assert Settings().stats_refresh is True


def test_logo_dev_key_is_stripped_and_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGO_DEV_KEY", " pk_test123\n")

    settings = Settings()

    assert settings.logo_dev_key.get_secret_value() == "pk_test123"
    assert "pk_test123" not in repr(settings)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "staging"),
        ("APP_ENV", "pr-42"),
        ("LOG_LEVEL", "LOUD"),
        ("CANDLES_MAX", "0"),
        ("CLIENT_RATE_LIMIT", "-1"),
        ("PUBLIC_URL", "app.candlestack.tech"),
        ("PUBLIC_URL", "https://app.candlestack.tech/app"),
    ],
)
def test_rejects_invalid_values(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError):
        Settings()


def test_get_settings_is_read_once() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()


def test_the_jobs_of_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Settings()
    assert defaults.jobs_redis_url == "redis://127.0.0.1:6379/1"
    assert defaults.job_files_dir == Path("var/jobs")
    assert defaults.job_time_limit == 120

    monkeypatch.setenv("JOBS_REDIS_URL", "redis://queue:6379/0")
    monkeypatch.setenv("JOB_TIME_LIMIT", "30")

    assert (Settings().jobs_redis_url, Settings().job_time_limit) == ("redis://queue:6379/0", 30)


@pytest.mark.parametrize("limit", ["0", "3601"])
def test_a_jobs_time_limit_out_of_range_is_refused(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setenv("JOB_TIME_LIMIT", limit)

    with pytest.raises(ValidationError):
        Settings()
