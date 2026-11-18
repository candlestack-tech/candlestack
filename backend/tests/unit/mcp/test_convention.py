"""The convention every tool follows, checked when a tool is declared, and the problems a tool
answers."""

import json
from types import SimpleNamespace
from typing import Any

import pytest
from mcp.server.mcpserver import Context
from mcp.types import TextContent, ToolAnnotations
from pydantic import BaseModel

from candlestack.accounts import Scope
from candlestack.core import ProblemError
from candlestack.mcp.convention import (
    Example,
    Progress,
    ToolSpec,
    changes,
    problem_result,
    reads,
    run_progress,
)
from candlestack.mcp.server import TOOLS


class Out(BaseModel):
    ok: bool


async def documented(ctx: Context) -> Out:
    """Does a thing for the tests,
    wrapped over two lines.

    A second paragraph."""
    return Out(ok=ctx is not None)


async def undocumented(ctx: Context) -> Out:
    return Out(ok=ctx is not None)


def spec(**fields: Any) -> ToolSpec:
    given: dict[str, Any] = {
        "fn": documented,
        "name": "do_thing",
        "title": "Do a thing",
        "scopes": (Scope.read,),
        "annotations": reads(),
        "example": Example({}, Out(ok=True)),
        **fields,
    }
    return ToolSpec(**given)


def test_the_description_is_the_docstring_with_its_paragraphs_unwrapped() -> None:
    assert spec().description == (
        "Does a thing for the tests, wrapped over two lines.\n\nA second paragraph."
    )
    assert spec().details is None


def test_the_details_are_unwrapped_as_the_description() -> None:
    details = """
        What the reference adds,
        over two lines.

        And a second paragraph."""

    assert spec(details=details).details == (
        "What the reference adds, over two lines.\n\nAnd a second paragraph."
    )


@pytest.mark.parametrize("name", ["Get_me", "me", "get-me", "get_me_now_and_then_again", "a" * 33])
def test_a_name_is_a_short_verb_noun(name: str) -> None:
    with pytest.raises(ValueError, match="verb_noun"):
        spec(name=name)


def test_a_tool_needs_a_docstring() -> None:
    with pytest.raises(ValueError, match="docstring"):
        spec(fn=undocumented)


def test_the_three_hints_are_always_set() -> None:
    with pytest.raises(ValueError, match="annotations"):
        spec(annotations=ToolAnnotations(read_only_hint=True))


def test_a_tool_names_its_scopes() -> None:
    with pytest.raises(ValueError, match="scopes"):
        spec(scopes=())


@pytest.mark.parametrize(
    ("scopes", "annotations"),
    [
        ((Scope.backtests,), reads()),
        ((Scope.read, Scope.backtests), reads()),
        ((Scope.read,), changes(destructive=False, idempotent=True)),
    ],
)
def test_read_only_goes_with_the_scope_read_alone(
    scopes: tuple[Scope, ...], annotations: ToolAnnotations
) -> None:
    with pytest.raises(ValueError, match="scope read alone"):
        spec(scopes=scopes, annotations=annotations)


def test_a_change_names_its_own_scope() -> None:
    made = spec(scopes=(Scope.models,), annotations=changes(destructive=True, idempotent=True))

    assert made.annotations.destructive_hint is True
    assert made.annotations.read_only_hint is False


def test_a_problem_is_a_tool_error_with_the_api_s_body() -> None:
    result = problem_result(
        ProblemError(403, "missing-scope", "Missing scope", "No.", extensions={"scope": "read"})
    )

    assert result.is_error
    [content] = result.content
    assert isinstance(content, TextContent)
    assert json.loads(content.text) == {
        "type": "https://candlestack.tech/problems/missing-scope",
        "title": "Missing scope",
        "status": 403,
        "detail": "No.",
        "scope": "read",
    }


@pytest.mark.parametrize("tool", TOOLS, ids=lambda tool: tool.name)
def test_every_tool_s_example_fits_its_schemas(tool: ToolSpec) -> None:
    """The example of the tool reference is a real call: its answer is the tool's output model."""
    output = tool.fn.__annotations__["return"]
    assert isinstance(tool.example.result, output)
    assert output.model_validate(tool.example.result.model_dump(mode="json"))


class FakeContext:
    """Stands in for the tool's ``Context``: records each progress it reports."""

    def __init__(self) -> None:
        self.sent: list[tuple[float, float | None, str | None]] = []

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        self.sent.append((progress, total, message))


@pytest.mark.anyio
async def test_progress_sends_only_a_value_higher_than_the_last() -> None:
    ctx = FakeContext()
    progress = Progress(ctx)  # ty: ignore[invalid-argument-type]

    for value, message in [(0, "a"), (0, "b"), (2, "c"), (1, "d"), (2, "e"), (2.5, "f")]:
        await progress.report(value, 10, message)

    # The first is sent whatever it is; a value that is not higher is left out, its message too.
    assert ctx.sent == [(0, 10, "a"), (2, 10, "c"), (2.5, 10, "f")]


def step(step_id: str, state: str, loaded: int | None = None, total: int | None = None):
    return SimpleNamespace(id=step_id, state=state, loaded=loaded, total=total)


@pytest.mark.parametrize(
    ("states", "candles", "expected"),
    [
        (("pending", "pending", "pending", "pending"), {}, 0.2),
        (("running", "pending", "pending", "pending"), {}, 0.2),
        (("running", "pending", "pending", "pending"), {"loaded": 1, "total": 4}, 0.25),
        (("running", "pending", "pending", "pending"), {"loaded": 9, "total": 4}, 0.4),
        (("done", "done", "running", "pending"), {}, 0.6),
        (("done", "done", "done", "running"), {}, 0.8),
    ],
)
def test_a_running_backtest_s_progress_follows_its_steps(
    states: tuple[str, ...], candles: dict[str, int], expected: float
) -> None:
    ids = ["candles", "queue", "model", "trades"]
    steps = [step(step_id, state) for step_id, state in zip(ids, states, strict=True)]
    steps[0] = step("candles", states[0], **candles)

    assert run_progress(steps) == pytest.approx(expected)
