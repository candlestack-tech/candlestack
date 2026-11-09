"""The MCP server: its instructions, the ``docs`` resource, the prompts and the tools, on the
official SDK.

``CandleStackMCP`` checks the API key's scopes before a tool runs, charges the rate limits what
a heavy tool costs beyond the request's 1 (``ToolSpec.cost``), and answers every failure as
a tool error with the API's problem (``convention.problem_result``): the ``ProblemError`` a tool
raised, ``validation`` for arguments that do not fit the input schema, ``not-found`` for an
unknown tool and ``internal-error`` for a crash, which is logged for a developer.

A result's text is its structured content as compact JSON without the null fields of its
models (``compact_text``): what a model reads costs tokens, and the SDK writes it indented. A
null in a list or as the value of a map stays. The structured content itself stays whole, nulls
included, so it fits the output schema.

tools/list sends the schemas shorter than Pydantic writes them (``schema``): a client sends the
tools to the model with every turn.
"""

import logging
from typing import Any, get_type_hints, override

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
from mcp.server.mcpserver.prompts import Prompt
from mcp.types import CallToolResult, Icon, InputRequiredResult, TextContent, Tool
from pydantic import BaseModel, ValidationError

from candlestack.accounts import charge_rate
from candlestack.core import ProblemError
from candlestack.mcp import account, deletes, library, manage, market, models, run, sweeps
from candlestack.mcp.apps import add_widget_versions, attached, widget_extension
from candlestack.mcp.convention import ToolSpec, problem_result, tool_call
from candlestack.mcp.docs import DOCS_TEXT, DOCS_URI, INSTRUCTIONS
from candlestack.mcp.prompts import PROMPTS
from candlestack.mcp.schema import compact_input, compact_output

logger = logging.getLogger(__name__)

NAME = "candlestack"
TITLE = "CandleStack"
WEBSITE = "https://candlestack.tech"
# The logo clients show next to the server's name: the website's, public on every environment
# (stage's own files sit behind Cloudflare Access), as SVG and as a PNG for clients without SVG.
ICONS = [
    Icon(src=f"{WEBSITE}/favicon.svg", mime_type="image/svg+xml", sizes=["any"]),
    Icon(src=f"{WEBSITE}/apple-touch-icon.png", mime_type="image/png", sizes=["180x180"]),
]
# Every tool of the server, in the order tools/list and the tool reference show them.
TOOLS: tuple[ToolSpec, ...] = (
    *account.TOOLS,
    *library.TOOLS,
    *market.TOOLS,
    *models.TOOLS,
    *run.TOOLS,
    *manage.TOOLS,
    *deletes.TOOLS,
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


def compact_text(output: type[BaseModel], structured: dict[str, Any]) -> str:
    """The text of a tool result whose output model is `output`: its structured content as
    compact JSON without the fields of its models that are null (a model reads a missing field
    as null). A null in a list or as a map's value stays: its place there means something."""
    return output.model_validate(structured).model_dump_json(by_alias=True, exclude_none=True)


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
            icons=ICONS,
            version=version,
            extensions=[widget_extension()],
        )
        self.specs = {spec.name: spec for spec in specs}
        # Each tool's output model, the return type of its function.
        self.outputs: dict[str, type[BaseModel]] = {
            spec.name: get_type_hints(spec.fn)["return"] for spec in specs
        }
        for spec in specs:
            self.add_tool(
                spec.fn,
                name=spec.name,
                title=spec.title,
                description=spec.description,
                annotations=spec.annotations,
                structured_output=True,
                meta=spec.meta,
            )
        self.resource(
            DOCS_URI,
            name="docs",
            title="CandleStack docs",
            description="Links to the docs for agents, the tool reference, the manifest and the "
            "backtest result references",
            mime_type="text/markdown",
        )(_docs)
        add_widget_versions(self)
        for prompt in PROMPTS:
            self.add_prompt(Prompt.from_function(prompt.fn, name=prompt.name, title=prompt.title))
        self._listed: list[Tool] | None = None

    @override
    async def list_tools(self) -> list[Tool]:
        """The tools with their schemas shortened (``schema``), worked out once."""
        if self._listed is None:
            self._listed = [
                tool.model_copy(
                    update={
                        "input_schema": compact_input(tool.input_schema),
                        "output_schema": compact_output(tool.output_schema),
                    }
                )
                for tool in await super().list_tools()
            ]
        return self._listed

    @override
    async def call_tool(
        self, name: str, arguments: dict[str, Any], context: Context | None = None
    ) -> CallToolResult | InputRequiredResult:
        spec = self.specs.get(name)
        if spec is None:
            return problem_result(_unknown_tool(name))
        try:
            if context is not None:
                call = tool_call(context)
                call.caller.require(*spec.scopes)
                await charge_rate(
                    call.state.rate_limiter, call.caller.budget, spec.cost_of(arguments)
                )
            result = await super().call_tool(name, arguments, context)
        except ProblemError as problem:
            return problem_result(problem)
        except ToolError as error:
            return problem_result(self._problem(name, error))
        if not isinstance(result, CallToolResult) or result.structured_content is None:
            return result
        text = compact_text(self.outputs[name], result.structured_content)
        update: dict[str, Any] = {"content": [TextContent(type="text", text=text)]}
        # The series of the widget's charts, which only the widget reads (``apps``).
        meta = None if context is None else attached(context)
        if meta is not None:
            update["meta"] = {**(result.meta or {}), **meta}
        return result.model_copy(update=update)

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
