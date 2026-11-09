"""The schemas tools/list sends: shorter than Pydantic writes them, the arguments the same, and
all the tools within a budget, since a client sends them to the model with every turn."""

import asyncio
import json
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from candlestack.experiments import BacktestIn
from candlestack.mcp.schema import SHORTER, compact_input, compact_output
from candlestack.mcp.server import build_server

# What tools/list costs a model: the characters of every tool's name, description and input
# schema as compact JSON (about 4 to a token). Trimmed to 43 % fewer tokens in #459, with
# 10 % to grow. A new tool or a longer description that does not fit: say less, or move it to
# the tool's `details`, which only the tool reference shows; raise the budget only on purpose.
BUDGET_CHARS = 52_000


class Rule(BaseModel):
    """A rule."""

    name: str = Field(examples=["threshold"])
    title: str = Field(description="A field named title stays")


class Args(BaseModel):
    rule: Rule = Rule(name="threshold", title="t")
    limit: Annotated[float | None, Field(description="Optional")] = None
    either: int | str | None = None


def test_an_input_schema_leaves_out_what_tells_a_model_nothing() -> None:
    schema = compact_input(Args.model_json_schema())

    assert schema == {
        "$defs": {
            "Rule": {
                "description": "A rule.",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"description": "A field named title stays", "type": "string"},
                },
                "required": ["name", "title"],
                "type": "object",
            }
        },
        "properties": {
            # The object default repeats its fields' own.
            "rule": {"$ref": "#/$defs/Rule"},
            # Optional: left out means null.
            "limit": {"description": "Optional", "type": "number"},
            # More than one type besides null stays as it is.
            "either": {
                "anyOf": [{"type": "integer"}, {"type": "string"}, {"type": "null"}],
                "default": None,
            },
        },
        "type": "object",
    }


def test_a_shared_model_takes_the_shorter_description() -> None:
    used: set[str] = set()
    schema = compact_input(BacktestIn.model_json_schema(), used)

    defs = schema["$defs"]
    assert "description" not in defs["ModelRef"]
    assert defs["ModelRef"]["properties"]["id"]["description"] == SHORTER["ModelRef.id"]
    assert defs["Execution"]["description"] == SHORTER["Execution"]
    assert {"ModelRef", "ModelRef.id", "Execution", "Period.start"} <= used


def test_every_shorter_description_is_used() -> None:
    """A renamed model or field leaves its entry behind: it fails here."""
    server = build_server("schema")
    raw = asyncio.run(MCPServer.list_tools(server))
    used: set[str] = set()
    for tool in raw:
        compact_input(tool.input_schema, used)

    assert sorted(set(SHORTER) - used) == []


def test_an_output_schema_keeps_its_nulls() -> None:
    schema = compact_output(Args.model_json_schema())

    assert "title" not in schema
    assert "title" not in schema["$defs"]["Rule"]
    assert schema["$defs"]["Rule"]["properties"]["title"]["description"]
    assert {"type": "null"} in schema["properties"]["limit"]["anyOf"]


def test_tools_list_sends_the_shorter_schemas() -> None:
    server = build_server("schema")
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}
    text = json.dumps([tool.input_schema for tool in tools.values()])

    assert '"title"' not in text
    assert '"discriminator"' not in text
    assert "Fee Bps" not in text
    assert "title" not in json.dumps(tools["get_me"].output_schema)
    # Worked out once.
    assert asyncio.run(server.list_tools()) is asyncio.run(server.list_tools())


def test_the_tools_stay_within_their_budget() -> None:
    tools = asyncio.run(build_server("budget").list_tools())
    size = sum(
        len(
            json.dumps(
                {"name": t.name, "description": t.description, "inputSchema": t.input_schema},
                separators=(",", ":"),
            )
        )
        for t in tools
    )

    assert size <= BUDGET_CHARS, (
        f"tools/list costs {size} characters, over the budget of {BUDGET_CHARS}: shorten the "
        "descriptions or move what a model needs only now and then to `details`"
    )
