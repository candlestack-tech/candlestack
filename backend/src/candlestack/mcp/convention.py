"""The convention every tool follows, checked when it is declared (``ToolSpec``), and what a
tool's body gets about its call (``tool_call``).

A tool is an async function that takes its arguments, typed, and ``ctx: Context``, and returns
a Pydantic model, which becomes its output schema. Its docstring is the description a model
reads: what the tool does, when to use it and what it answers. ``ToolSpec`` adds a short
``verb_noun`` name, a title for people, the scopes an API key needs, the annotations (read-only,
destructive, idempotent, always all three), an example call for the tool reference, and what a
call costs of the rate limits (``cost``: 1, or ``HEAVY`` for one that builds or reruns much,
or what works it out from the arguments).

A tool raises ``ProblemError`` for what the agent should see, as a route does; the server turns
it and every other failure into a tool error whose text is the problem as JSON
(``problem_result``).
"""

import inspect
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any

from mcp.server.mcpserver import Context
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import State
from starlette.requests import Request

from candlestack.accounts import Caller, Scope, User
from candlestack.core import PROBLEM_TYPE_BASE, ProblemError, Settings
from candlestack.experiments import IDEMPOTENCY_KEY_MAX, IDEMPOTENCY_KEY_PATTERN, BacktestRunner
from candlestack.mcp.docs import TOOLS_DOCS

# A verb and what it acts on, lowercase: get_me, start_backtest, list_models.
_NAME = re.compile(r"[a-z]+(_[a-z0-9]+){1,3}")
_NAME_MAX = 32
# Where the HTTP layer leaves the caller of a request (``request.state``), for the tools.
CALLER_STATE = "mcp_caller"
# The longest wait of one call of a wait tool: clients give up on a call after a minute or so,
# and the transport answers JSON at the end of the call, so the agent calls again.
WAIT_MAX_S = 60
WAIT_DEFAULT_S = 50
# How often a wait reads the backtests' rows again when nothing told it of a change: the runner
# tells it when one starts, ends or is cancelled (``BacktestRunner.watch``).
POLL_S = 2.5

# The idempotency key of a tool that starts something (``experiments.idempotent``).
IdempotencyKey = Annotated[
    str | None,
    Field(
        max_length=IDEMPOTENCY_KEY_MAX,
        pattern=IDEMPOTENCY_KEY_PATTERN,
        description="A key you make, such as a UUID: the same call again with it within 24 "
        "hours answers the first one instead of starting again",
    ),
]


def reads() -> ToolAnnotations:
    """The annotations of a tool that only reads."""
    return ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )


def changes(*, destructive: bool, idempotent: bool) -> ToolAnnotations:
    """The annotations of a tool that changes data: `destructive` when it deletes or overwrites
    what the user had (or makes it public), `idempotent` when calling it again changes nothing
    more."""
    return ToolAnnotations(
        read_only_hint=False,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=False,
    )


@dataclass(frozen=True, slots=True)
class Example:
    """An example call of a tool and its answer, for the tool reference."""

    arguments: dict[str, Any]
    result: BaseModel


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool and what the convention asks of it; a tool that breaks it fails here, when the
    server is built."""

    fn: Callable[..., Awaitable[BaseModel]]
    name: str
    title: str
    scopes: tuple[Scope, ...]
    annotations: ToolAnnotations
    example: Example
    # What a call costs of the caller's rate limits (``accounts.charge_rate``), or what works
    # it out from the call's arguments (a short answer from stored data costs less).
    cost: int | Callable[[dict[str, Any]], int] = 1
    description: str = field(init=False)

    def __post_init__(self) -> None:
        if not _NAME.fullmatch(self.name) or len(self.name) > _NAME_MAX:
            raise ValueError(f"Tool name {self.name!r}: verb_noun, lowercase, {_NAME_MAX} at most")
        doc = inspect.getdoc(self.fn)
        if not doc:
            raise ValueError(f"Tool {self.name}: its docstring is the description a model reads")
        hints = self.annotations
        if None in (hints.read_only_hint, hints.destructive_hint, hints.idempotent_hint):
            raise ValueError(f"Tool {self.name}: annotations come from reads() or changes()")
        if not self.scopes:
            raise ValueError(f"Tool {self.name}: name the scopes an API key needs")
        if hints.read_only_hint != (self.scopes == (Scope.read,)):
            raise ValueError(
                f"Tool {self.name}: a tool that only reads needs the scope read alone, one that "
                "changes data the scope of what it changes"
            )
        # The docstring's lines are wrapped for the code; a model reads its paragraphs whole.
        paragraphs = [" ".join(part.split()) for part in doc.split("\n\n")]
        paragraphs.append(f"Docs: {TOOLS_DOCS}#{self.name}")
        object.__setattr__(self, "description", "\n\n".join(paragraphs))

    def cost_of(self, arguments: dict[str, Any]) -> int:
        """What the call with `arguments` costs of the caller's rate limits."""
        return self.cost if isinstance(self.cost, int) else self.cost(arguments)


@dataclass(frozen=True, slots=True)
class ToolCall:
    """What a tool's body knows about its call: who sent it (the API key's user and scopes)
    and the app's shared clients."""

    caller: Caller
    state: State

    @property
    def user(self) -> User:
        return self.caller.user

    @property
    def scopes(self) -> list[Scope]:
        """The API key's scopes, in the order of ``Scope``."""
        granted = self.caller.scopes
        return [scope for scope in Scope if granted is None or scope in granted]

    @property
    def settings(self) -> Settings:
        return self.state.settings

    @asynccontextmanager
    async def db(self) -> AsyncIterator[AsyncSession]:
        """A Postgres session for the call, as a route gets one per request."""
        async with self.state.db.sessions() as session:
            yield session


def runner_of(call: ToolCall) -> BacktestRunner:
    """The app's backtest runner, which tells a wait when a backtest changed."""
    return call.state.backtest_runner


def tool_call(ctx: Context) -> ToolCall:
    """The call `ctx` belongs to; the HTTP layer signed its request in before any tool runs."""
    request = ctx.request_context.request
    if not isinstance(request, Request) or not hasattr(request.state, CALLER_STATE):
        raise RuntimeError("A tool ran outside a request the MCP endpoint signed in")
    return ToolCall(getattr(request.state, CALLER_STATE), request.app.state)


def backtest_url(call: ToolCall, backtest_id: str) -> str:
    """The backtest's page in the app, which the agent gives the user to open."""
    return f"{call.settings.public_url}/backtests/{backtest_id}"


def problem_body(problem: ProblemError) -> dict[str, Any]:
    """The problem as the API sends it (RFC 9457)."""
    return {
        "type": PROBLEM_TYPE_BASE + problem.slug,
        "title": problem.title,
        "status": problem.status,
        "detail": problem.detail,
        **problem.extensions,
    }


def problem_result(problem: ProblemError) -> CallToolResult:
    """A tool error whose text is the problem as JSON, so a model reads the type and detail."""
    text = json.dumps(problem_body(problem), ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)
