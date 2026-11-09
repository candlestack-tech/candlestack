"""The parity table: every endpoint of the HTTP API, by its method and path in the API
reference, and the MCP tools that do the same, or why it stays web only.

A unit test fails when an endpoint is missing here or an entry names an endpoint or a tool that
does not exist, so an agent can do everything the web app does unless this table says why not.
An endpoint whose tools are not built yet is ``Planned`` with its task; when the tools land, the
entry becomes ``Tools``.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Tools:
    """The tools that do what the endpoint does."""

    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Planned:
    """Tools the task `issue` (#402 ...) adds; until then the agent has no way to do it."""

    issue: int
    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WebOnly:
    """No tool, on purpose: `reason` says why, for the docs (what is web only and why)."""

    reason: str


Entry = Tools | Planned | WebOnly

_MONITORING = "Health of the service for monitoring and the status page, not the user's data."
_SIGN_IN = "Signing in with GitHub or Google happens in the browser; an agent signs in with a key."
_SESSION = "The browser's session only: API keys cannot sign out or end sessions."
_KEYS = "API keys are managed in the signed-in browser only, so a key cannot make or revoke keys."
_OAUTH = (
    "The OAuth sign-in of MCP clients itself: the client and the browser call it to sign in, "
    "before any tool can run."
)
_CONSENT = "The consent page in the signed-in browser decides what an app may do, never the app."
_APPS = (
    "Connected apps are managed in the signed-in browser only, so an app cannot disconnect apps."
)

PARITY: dict[str, Entry] = {
    "GET /api/health": WebOnly(_MONITORING),
    "GET /api/health/sources": WebOnly(_MONITORING),
    "GET /api/v1/auth/denied": WebOnly(_SIGN_IN),
    "GET /api/v1/auth/{provider}/start": WebOnly(_SIGN_IN),
    "POST /api/v1/auth/sign-out": WebOnly(_SESSION),
    "POST /api/v1/auth/sign-out-everywhere": WebOnly(_SESSION),
    "GET /api/v1/me": Tools(("get_me",)),
    "PATCH /api/v1/me": WebOnly(
        "The display name is changed in Settings; API keys cannot change the profile."
    ),
    "DELETE /api/v1/me": WebOnly("Deleting the account needs the signed-in browser."),
    "DELETE /api/v1/me/sign-in-methods/{provider}": WebOnly(
        "Sign-in methods are managed in the signed-in browser."
    ),
    "GET /api/v1/me/api-keys": WebOnly(_KEYS),
    "POST /api/v1/me/api-keys": WebOnly(_KEYS),
    "DELETE /api/v1/me/api-keys/{key_id}": WebOnly(_KEYS),
    "GET /api/v1/oauth/authorize": WebOnly(_OAUTH),
    "POST /api/v1/oauth/token": WebOnly(_OAUTH),
    "POST /api/v1/oauth/register": WebOnly(_OAUTH),
    "POST /api/v1/oauth/revoke": WebOnly(_OAUTH),
    "GET /api/v1/oauth/requests/{request_id}": WebOnly(_CONSENT),
    "POST /api/v1/oauth/requests/{request_id}": WebOnly(_CONSENT),
    "GET /api/v1/me/connected-apps": WebOnly(_APPS),
    "DELETE /api/v1/me/connected-apps/{app_id}": WebOnly(_APPS),
    "GET /api/v1/me/preferences": Tools(("get_preferences", "get_backtest_defaults")),
    "PUT /api/v1/me/preferences/backtest-defaults": Tools(("update_backtest_defaults",)),
    "PATCH /api/v1/me/preferences/appearance": WebOnly(
        "The theme of the web app, which means nothing to an agent."
    ),
    "GET /api/v1/data/instruments": Tools(("search_instruments", "top_instruments")),
    "GET /api/v1/data/instruments/{instrument_id}": Tools(("get_instrument",)),
    "GET /api/v1/data/instruments/{instrument_id}/candles": Tools(("get_candles",)),
    "GET /api/v1/data/instruments/{instrument_id}/icon": WebOnly(
        "An image for the app's lists, which an agent does not need."
    ),
    "GET /api/v1/data/top-instruments": Tools(("top_instruments",)),
    "GET /api/v1/preprocessing/catalog": Tools(("preprocessing_catalog",)),
    "GET /api/v1/models": Tools(("list_models", "get_model")),
    "POST /api/v1/models": Tools(("create_model_upload", "get_model_upload")),
    "POST /api/v1/models/uploads": Tools(("create_model_upload",)),
    "GET /api/v1/models/uploads/{upload_id}": Tools(("get_model_upload",)),
    "GET /api/v1/uploads/{token}": WebOnly(
        "The upload page reads its link for the signed-in browser; an agent reads the upload "
        "with get_model_upload."
    ),
    "PUT /api/v1/uploads/{token}": WebOnly(
        "The file goes to the one-time link with curl or the upload page, not through a tool."
    ),
    "PATCH /api/v1/models/{model_id}": Tools(("rename_model", "star_model")),
    "DELETE /api/v1/models/{model_id}": Tools(("delete_model",)),
    "GET /api/v1/models/{model_id}/manifest": Tools(("get_manifest",)),
    "PUT /api/v1/models/{model_id}/manifest": Tools(("set_manifest",)),
    "POST /api/v1/models/{model_id}/manifest/check": Tools(("validate_manifest",)),
    "POST /api/v1/backtests": Tools(("start_backtest",)),
    "POST /api/v1/backtests/check": Tools(("validate_backtest",)),
    "GET /api/v1/backtests/{backtest_id}": Tools(
        ("get_backtest", "get_backtest_status", "wait_for_backtest")
    ),
    "GET /api/v1/backtests/{backtest_id}/download": WebOnly(
        "The whole result document as a file to save; an agent reads the same result in pages "
        "with get_backtest."
    ),
    "GET /api/v1/compare": Tools(("compare_backtests",)),
    "GET /api/v1/backtests": Tools(("list_backtests",)),
    "PATCH /api/v1/backtests/{backtest_id}": Tools(
        ("rename_backtest", "star_backtest", "publish_backtest", "unpublish_backtest")
    ),
    "POST /api/v1/backtests/{backtest_id}/cancel": Tools(("cancel_backtest",)),
    "DELETE /api/v1/backtests/{backtest_id}": Tools(("delete_backtests",)),
    "POST /api/v1/backtests/delete": Tools(("delete_backtests",)),
    "POST /api/v1/backtests/update": Tools(("star_backtests", "publish_backtests")),
    "GET /api/v1/experiments": Tools(("list_experiments",)),
    "GET /api/v1/dashboard": Tools(("get_dashboard",)),
    "POST /api/v1/sweeps": Tools(("validate_sweep", "run_sweep")),
    "GET /api/v1/sweeps": Tools(("list_sweeps",)),
    "GET /api/v1/sweeps/{sweep_id}": Tools(("get_sweep", "wait_for_sweep")),
}


def endpoints_of(tool: str) -> list[str]:
    """The endpoints the tool does the work of, in the order of the table."""
    return [
        endpoint
        for endpoint, entry in PARITY.items()
        if isinstance(entry, Tools) and tool in entry.names
    ]


def web_only() -> dict[str, str]:
    """The endpoints that have no tool on purpose, and why."""
    return {
        endpoint: entry.reason for endpoint, entry in PARITY.items() if isinstance(entry, WebOnly)
    }
