"""What the API reference says about API keys, from the dependencies each route takes.

FastAPI lists the key scheme on every route that depends on it, with the scopes the route
declares: none on a route of ``CurrentUserDep``. ``document_api_keys`` rewrites that into what
a key may really do there (``dependencies``): ``read`` on a GET, nothing on a route that
changes something (only the session may). Every route a key may call documents the key's
problems, and every route of the signed-in user that keys may not call says so in its 403.
"""

from typing import Any

from candlestack.accounts.dependencies import API_KEY_RATE_LIMIT, API_KEY_SCHEME
from candlestack.accounts.limits import HEAVY, USER_RATE_LIMIT
from candlestack.accounts.schemas import Scope

_PROBLEM = {"application/problem+json": {"schema": {"$ref": "#/components/schemas/Problem"}}}
_KEY_PROBLEMS = {
    "401": "With an API key: the key is unknown, revoked or expired, or its account is no "
    "longer on the beta list (`invalid-api-key`).",
    "403": "The API key does not have a scope this needs (`missing-scope`; `scope` names it).",
    "429": f"More than {API_KEY_RATE_LIMIT} requests in one minute with this API key, or "
    f"{USER_RATE_LIMIT} across all the account's keys and connected apps, where a heavy request "
    f"counts as {HEAVY} (`rate-limited`).",
}
_SESSION_REQUIRED = "API keys cannot do this, only the signed-in browser (`session-required`)."


def _add_problem(operation: dict[str, Any], status: str, description: str) -> None:
    responses = operation.setdefault("responses", {})
    if status in responses:
        responses[status]["description"] += f" {description}"
    else:
        responses[status] = {"description": description, "content": _PROBLEM}


def _key_requirements(
    method: str, security: list[dict[str, list[str]]]
) -> list[dict[str, list[str]]]:
    """The route's security requirements as a key may meet them: the key scheme without
    scopes becomes ``read`` on a GET and is dropped on any other method."""
    requirements = []
    for requirement in security:
        if requirement.get(API_KEY_SCHEME) != []:
            requirements.append(requirement)
        elif method == "get":
            requirements.append({API_KEY_SCHEME: [Scope.read.value]})
    return requirements


def document_api_keys(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrites a generated OpenAPI schema so the API key scheme and its problems say what a
    key may do on each route."""
    for operations in schema.get("paths", {}).values():
        for method, operation in operations.items():
            requirements = _key_requirements(method, operation.pop("security", []))
            if requirements:
                operation["security"] = requirements
                for status, description in _KEY_PROBLEMS.items():
                    _add_problem(operation, status, description)
            elif "401" in operation.get("responses", {}):
                _add_problem(operation, "403", _SESSION_REQUIRED)
    return schema
