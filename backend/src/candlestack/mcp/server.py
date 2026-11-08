"""The MCP server: its instructions, the ``docs`` resource and the tools, on the official SDK.

``CandleStackMCP`` checks the API key's scopes before a tool runs and answers every failure as
a tool error with the API's problem (``convention.problem_result``): the ``ProblemError`` a tool
raised, ``validation`` for arguments that do not fit the input schema, ``not-found`` for an
unknown tool and ``internal-error`` for a crash, which is logged for a developer.
"""

import logging
from typing import Any, override

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.types import CallToolResult, InputRequiredResult
from pydantic import ValidationError

from candlestack.core import ProblemError
from candlestack.mcp import account, manage, models, run, sweeps
from candlestack.mcp.convention import ToolSpec, problem_result, tool_call
from candlestack.mcp.docs import DOCS_TEXT, DOCS_URI, INSTRUCTIONS

logger = logging.getLogger(__name__)

NAME = "candlestack"
TITLE = "CandleStack"
WEBSITE = "https://candlestack.tech"
# Every tool of the server, in the order tools/list and the tool reference show them.
TOOLS: tuple[ToolSpec, ...] = (
    *account.TOOLS,
    *models.TOOLS,
    *run.TOOLS,
    *manage.TOOLS,
    *sweeps.TOOLS,
)


def _invalid_arguments(error: ValidationError) -> ProblemError:
    """The ``validation`` problem of arguments that do not fit the input schema, as the API
    words a request's."""
    errors = [
        {"loc": list(item["loc"]), "msg": item["msg"], "type": item["type"]}
        for item in error.errors(include_url=False, include_input=False)
    ]
    detail = "; ".join(
        f"argument {'.'.join(str(part) for part in item['loc'])!r}: {item['msg']}"
        for item in errors
    )
    return ProblemError(
        422, "validation", "Invalid arguments", detail, extensions={"errors": errors}
    )


def _unknown_tool(name: str) -> ProblemError:
    return ProblemError(
        404, "not-found", "Not found", f"There is no tool {name!r}. tools/list names every tool."
    )


def _internal_error() -> ProblemError:
    return ProblemError(
        500,
        "internal-error",
        "Internal server error",
        "The tool failed on the server. Try again later; if it keeps failing, tell the user.",
    )


class CandleStackMCP(MCPServer):
    """The server with the tools of `specs`, their scopes checked and failures as problems."""

    def __init__(self, specs: tuple[ToolSpec, ...], *, version: str) -> None:
        super().__init__(
            name=NAME,
            title=TITLE,
            instructions=INSTRUCTIONS,
            website_url=WEBSITE,
            version=version,
        )
        self.specs = {spec.name: spec for spec in specs}
        for spec in specs:
            self.add_tool(
                spec.fn,
                name=spec.name,
                title=spec.title,
                description=spec.description,
                annotations=spec.annotations,
                structured_output=True,
            )
        self.resource(
            DOCS_URI,
            name="docs",
            title="CandleStack docs",
            description="Links to the docs for agents, the tool reference, the manifest and the "
            "backtest result references",
            mime_type="text/markdown",
        )(_docs)

    @override
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> CallToolResult | InputRequiredResult:
        spec = self.specs.get(name)
        if spec is None:
            return problem_result(_unknown_tool(name))
        try:
            if context is not None:
                tool_call(context).caller.require(*spec.scopes)
            return await super().call_tool(name, arguments, context)
        except ProblemError as problem:
            return problem_result(problem)
        except ToolError as error:
            return problem_result(self._problem(name, error))

    @staticmethod
    def _problem(name: str, error: ToolError) -> ProblemError:
        cause = error.__cause__
        if isinstance(cause, ProblemError):
            return cause
        # Arguments that do not fit; a model the tool itself fails to build is its crash.
        if isinstance(cause, ValidationError) and not isinstance(error, UnexpectedToolError):
            return _invalid_arguments(cause)
        logger.error("The tool %s failed", name, exc_info=cause)
        return _internal_error()


def _docs() -> str:
    return DOCS_TEXT


def build_server(version: str) -> CandleStackMCP:
    """The server with every tool; `version` is the app's, which clients see."""
    return CandleStackMCP(TOOLS, version=version)
